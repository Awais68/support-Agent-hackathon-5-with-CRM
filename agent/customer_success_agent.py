"""Customer Success Agent orchestrator for TechFlow CRM Digital FTE."""

import json
from dataclasses import dataclass
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime

import asyncpg
import structlog
from openai import AsyncOpenAI

from agent.prompts import SYSTEM_PROMPT, CHANNEL_ADDENDUMS, CLASSIFICATION_PROMPT
from agent.tools import OPENAI_TOOL_SCHEMAS, ToolContext, execute_tool
from agent.formatters import format_email_response, format_whatsapp_response, format_web_form_response
from agent.pre_processing_gate import run_gate, GateAction
from agent.sentiment_analyzer import detect_sentiment_drop
from kafka_client import KafkaProducerClient, create_agent_processing_message
from database import queries as db
from metrics import (
    sentiment_score as metric_sentiment_score,
    sentiment_emotion,
    sentiment_urgency as metric_sentiment_urgency,
    sentiment_below_threshold,
    sentiment_high_urgency,
)

logger = structlog.get_logger(__name__)


@dataclass
class AgentContext:
    """Context for agent execution."""

    db_pool: asyncpg.Pool
    kafka_producer: KafkaProducerClient
    openai_client: AsyncOpenAI
    logger: Any = None

    def __post_init__(self):
        if self.logger is None:
            self.logger = logger


