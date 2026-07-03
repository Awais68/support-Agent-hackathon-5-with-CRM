"""Agent tools for TechFlow CRM Digital FTE using OpenAI SDK with real DB/Kafka integration."""

import json
import os
from dataclasses import dataclass
from uuid import UUID
from datetime import UTC, datetime

import asyncpg
import structlog
from openai import AsyncOpenAI
from exceptions import sanitize_error_message
from utils.circuit_breaker import get_circuit_breaker, CircuitBreakerError

from database import queries as db
from kafka_client import (
    KafkaProducerClient,
    create_escalation_message,
)

logger = structlog.get_logger(__name__)


# Tool context passed to all tool functions
@dataclass
class ToolContext:
    """Context for executing agent tools with access to DB, Kafka, and OpenAI."""

    db_pool: asyncpg.Pool
    kafka_producer: KafkaProducerClient
    openai_client: AsyncOpenAI


# OpenAI tool schemas (proper format for chat.completions.create)
OPENAI_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search the knowledge base for relevant articles by topic. Useful when customer has questions about features, setup, or troubleshooting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query or topic to find in knowledge base (e.g., 'how to set up connectors', 'billing questions')",
                    },
                    "customer_tier": {
                        "type": "string",
                        "enum": ["starter", "growth", "enterprise"],
                        "description": "Customer tier: 'starter', 'growth', or 'enterprise'",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter: 'technical', 'billing', 'onboarding', 'general'",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 5)",
                    },
                },
                "required": ["query", "customer_tier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": "Create a new support ticket for tracking customer issues. Use this when a customer problem cannot be resolved immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_email": {
                        "type": "string",
                        "description": "Customer email address",
                    },
                    "channel": {
                        "type": "string",
                        "enum": ["email", "whatsapp", "webform"],
                        "description": "Channel through which customer contacted",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Brief subject line for the ticket",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["technical", "billing", "onboarding", "general"],
                        "description": "Ticket category",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                        "description": "Priority level",
                    },
                    "initial_message": {
                        "type": "string",
                        "description": "The customer's initial message or problem description",
                    },
                },
                "required": ["customer_email", "channel", "subject", "initial_message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_history",
            "description": "Retrieve customer's ticket history and previous interactions. Use this to understand customer context and identify patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_email": {
                        "type": "string",
                        "description": "Customer email address",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of tickets to return (default: 10)",
                    },
                    "include_resolved": {
                        "type": "boolean",
                        "description": "Include resolved tickets in history (default: true)",
                    },
                },
                "required": ["customer_email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Escalate a ticket to the human support team. Use this when the issue is complex, requires human judgment, or the customer is frustrated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket ID to escalate (UUID format)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for escalation (e.g., 'Complex technical issue', 'Customer unhappy')",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                        "description": "Escalation priority",
                    },
                    "context_summary": {
                        "type": "string",
                        "description": "Summary of the issue for the human agent",
                    },
                },
                "required": ["ticket_id", "reason", "context_summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_response",
            "description": "Send a response to the customer via their preferred channel. Use this to provide information, solutions, or updates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket ID to respond to (UUID format)",
                    },
                    "customer_email": {
                        "type": "string",
                        "description": "Customer email address",
                    },
                    "channel": {
                        "type": "string",
                        "enum": ["email", "whatsapp", "webform"],
                        "description": "Response channel",
                    },
                    "response_body": {
                        "type": "string",
                        "description": "The response message to send",
                    },
                    "response_type": {
                        "type": "string",
                        "enum": ["informational", "solution", "escalation", "ticket_created"],
                        "description": "Type of response",
                    },
                },
                "required": ["ticket_id", "customer_email", "channel", "response_body"],
            },
        },
    },
]


# Tool implementations
async def search_knowledge_base(args: dict, context: ToolContext) -> dict:
    """Search the knowledge base for relevant articles by topic."""
    try:
        query = args.get("query", "")
        customer_tier = args.get("customer_tier", "starter")
        category = args.get("category")
        max_results = args.get("max_results", 5)

        logger.info(
            "Searching knowledge base",
            query=query,
            tier=customer_tier,
            category=category,
        )

        embedding_model = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
        _cb = get_circuit_breaker("openai")
        try:
            async with _cb:
                embedding_resp = await context.openai_client.embeddings.create(
                    input=query,
                    model=embedding_model,
                )
            embedding = embedding_resp.data[0].embedding
        except CircuitBreakerError:
            logger.error("Knowledge base search failed (OpenAI circuit open)", query=query)
            return {
                "found": False,
                "results": [],
                "message": "Unable to search knowledge base. Please escalate to human support.",
                "error": "AI service temporarily unavailable",
            }

        # Search database using pgvector
        results = await db.search_knowledge_base(
            context.db_pool,
            embedding=embedding,
            customer_tier=customer_tier,
            category=category,
            max_results=max_results,
        )

        found = len(results) > 0
        logger.info(
            "Knowledge base search completed",
            query=query,
            found=found,
            result_count=len(results),
        )

        return {
            "found": found,
            "results": [dict(r) for r in results] if results else [],
            "message": f"Found {len(results)} relevant articles about '{query}' for {customer_tier} tier."
            if found
            else f"No articles found for '{query}'. Please escalate to human support.",
        }

    except (asyncpg.PostgresError, ConnectionError) as e:
        logger.error("Knowledge base search failed (DB)", error=sanitize_error_message(str(e)), query=args.get("query"))
        return {
            "found": False,
            "results": [],
            "message": "Unable to search knowledge base. Please escalate to human support.",
            "error": sanitize_error_message(str(e)),
        }
    except Exception as e:
        logger.error("Knowledge base search failed", error=sanitize_error_message(str(e)), query=args.get("query"))
        return {
            "found": False,
            "results": [],
            "message": "Unable to search knowledge base. Please escalate to human support.",
            "error": sanitize_error_message(str(e)),
        }


