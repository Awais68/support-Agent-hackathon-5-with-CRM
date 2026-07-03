"""Tool executor for customer success agent - executes tools called by LLM."""

import json
import os
from typing import Any, Dict, Optional
from uuid import UUID

import asyncpg
import structlog
from openai import AsyncOpenAI
from openai import APIError as OpenAIAPIError, APITimeoutError, APIConnectionError
from exceptions import sanitize_error_message
from utils.circuit_breaker import get_circuit_breaker, CircuitBreakerError

from database import queries as db
from kafka_client import KafkaProducerClient

logger = structlog.get_logger(__name__)


class ToolExecutor:
    """Executes tools called by the OpenAI agent."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        kafka_producer: KafkaProducerClient,
        openai_client: AsyncOpenAI,
    ):
        self.db_pool = db_pool
        self.kafka_producer = kafka_producer
        self.openai_client = openai_client

    async def search_knowledge_base(
        self, query: str, category: Optional[str] = None, max_results: int = 5
    ) -> Dict[str, Any]:
        """Search knowledge base by semantic similarity."""
        try:
            embedding_model = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
            _cb = get_circuit_breaker("openai")
            try:
                async with _cb:
                    embedding_response = await self.openai_client.embeddings.create(
                        input=query,
                        model=embedding_model,
                    )
            except CircuitBreakerError:
                logger.error("KB search failed (OpenAI circuit open)", query=query)
                return {"error": "AI service temporarily unavailable", "query": query, "results": []}
            query_embedding = embedding_response.data[0].embedding

            # Search using pgvector similarity
            results = await db.search_knowledge_base(
                self.db_pool,
                embedding=query_embedding,
                category=category,
                max_results=max_results,
            )

            logger.info(
                "KB search completed",
                query=query,
                results_count=len(results),
                category=category,
            )

            return {
                "query": query,
                "results": [
                    {
                        "id": str(r["id"]),
                        "title": r["title"],
                        "content": r["content"],
                        "category": r["category"],
                        "tags": r.get("tags", []),
                        "similarity_score": float(r.get("similarity", 0)),
                    }
                    for r in results
                ],
                "count": len(results),
            }

        except (OpenAIAPIError, APITimeoutError, APIConnectionError) as e:
            logger.error("KB search failed (OpenAI)", error=sanitize_error_message(str(e)), query=query)
            return {"error": sanitize_error_message(str(e)), "query": query, "results": []}
        except (asyncpg.PostgresError, ConnectionError) as e:
            logger.error("KB search failed (DB)", error=sanitize_error_message(str(e)), query=query)
            return {"error": sanitize_error_message(str(e)), "query": query, "results": []}
        except Exception as e:
            logger.error("KB search failed", error=sanitize_error_message(str(e)), query=query)
            return {"error": sanitize_error_message(str(e)), "query": query, "results": []}

    async def create_ticket(
        self,
        customer_email: str,
        subject: str,
        priority: str,
        category: str,
        customer_name: str,
    ) -> Dict[str, Any]:
        """Create a support ticket."""
        try:
            ticket = await db.create_ticket(
                self.db_pool,
                customer_email=customer_email,
                subject=subject,
                priority=priority,
                category=category,
                channel="agent",
                initial_message=f"Auto-created by agent for {customer_name}",
            )

            logger.info(
                "Ticket created by agent",
                ticket_number=ticket["ticket_number"],
                priority=priority,
            )

            return {
                "ticket_number": ticket["ticket_number"],
                "ticket_id": str(ticket["id"]),
                "status": ticket["status"],
                "created_at": ticket["created_at"].isoformat(),
                "priority": ticket["priority"],
            }

        except (asyncpg.PostgresError, ConnectionError) as e:
            logger.error("Failed to create ticket (DB)", error=sanitize_error_message(str(e)), customer_email=customer_email)
            return {"error": sanitize_error_message(str(e))}
        except Exception as e:
            logger.error("Failed to create ticket", error=sanitize_error_message(str(e)), customer_email=customer_email)
            return {"error": sanitize_error_message(str(e))}

    async def get_customer_history(
        self, customer_email: str, limit: int = 10, include_resolved: bool = True
    ) -> Dict[str, Any]:
        """Get customer's ticket history (cross-channel via identifier table)."""
        try:
            history = await db.get_customer_history(
                self.db_pool,
                email=customer_email,
                limit=limit,
                include_resolved=include_resolved,
            )

            logger.info(
                "Customer history retrieved",
                customer_email=customer_email,
                ticket_count=len(history),
            )

            return {
                "customer_email": customer_email,
                "tickets": [
                    {
                        "ticket_number": h["ticket_number"],
                        "subject": h["subject"],
                        "status": h["status"],
                        "priority": h["priority"],
                        "created_at": h["created_at"].isoformat(),
                    }
                    for h in history
                ],
                "count": len(history),
            }

        except (asyncpg.PostgresError, ConnectionError) as e:
            logger.error("Failed to get customer history (DB)", error=sanitize_error_message(str(e)), customer_email=customer_email)
            return {"error": sanitize_error_message(str(e)), "count": 0, "tickets": []}
        except Exception as e:
            logger.error("Failed to get customer history", error=sanitize_error_message(str(e)), customer_email=customer_email)
            return {"error": sanitize_error_message(str(e)), "count": 0, "tickets": []}

    async def escalate_to_human(
        self, ticket_id: UUID, reason: str, priority_level: str = "high"
    ) -> Dict[str, Any]:
        """Escalate ticket to human agent."""
        try:
            # Update ticket status to escalated
            ticket = await db.update_ticket_status(self.db_pool, ticket_id, "escalated")

            # Send to human queue via Kafka
            await self.kafka_producer.send_message(
                "escalations.human_queue",
                {
                    "ticket_id": str(ticket_id),
                    "reason": reason,
                    "priority": priority_level,
                    "timestamp": str(ticket["updated_at"]),
                },
                key=str(ticket_id),
            )

            logger.info(
                "Ticket escalated to human",
                ticket_id=str(ticket_id),
                reason=reason,
                priority=priority_level,
            )

            return {
                "status": "escalated",
                "ticket_number": ticket["ticket_number"],
                "escalation_reason": reason,
                "priority": priority_level,
                "eta_human_response": "15 minutes",
            }

        except (asyncpg.PostgresError, ConnectionError) as e:
            logger.error("Failed to escalate ticket (DB/Kafka)", error=sanitize_error_message(str(e)), ticket_id=str(ticket_id))
            return {"error": sanitize_error_message(str(e)), "status": "escalation_failed"}
        except Exception as e:
            logger.error("Failed to escalate ticket", error=sanitize_error_message(str(e)), ticket_id=str(ticket_id))
            return {"error": sanitize_error_message(str(e)), "status": "escalation_failed"}

    async def send_response(
        self, ticket_id: UUID, message: str, channel: str
    ) -> Dict[str, Any]:
        """Send response to customer via specified channel."""
        try:
            # Get ticket info
            ticket = await db.get_ticket(self.db_pool, ticket_id)
            if not ticket:
                return {"error": "Ticket not found"}

            # Add message to conversation
            msg = await db.add_message(
                self.db_pool,
                ticket_id=ticket_id,
                customer_id=UUID(ticket["customer_id"]),
                direction="outbound",
                content=message,
                channel=channel,
            )

            # Send via Kafka notification queue
            await self.kafka_producer.send_message(
                f"notifications.{channel}",
                {
                    "ticket_id": str(ticket_id),
                    "customer_email": ticket["customer_email"],
                    "channel": channel,
                    "message": message,
                    "message_id": str(msg["id"]),
                },
                key=str(ticket_id),
            )

            logger.info(
                "Response sent",
                ticket_id=str(ticket_id),
                channel=channel,
                message_length=len(message),
            )

            return {
                "status": "sent",
                "message_id": str(msg["id"]),
                "channel": channel,
                "sent_at": msg["created_at"].isoformat(),
            }

        except (asyncpg.PostgresError, ConnectionError) as e:
            logger.error("Failed to send response (DB/Kafka)", error=sanitize_error_message(str(e)), ticket_id=str(ticket_id))
            return {"error": sanitize_error_message(str(e)), "status": "send_failed"}
        except Exception as e:
            logger.error("Failed to send response", error=sanitize_error_message(str(e)), ticket_id=str(ticket_id))
            return {"error": sanitize_error_message(str(e)), "status": "send_failed"}

    async def execute_tool(
        self, tool_name: str, tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a tool based on name and input."""
        logger.info("Executing tool", tool_name=tool_name, input_keys=list(tool_input.keys()))

        try:
            if tool_name == "search_knowledge_base":
                return await self.search_knowledge_base(
                    query=tool_input.get("query", ""),
                    category=tool_input.get("category"),
                    limit=tool_input.get("limit", 5),
                )

            elif tool_name == "create_ticket":
                return await self.create_ticket(
                    customer_email=tool_input.get("customer_email", ""),
                    subject=tool_input.get("subject", ""),
                    priority=tool_input.get("priority", "medium"),
                    category=tool_input.get("category", "general"),
                    customer_name=tool_input.get("customer_name", "Customer"),
                )

            elif tool_name == "get_customer_history":
                return await self.get_customer_history(
                    customer_email=tool_input.get("customer_email", ""),
                    limit=tool_input.get("limit", 10),
                    include_resolved=tool_input.get("include_resolved", True),
                )

            elif tool_name == "escalate_to_human":
                return await self.escalate_to_human(
                    ticket_id=UUID(tool_input.get("ticket_id", "")),
                    reason=tool_input.get("reason", "Complex issue"),
                    priority_level=tool_input.get("priority_level", "high"),
                )

            elif tool_name == "send_response":
                return await self.send_response(
                    ticket_id=UUID(tool_input.get("ticket_id", "")),
                    message=tool_input.get("message", ""),
                    channel=tool_input.get("channel", "email"),
                )

            else:
                logger.warning("Unknown tool", tool_name=tool_name)
                return {"error": f"Unknown tool: {tool_name}"}

        except json.JSONDecodeError as e:
            logger.error("Invalid tool input JSON", error=sanitize_error_message(str(e)), tool_name=tool_name)
            return {"error": f"Invalid JSON: {str(e)}"}
        except Exception as e:
            logger.error("Tool execution failed", tool_name=tool_name, error=sanitize_error_message(str(e)))
            return {"error": f"Tool execution failed: {sanitize_error_message(str(e))}"}