class CustomerSuccessAgent:
    """Main agent orchestrator for customer support."""

    def __init__(self, context: AgentContext, model: str = "gpt-4o"):
        self.context = context
        self.model = model

    async def classify_message(self, message: str) -> str:
        """Classify customer message into category."""
        try:
            response = await self.context.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": CLASSIFICATION_PROMPT},
                    {"role": "user", "content": message},
                ],
                max_tokens=50,
                temperature=0.3,
            )

            classification = response.choices[0].message.content.strip()
            return classification

        except Exception as e:
            self.context.logger.error("Classification failed", error=str(e))
            return "General Inquiry"

    async def process_customer_message(
        self,
        ticket_id: UUID,
        customer_id: UUID,
        customer_email: str,
        customer_name: str,
        message: str,
        channel: str = "email",
    ) -> Dict[str, Any]:
        """Process a customer message with agent orchestration."""

        try:
            # Step 1: Classify message
            classification = await self.classify_message(message)
            self.context.logger.info(
                "Message classified",
                ticket_id=str(ticket_id),
                classification=classification,
            )

            # Step 2: Get system prompt for channel
            system_prompt = SYSTEM_PROMPT + "\n\n" + CHANNEL_ADDENDUMS.get(channel, "")

            # Step 3: Create agent processing event
            await self.context.kafka_producer.send_message(
                "agent.processing",
                {
                    "ticket_id": str(ticket_id),
                    "customer_id": str(customer_id),
                    "message": message,
                    "channel": channel,
                    "classification": classification,
                    "timestamp": datetime.utcnow().isoformat(),
                },
                key=str(ticket_id),
            )

            # Step 4: Record agent run in database
            agent_run = await db.create_agent_run(
                self.context.db_pool,
                ticket_id=ticket_id,
                customer_id=customer_id,
                input_message=message,
                status="running",
            )

            agent_run_id = agent_run["id"]

            # Step 5: Run pre-processing gate (hard rule enforcement)
            gate_result = await run_gate(
                self.context.openai_client,
                message,
                customer_name=customer_name,
            )
            self.context.logger.info(
                "Gate check completed",
                action=gate_result.action.value,
                reason=gate_result.reason,
                sentiment=gate_result.sentiment_score,
                emotion=gate_result.emotion,
                urgency=gate_result.urgency_score,
            )

            tool_calls = []
            token_usage = 0
            was_escalated = False

            # Persist sentiment score + emotion + urgency + aspects to messages table
            if gate_result.sentiment_score is not None:
                try:
                    await db.update_message_sentiment(
                        self.context.db_pool,
                        ticket_id,
                        gate_result.sentiment_score,
                    )
                except Exception as e:
                    self.context.logger.warning(
                        "Failed to persist sentiment score to messages",
                        error=str(e),
                    )

            # Step 5b: Sentiment trend detection across conversation turns
            sentiment_drop_detected = False
            sentiment_drop_amount = 0.0
            try:
                history = await db.get_message_sentiment_history(
                    self.context.db_pool, ticket_id, limit=4
                )
                if history:
                    drop_detected, drop_amount, drop_desc = detect_sentiment_drop(history)
                    if drop_detected:
                        self.context.logger.warning(
                            "Sentiment drop detected across turns",
                            drop_amount=drop_amount,
                            description=drop_desc,
                        )
                        sentiment_drop_detected = drop_detected
                        sentiment_drop_amount = drop_amount
            except Exception as e:
                self.context.logger.warning(
                    "Sentiment trend detection failed",
                    error=str(e),
                )

            # Step 5c: Record Prometheus metrics
            try:
                metric_sentiment_score.labels(channel=channel, tier="all").set(
                    gate_result.sentiment_score or 0.5
                )
                sentiment_emotion.labels(
                    emotion=gate_result.emotion, channel=channel
                ).inc()
                metric_sentiment_urgency.labels(channel=channel).set(
                    gate_result.urgency_score
                )
                if gate_result.sentiment_score is not None and gate_result.sentiment_score < 0.3:
                    sentiment_below_threshold.labels(channel=channel).inc()
                if gate_result.is_urgent:
                    sentiment_high_urgency.labels(channel=channel).inc()
            except Exception as e:
                self.context.logger.warning("Failed to record sentiment metrics", error=str(e))

            if gate_result.action == GateAction.ESCALATE:
                self.context.logger.info(
                    "Gate triggered escalation",
                    reason=gate_result.reason,
                    priority=gate_result.priority,
                )
                aspects_str = ", ".join(
                    f"{k}={v:.2f}" for k, v in gate_result.aspect_scores.items() if v != 0.5
                ) if gate_result.aspect_scores else "none"
                await self.context.kafka_producer.send_message(
                    "escalations",
                    {
                        "ticket_id": str(ticket_id),
                        "customer_id": str(customer_id),
                        "reason": gate_result.reason,
                        "priority": gate_result.priority,
                        "context_summary": (
                            f"Customer message: {message[:500]}\n"
                            f"Sentiment: {gate_result.sentiment_score}\n"
                            f"Emotion: {gate_result.emotion}\n"
                            f"Urgency: {gate_result.urgency_score:.2f}\n"
                            f"Aspects: {aspects_str}\n"
                            f"Sentiment drop: {sentiment_drop_amount:.2f}" if sentiment_drop_detected else ""
                        ),
                        "source": "pre_processing_gate",
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                    key=str(ticket_id),
                )
                await db.update_ticket_status(
                    self.context.db_pool,
                    ticket_id,
                    "escalated",
                )
                output_message = (
                    "I understand your concern. This requires specialized assistance, "
                    "so I've escalated your request to our dedicated support team. "
                    "A human agent will follow up with you shortly."
                )
                was_escalated = True
            elif gate_result.action == GateAction.DEFLECT:
                self.context.logger.info(
                    "Gate triggered deflection",
                    reason=gate_result.reason,
                )
                output_message = (
                    "I'm a support assistant focused on helping with TechFlow Analytics. "
                    "Let's focus on your question — how can I help you with our product today?"
                )
            else:
                # Gate says ALLOW — proceed with normal agentic loop
                self.context.logger.info(
                    "Starting agentic loop",
                    ticket_id=str(ticket_id),
                    model=self.model,
                )

                tool_context = ToolContext(
                    db_pool=self.context.db_pool,
                    kafka_producer=self.context.kafka_producer,
                    openai_client=self.context.openai_client,
                )

                messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"{customer_name} ({customer_email}) on {channel}: {message}",
                    },
                ]

                output_message = ""
                MAX_TURNS = 10

                for turn in range(MAX_TURNS):
                    response = await self.context.openai_client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=OPENAI_TOOL_SCHEMAS,
                        tool_choice="auto",
                        temperature=0.7,
                        max_tokens=2000,
                    )

                    if response.usage:
                        token_usage = response.usage.total_tokens

                    choice = response.choices[0]
                    messages.append(choice.message.model_dump(exclude_none=True))

                    if not choice.message.tool_calls:
                        output_message = choice.message.content or ""
                        self.context.logger.info(
                            "Agent completed without tool calls",
                            ticket_id=str(ticket_id),
                            turn=turn,
                        )
                        break

                    for tc in choice.message.tool_calls:
                        tool_name = tc.function.name
                        tool_args = json.loads(tc.function.arguments)
                        tool_calls.append(tool_name)
                        self.context.logger.info(
                            "Executing tool",
                            tool_name=tool_name,
                            ticket_id=str(ticket_id),
                            turn=turn,
                        )
                        tool_result = await execute_tool(tool_name, tool_args, tool_context)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": tool_result,
                        })
                        self.context.logger.info(
                            "Tool executed",
                            tool_name=tool_name,
                            ticket_id=str(ticket_id),
                        )
                else:
                    output_message = (
                        "I've been working on your request. A support agent will follow up shortly."
                    )
                    self.context.logger.warning(
                        "Max turns reached in agentic loop",
                        ticket_id=str(ticket_id),
                        max_turns=MAX_TURNS,
                    )

            # Step 7: Format response for channel
            if channel == "email":
                formatted_response = format_email_response(
                    output_message,
                    customer_name=customer_name,
                    ticket_number=str(ticket_id),
                )
            elif channel == "whatsapp":
                formatted_response = format_whatsapp_response(output_message)
            elif channel == "webform":
                formatted_response = format_web_form_response(
                    output_message,
                    ticket_number=str(ticket_id),
                    tracking_url=f"https://support.techflow.com/track/{ticket_id}",
                )
            else:
                formatted_response = output_message

            # Step 8: Update agent run with completion
            await db.complete_agent_run(
                self.context.db_pool,
                agent_run_id=agent_run_id,
                output_message=formatted_response,
                tokens_used=token_usage,
                result={
                    "classification": classification,
                    "tool_calls": tool_calls,
                    "escalated": was_escalated or "escalate" in output_message.lower(),
                    "sentiment_score": gate_result.sentiment_score,
                    "emotion": gate_result.emotion,
                    "urgency_score": gate_result.urgency_score,
                    "is_urgent": gate_result.is_urgent,
                    "aspect_scores": gate_result.aspect_scores,
                    "sentiment_drop_detected": sentiment_drop_detected,
                    "sentiment_drop_amount": sentiment_drop_amount,
                },
            )

            # Step 9: Send response via Kafka
            await self.context.kafka_producer.send_message(
                f"notifications.outbound",
                {
                    "ticket_id": str(ticket_id),
                    "customer_email": customer_email,
                    "channel": channel,
                    "message": formatted_response,
                    "agent_run_id": str(agent_run_id),
                    "timestamp": datetime.utcnow().isoformat(),
                },
                key=str(ticket_id),
            )

            # Step 10: Create agent completed event
            await self.context.kafka_producer.send_message(
                "agent.completed",
                {
                    "ticket_id": str(ticket_id),
                    "customer_id": str(customer_id),
                    "agent_run_id": str(agent_run_id),
                    "classification": classification,
                    "tools_used": tool_calls,
                    "response_length": len(formatted_response),
                    "tokens_used": token_usage,
                    "gate_action": gate_result.action.value,
                    "sentiment_score": gate_result.sentiment_score,
                    "emotion": gate_result.emotion,
                    "urgency_score": gate_result.urgency_score,
                    "is_urgent": gate_result.is_urgent,
                    "aspect_scores": gate_result.aspect_scores,
                    "sentiment_drop_detected": sentiment_drop_detected,
                    "sentiment_drop_amount": sentiment_drop_amount,
                    "timestamp": datetime.utcnow().isoformat(),
                },
                key=str(ticket_id),
            )

            self.context.logger.info(
                "Message processed successfully",
                ticket_id=str(ticket_id),
                agent_run_id=str(agent_run_id),
                response_length=len(formatted_response),
            )

            return {
                "status": "success",
                "ticket_id": str(ticket_id),
                "agent_run_id": str(agent_run_id),
                "response": formatted_response,
                "classification": classification,
                "tool_calls": tool_calls,
                "tokens_used": token_usage,
                "gate_action": gate_result.action.value,
                "sentiment_score": gate_result.sentiment_score,
                "emotion": gate_result.emotion,
                "urgency_score": gate_result.urgency_score,
                "aspect_scores": gate_result.aspect_scores,
                "sentiment_drop_detected": sentiment_drop_detected,
                "sentiment_drop_amount": sentiment_drop_amount,
            }

        except Exception as e:
            self.context.logger.error(
                "Message processing failed",
                error=str(e),
                ticket_id=str(ticket_id),
            )

            # Try to mark agent run as failed
            try:
                await db.complete_agent_run(
                    self.context.db_pool,
                    agent_run_id=agent_run_id if "agent_run_id" in locals() else None,
                    output_message=f"Error processing message: {str(e)}",
                    result={"status": "failed", "error": str(e)},
                )
            except:
                pass

            # Send error event to Kafka
            try:
                await self.context.kafka_producer.send_message(
                    "dlq",
                    {
                        "original_topic": "agent.processing",
                        "ticket_id": str(ticket_id),
                        "error": str(e),
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                    key=str(ticket_id),
                )
            except:
                pass

            raise


async def build_agent(context: AgentContext, model: str = "gpt-4o") -> CustomerSuccessAgent:
    """Factory function to build and initialize the customer success agent."""
    agent = CustomerSuccessAgent(context, model=model)
    context.logger.info("Agent initialized", model=model)
    return agent


# Example usage
async def example_usage():
    """Example of how to use the agent."""
    # Initialize context (in real app, done in FastAPI lifespan)
    pool = await asyncpg.create_pool("postgresql://***REDACTED_USER***:***REDACTED_PASS***@localhost/techflow")
    kafka_producer = KafkaProducerClient("localhost:9092")
    await kafka_producer.start()
    client = AsyncOpenAI()

    context = AgentContext(
        db_pool=pool,
        kafka_producer=kafka_producer,
        openai_client=client,
    )

    # Create agent
    agent = await build_agent(context)

    # Process a message
    result = await agent.process_customer_message(
        ticket_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        customer_id=UUID("550e8400-e29b-41d4-a716-446655440001"),
        customer_email="customer@example.com",
        customer_name="John Doe",
        message="How do I set up a data connector?",
        channel="email",
    )

    print(f"Result: {result}")

    # Cleanup
    await kafka_producer.stop()
    await pool.close()