async def create_ticket(args: dict, context: ToolContext) -> dict:
    """Create a new support ticket for tracking."""
    try:
        customer_email = args.get("customer_email", "")
        channel = args.get("channel", "email")
        subject = args.get("subject", "")
        category = args.get("category", "general")
        priority = args.get("priority", "medium")
        initial_message = args.get("initial_message", "")

        logger.info(
            "Creating ticket",
            email=customer_email,
            channel=channel,
            category=category,
        )

        ticket = await db.create_ticket(
            pool=context.db_pool,
            customer_email=customer_email,
            subject=subject,
            category=category,
            priority=priority,
            channel=channel,
            initial_message=initial_message,
        )

        ticket_id = str(ticket["id"])
        ticket_number = ticket["ticket_number"]

        logger.info(
            "Ticket created",
            ticket_number=ticket_number,
            ticket_id=ticket_id,
        )

        return {
            "ticket_number": ticket_number,
            "ticket_id": ticket_id,
            "status": ticket.get("status", "open"),
            "tracking_url": f"https://support.techflow.com/tickets/{ticket_number}",
        }

    except (asyncpg.PostgresError, ConnectionError) as e:
        logger.error(
            "Ticket creation failed (DB)",
            error=sanitize_error_message(str(e)),
            email=args.get("customer_email"),
        )
        return {
            "ticket_number": None,
            "ticket_id": None,
            "status": "error",
            "tracking_url": None,
            "error": sanitize_error_message(str(e)),
        }
    except Exception as e:
        logger.error(
            "Ticket creation failed",
            error=sanitize_error_message(str(e)),
            email=args.get("customer_email"),
        )
        return {
            "ticket_number": None,
            "ticket_id": None,
            "status": "error",
            "tracking_url": None,
            "error": sanitize_error_message(str(e)),
        }


async def get_customer_history(args: dict, context: ToolContext) -> dict:
    """Retrieve customer's ticket history and previous interactions (cross-channel)."""
    try:
        customer_email = args.get("customer_email", "")
        limit = args.get("limit", 10)
        include_resolved = args.get("include_resolved", True)

        logger.info(
            "Fetching customer history",
            email=customer_email,
            limit=limit,
        )

        history = await db.get_customer_history(
            pool=context.db_pool,
            email=customer_email,
            limit=limit,
            include_resolved=include_resolved,
        )

        if not history:
            phone = args.get("customer_phone")
            if phone:
                customer = await db.get_customer_by_identifier(
                    context.db_pool, "phone", phone
                )
                if customer:
                    customer_email = customer["email"]
                    history = await db.get_customer_history(
                        pool=context.db_pool,
                        email=customer_email,
                        limit=limit,
                        include_resolved=include_resolved,
                    )

        logger.info(
            "Customer history retrieved",
            email=customer_email,
            ticket_count=len(history),
        )

        return {
            "ticket_count": len(history),
            "tickets": [dict(t) for t in history] if history else [],
            "message": f"Found {len(history)} previous tickets for {customer_email}."
            if history
            else f"No previous tickets found for {customer_email}.",
        }

    except (asyncpg.PostgresError, ConnectionError) as e:
        logger.error(
            "History retrieval failed (DB)",
            error=sanitize_error_message(str(e)),
            email=args.get("customer_email"),
        )
        return {
            "ticket_count": 0,
            "tickets": [],
            "message": "Unable to retrieve history.",
            "error": sanitize_error_message(str(e)),
        }
    except Exception as e:
        logger.error(
            "History retrieval failed",
            error=sanitize_error_message(str(e)),
            email=args.get("customer_email"),
        )
        return {
            "ticket_count": 0,
            "tickets": [],
            "message": "Unable to retrieve history.",
            "error": sanitize_error_message(str(e)),
        }


async def escalate_to_human(args: dict, context: ToolContext) -> dict:
    """Escalate ticket to human support team."""
    try:
        ticket_id = args.get("ticket_id", "")
        reason = args.get("reason", "")
        priority = args.get("priority", "high")
        context_summary = args.get("context_summary", "")

        logger.info(
            "Escalating ticket",
            ticket_id=ticket_id,
            priority=priority,
            reason=reason,
        )

        # Update ticket status to escalated
        ticket = await db.update_ticket_status(
            context.db_pool,
            UUID(ticket_id),
            "escalated",
        )

        # Send escalation event to Kafka
        escalation_message = create_escalation_message(
            ticket_id=ticket_id,
            customer_id="",  # Would be extracted from ticket
            reason=reason,
            priority=priority,
            context={"summary": context_summary},
        )

        await context.kafka_producer.send_message(
            escalation_message.topic,
            escalation_message.payload,
            key=ticket_id,
        )

        escalation_id = f"ESC-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        logger.info(
            "Ticket escalated",
            ticket_id=ticket_id,
            escalation_id=escalation_id,
        )

        return {
            "escalated": True,
            "escalation_id": escalation_id,
            "message": f"Ticket {ticket_id} has been escalated to our {priority} priority queue. "
            f"A human agent will review and respond shortly.",
        }

    except (asyncpg.PostgresError, ConnectionError) as e:
        logger.error(
            "Escalation failed (DB/Kafka)",
            error=sanitize_error_message(str(e)),
            ticket_id=args.get("ticket_id"),
        )
        return {
            "escalated": False,
            "escalation_id": None,
            "message": "Escalation failed.",
            "error": sanitize_error_message(str(e)),
        }
    except Exception as e:
        logger.error(
            "Escalation failed",
            error=sanitize_error_message(str(e)),
            ticket_id=args.get("ticket_id"),
        )
        return {
            "escalated": False,
            "escalation_id": None,
            "message": "Escalation failed.",
            "error": sanitize_error_message(str(e)),
        }


async def send_response(args: dict, context: ToolContext) -> dict:
    """Send a response to customer via specified channel."""
    try:
        ticket_id = args.get("ticket_id", "")
        customer_email = args.get("customer_email", "")
        channel = args.get("channel", "email")
        response_body = args.get("response_body", "")
        response_type = args.get("response_type", "informational")

        logger.info(
            "Sending response",
            ticket_id=ticket_id,
            channel=channel,
            response_type=response_type,
        )

        # Get ticket to extract customer_id
        ticket = await db.get_ticket(context.db_pool, UUID(ticket_id))
        if not ticket:
            return {
                "sent": False,
                "message_id": None,
                "message": f"Ticket {ticket_id} not found",
                "error": "Ticket not found",
            }

        customer_id = ticket["customer_id"]

        # Add message to database
        message = await db.add_message(
            pool=context.db_pool,
            ticket_id=UUID(ticket_id),
            customer_id=customer_id,
            direction="outbound",
            content=response_body,
            channel=channel,
        )

        message_id = str(message["id"])

        # Send notification via Kafka
        await context.kafka_producer.send_message(
            "notifications.outbound",
            {
                "ticket_id": ticket_id,
                "message_id": message_id,
                "customer_email": customer_email,
                "channel": channel,
                "content": response_body,
                "response_type": response_type,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            key=ticket_id,
        )

        logger.info(
            "Response sent",
            ticket_id=ticket_id,
            message_id=message_id,
            channel=channel,
        )

        return {
            "sent": True,
            "message_id": message_id,
            "message": f"Response sent to {customer_email} via {channel}.",
        }

    except (asyncpg.PostgresError, ConnectionError) as e:
        logger.error(
            "Failed to send response (DB/Kafka)",
            error=sanitize_error_message(str(e)),
            ticket_id=args.get("ticket_id"),
            channel=args.get("channel"),
        )
        return {
            "sent": False,
            "message_id": None,
            "message": "Failed to send response.",
            "error": sanitize_error_message(str(e)),
        }
    except Exception as e:
        logger.error(
            "Failed to send response",
            error=sanitize_error_message(str(e)),
            ticket_id=args.get("ticket_id"),
            channel=args.get("channel"),
        )
        return {
            "sent": False,
            "message_id": None,
            "message": "Failed to send response.",
            "error": sanitize_error_message(str(e)),
        }


# Tool dispatcher for executing tools by name
_TOOL_DISPATCH = {
    "search_knowledge_base": search_knowledge_base,
    "create_ticket": create_ticket,
    "get_customer_history": get_customer_history,
    "escalate_to_human": escalate_to_human,
    "send_response": send_response,
}


async def execute_tool(name: str, args: dict, context: ToolContext) -> str:
    """Execute a tool by name and return JSON string result."""
    fn = _TOOL_DISPATCH.get(name)
    try:
        if fn:
            result = await fn(args, context)
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": sanitize_error_message(str(e))}
    return json.dumps(result)


# Legacy alias for backward compatibility
AGENT_TOOLS = OPENAI_TOOL_SCHEMAS
