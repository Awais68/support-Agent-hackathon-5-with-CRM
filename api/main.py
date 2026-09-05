"""FastAPI application for TechFlow CRM Digital FTE."""

import os
import re
import traceback as tb
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID
from xml.sax.saxutils import escape as xml_escape

import asyncpg
import structlog
from env_config import load_environment
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from openai import APIError as OpenAIAPIError
from openai import AsyncOpenAI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, EmailStr, Field, field_validator
from pydantic import ValidationError as PydanticValidationError
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# Load .env BEFORE the first-party imports below: `uvicorn api.main:app` bypasses
# main.py's entrypoint, and modules like api.rate_limiter read os.getenv() at
# import time. Uses the shared loader so uvicorn sees the same layering
# (.env.<ENVIRONMENT> over .env) that main.py and the worker do.
load_environment()

from agent.customer_success_agent import AgentContext, CustomerSuccessAgent  # noqa: E402
from api.rate_limiter import limiter, rate_limit_exceeded_handler, strict_limit  # noqa: E402
from api.websocket_manager import WebSocketManager  # noqa: E402
from channels.voice_handler import VoiceHandler, twilio_language  # noqa: E402
from channels.web_form_handler import WebFormHandler, WebFormSubmission  # noqa: E402
from channels.whatsapp_handler import WhatsAppHandler  # noqa: E402
from database import queries as db  # noqa: E402
from embeddings_provider import (  # noqa: E402
    EMBEDDING_CIRCUIT_BREAKER,
    EmbeddingProvider,
    build_embedding_provider,
)
from exceptions import (  # noqa: E402
    AppError,
    ConfigurationError,
    NotFoundError,
    ValidationError,
    sanitize_error_message,
    to_error_response,
)
from kafka_client import KafkaProducerClient, NoOpKafkaProducer  # noqa: E402
from utils.circuit_breaker import CircuitBreakerError, get_circuit_breaker  # noqa: E402

logger = structlog.get_logger(__name__)


# Fail closed on unsigned Twilio webhooks in production deployments.
REQUIRE_TWILIO_SIGNATURE = os.getenv("REQUIRE_TWILIO_SIGNATURE", "false").lower() in (
    "1",
    "true",
    "yes",
)


def _kafka_requested() -> bool:
    """Whether the operator asked for a Kafka broker at all."""
    return os.getenv("ENABLE_KAFKA", "true").lower() in ("1", "true", "yes")


# Pydantic models
class HealthResponse(BaseModel):
    status: str
    db: str
    kafka: str


class CreateTicketRequest(BaseModel):
    name: str
    email: EmailStr
    subject: str
    category: str = "general"
    priority: str = "medium"
    message: str


class CreateTicketResponse(BaseModel):
    ticket_number: str
    ticket_id: str
    status: str
    created_at: str


class TicketResponse(BaseModel):
    id: str
    ticket_number: str
    customer_email: str
    subject: str
    status: str
    priority: str
    category: str
    channel: str
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    id: str
    ticket_id: str
    direction: str
    content: str
    channel: str
    created_at: str


# Mirrors the tickets.status CHECK constraint in database/schema.sql
VALID_TICKET_STATUSES = frozenset(
    {"open", "in_progress", "resolved", "escalated", "closed"}
)


class UpdateStatusRequest(BaseModel):
    status: str = Field(..., description="New status: open, in_progress, resolved, escalated, closed")

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in VALID_TICKET_STATUSES:
            raise ValueError(
                f"status must be one of: {', '.join(sorted(VALID_TICKET_STATUSES))}"
            )
        return v


class ReplyRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)


class MergeCustomerRequest(BaseModel):
    source_customer_id: UUID = Field(
        ..., description="Customer to absorb; its tickets move to the path customer and it is deleted"
    )
    reason: str | None = Field(None, max_length=500, description="Why the two records are the same person")


class MetricsResponse(BaseModel):
    metric_name: str
    metric_value: float
    timestamp: str


class DashboardMetricsResponse(BaseModel):
    status_counts: dict[str, int]
    avg_resolution_hours: float
    escalation_rate_percent: float
    channel_counts: dict[str, int]
    total_tickets: int


class KBSearchRequest(BaseModel):
    q: str
    category: str | None = None
    limit: int = 5


class KBArticleRequest(BaseModel):
    title: str
    content: str
    category: str = "general"
    tags: list[str] = []
    embedding: list[float] = []


class VoiceMessageRequest(BaseModel):
    """Voice message payload: base64 audio OR a public audio URL."""

    audio_base64: str | None = None
    audio_url: str | None = None
    filename: str = "voice.wav"
    content_type: str | None = None
    language: str | None = None
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None


class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    target_language: str | None = None


# Global state
db_pool: asyncpg.Pool | None = None
kafka_producer: KafkaProducerClient | None = None
ws_manager: WebSocketManager = WebSocketManager()


# Dependency injection
async def get_db(request: Request) -> asyncpg.Pool:
    """Get database pool from request state."""
    pool = getattr(request.app.state, "db_pool", None)
    if not pool:
        raise ConfigurationError(message="Database not initialized")
    return pool


async def get_kafka(request: Request) -> KafkaProducerClient:
    """Get Kafka producer from request state."""
    producer = getattr(request.app.state, "kafka_producer", None)
    if not producer:
        raise ConfigurationError(message="Kafka not initialized")
    return producer


async def get_openai(request: Request) -> AsyncOpenAI:
    """Get the OpenRouter chat client from request state."""
    client = getattr(request.app.state, "openai_client", None)
    if not client:
        raise ConfigurationError(message="OpenAI client not initialized")
    return client


async def get_embedding_provider(request: Request) -> EmbeddingProvider | None:
    """Get the embedding provider from request state (may be None if unconfigured)."""
    return getattr(request.app.state, "embedding_provider", None)


async def verify_api_key(
    request: Request, x_api_key: str | None = Header(None)
) -> bool:
    """Verify API key for non-webhook endpoints."""
    # Skip verification for webhook, health, and metrics endpoints
    if request.url.path in [
        "/health",
        "/metrics",
        "/webhooks/whatsapp",
        "/webhooks/webform",
        "/webhooks/voice/message",
        "/webhooks/voice/call",
    ]:
        return True

    # Deploy configs (docker-compose, render.yaml, k8s) set API_KEY; the .env
    # files set API_KEY_SECRET. Accept both so the key is never silently ignored.
    api_key = os.getenv("API_KEY") or os.getenv("API_KEY_SECRET") or "test-key-12345"
    key = request.headers.get("X-API-Key")
    if not key or key != api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    return True


# Lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management."""
    # Startup
    logger.info("Initializing FastAPI application")

    # Initialize database
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://***REDACTED_USER***:***REDACTED_PASS***@localhost/techflow",
    )
    pool_min = int(os.getenv("DATABASE_POOL_MIN", "1"))
    pool_max = int(os.getenv("DATABASE_POOL_MAX", "5"))
    db_ssl = os.getenv("DATABASE_SSL", "disable")
    app.state.db_pool = await asyncpg.create_pool(
        db_url,
        min_size=pool_min,
        max_size=pool_max,
        ssl=db_ssl,
        init=db.init_pgvector_connection,
    )
    logger.info("Database pool initialized")

    # Initialize Kafka (optional — free-tier deploys can run without a broker)
    kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    enable_kafka = _kafka_requested()
    app.state.kafka_enabled = False
    if enable_kafka:
        try:
            app.state.kafka_producer = KafkaProducerClient(kafka_bootstrap)
            await app.state.kafka_producer.start()
            app.state.kafka_enabled = True
            logger.info("Kafka producer initialized", servers=kafka_bootstrap)
        except Exception as e:
            logger.warning(
                "Kafka unavailable at startup — running in degraded mode (no broker)",
                error=sanitize_error_message(str(e)),
            )
            app.state.kafka_producer = NoOpKafkaProducer()
    else:
        logger.info("Kafka disabled via ENABLE_KAFKA=false — running in degraded mode")
        app.state.kafka_producer = NoOpKafkaProducer()

    # Initialize OpenRouter client (handles both chat completions and embeddings)
    # An empty string counts as "unset": exported-but-empty vars are common in
    # shell wrappers and would otherwise shadow the value from .env.
    openrouter_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not openrouter_key:
        raise ConfigurationError(
            message=(
                "OPENROUTER_API_KEY is not set. The agent cannot run without an "
                "AI provider key — set it in .env or the environment."
            )
        )
    app.state.openai_client = AsyncOpenAI(
        api_key=openrouter_key,
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )

    # Embeddings go to their own provider: OpenRouter serves chat here but has
    # no embedding credits, so knowledge base search runs on Gemini.
    app.state.embedding_provider = build_embedding_provider(app.state.openai_client)

    # Initialize WebSocket manager
    app.state.ws_manager = ws_manager
    logger.info("WebSocket manager initialized")

    # Initialize MCP Server for extensible tools
    # NOTE: MCP server provides alternative tool execution path via Model Context Protocol
    from mcp_server import mcp_server
    app.state.mcp_server = mcp_server
    logger.info("MCP server initialized")

    yield

    # Shutdown
    logger.info("Shutting down application")
    await app.state.kafka_producer.stop()
    await app.state.db_pool.close()
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="TechFlow CRM Digital FTE",
    description="Customer Success AI Agent",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# API Key middleware
@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Verify API key for all endpoints except webhooks and health."""
    try:
        await verify_api_key(request)
    except HTTPException as e:
        from starlette.responses import JSONResponse
        return JSONResponse(
            status_code=e.status_code,
            content={"detail": e.detail},
        )
    response = await call_next(request)
    return response


# Health check endpoint
@app.get("/health", response_model=HealthResponse)
@limiter.exempt
async def health_check(request: Request, pool: asyncpg.Pool = Depends(get_db)) -> HealthResponse:
    """Health check endpoint."""
    db_status = "ok"
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except asyncpg.PostgresError as e:
        logger.error("Database health check failed", error=sanitize_error_message(str(e)))
        db_status = "error"
    except Exception as e:
        logger.error("Unexpected health check error", error=sanitize_error_message(str(e)))
        db_status = "error"

    if getattr(request.app.state, "kafka_enabled", False):
        kafka_status = "ok"
    else:
        kafka_status = "disabled" if not _kafka_requested() else "error"

    return HealthResponse(
        status="healthy" if db_status == "ok" else "degraded",
        db=db_status,
        kafka=kafka_status,
    )


# Prometheus metrics endpoint
@app.get("/metrics", include_in_schema=False)
@limiter.exempt
async def prometheus_metrics(request: Request) -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# WebSocket endpoint for real-time ticket updates
@app.websocket("/ws/tickets/{ticket_id}")
async def websocket_ticket(
    websocket: WebSocket,
    ticket_id: UUID,
):
    """WebSocket endpoint for real-time ticket updates."""
    tid = str(ticket_id)
    await ws_manager.connect(websocket, tid)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(websocket, tid)


# Ticket endpoints
@app.post("/tickets", response_model=CreateTicketResponse, status_code=201)
@limiter.limit("20/minute")
async def create_ticket(
    request: Request,
    body: CreateTicketRequest,
    pool: asyncpg.Pool = Depends(get_db),
) -> CreateTicketResponse:
    """Create a new support ticket."""
    ticket = await db.create_ticket(
        pool,
        customer_email=body.email,
        subject=body.subject,
        category=body.category,
        priority=body.priority,
        channel="api",
        initial_message=body.message,
    )

    tid = str(ticket["id"])
    await ws_manager.broadcast_ticket_update(
        tid,
        {
            "ticket_id": tid,
            "ticket_number": ticket["ticket_number"],
            "status": ticket["status"],
            "subject": body.subject,
            "customer_email": body.email,
            "created_at": ticket["created_at"].isoformat(),
        },
    )

    return CreateTicketResponse(
        ticket_number=ticket["ticket_number"],
        ticket_id=tid,
        status=ticket["status"],
        created_at=ticket["created_at"].isoformat(),
    )


@app.get("/tickets")
async def list_tickets(
    status: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """List all tickets with pagination."""
    if status is not None and status not in VALID_TICKET_STATUSES:
        raise ValidationError(
            message=f"Invalid status. Must be one of: {', '.join(sorted(VALID_TICKET_STATUSES))}"
        )

    tickets, total = await db.list_tickets(pool, status=status, limit=limit, offset=offset)

    return {
        "tickets": [dict(t) for t in tickets],
        "total": total,
        "page": offset // limit + 1,
        "limit": limit,
    }


@app.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """Get full ticket details by internal UUID or by TKT-… ticket number.

    The web form hands the customer a ticket_number, and the tracking page
    resolves that same value, so a UUID-only path param rejected every
    customer-facing lookup with a 422.
    """
    try:
        ticket_uuid: UUID | None = UUID(ticket_id)
    except ValueError:
        ticket_uuid = None

    if ticket_uuid is not None:
        ticket = await db.get_ticket(pool, ticket_uuid)
    else:
        ticket = await db.get_ticket_by_number(pool, ticket_id)

    if not ticket:
        raise NotFoundError(message=f"Ticket {ticket_id} not found")

    resolved_id = ticket["id"]
    messages = await db.get_ticket_messages(pool, resolved_id)
    agent_runs = await db.get_agent_runs(pool, resolved_id)

    return {
        **dict(ticket),
        "messages": [dict(m) for m in messages],
        "agent_runs": [dict(ar) for ar in agent_runs],
    }


@app.patch("/tickets/{ticket_id}/status")
async def update_ticket_status(
    ticket_id: UUID,
    request: UpdateStatusRequest,
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """Update ticket status."""
    ticket = await db.update_ticket_status(pool, ticket_id, request.status)
    if not ticket:
        raise NotFoundError(message=f"Ticket {ticket_id} not found")

    await ws_manager.broadcast_ticket_update(str(ticket_id), dict(ticket))

    return dict(ticket)


@app.get("/tickets/{ticket_id}/messages")
async def get_ticket_messages(
    ticket_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """Get messages for a ticket."""
    messages = await db.get_ticket_messages(pool, ticket_id, limit=limit)

    return {
        "ticket_id": str(ticket_id),
        "messages": [dict(m) for m in messages],
        "count": len(messages),
    }


@app.post("/tickets/{ticket_id}/reply", status_code=201)
@limiter.limit(strict_limit)
async def reply_to_ticket(
    request: Request,
    ticket_id: UUID,
    body: ReplyRequest,
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """Send a reply to a ticket."""
    ticket = await db.get_ticket(pool, ticket_id)
    if not ticket:
        raise NotFoundError(message=f"Ticket {ticket_id} not found")

    message = await db.add_message(
        pool,
        ticket_id=ticket_id,
        customer_id=ticket["customer_id"],
        direction="outbound",
        content=body.message,
        channel=ticket["channel"],
    )

    await ws_manager.broadcast_new_message(
        str(ticket_id),
        {
            "message_id": str(message["id"]),
            "content": body.message,
            "direction": "outbound",
            "created_at": message["created_at"].isoformat(),
        },
    )

    return {
        "message_id": str(message["id"]),
        "sent_at": message["created_at"].isoformat(),
    }


# Webhook endpoints
@app.post("/webhooks/whatsapp")
@limiter.limit(strict_limit)
async def webhook_whatsapp(
    request: Request,
    kafka_producer: KafkaProducerClient = Depends(get_kafka),
) -> dict[str, str]:
    """WhatsApp webhook from Twilio."""
    try:
        form_data = await request.form()
        handler = WhatsAppHandler(kafka_producer)

        signature = request.headers.get("X-Twilio-Signature", "")
        params = {k: str(v) for k, v in form_data.items()}
        if handler.signature_validation_enabled:
            if not handler.validate_webhook(signature, params=params, url=str(request.url)):
                logger.warning("Rejected WhatsApp webhook: invalid Twilio signature")
                raise HTTPException(status_code=403, detail="Invalid webhook signature")
        elif REQUIRE_TWILIO_SIGNATURE:
            logger.error("Rejected WhatsApp webhook: TWILIO_AUTH_TOKEN not configured")
            raise HTTPException(status_code=403, detail="Webhook signature validation unavailable")
        else:
            logger.warning(
                "Skipping WhatsApp signature validation (no TWILIO_AUTH_TOKEN configured)"
            )

        message_data = await handler.parse_webhook(params)
        if message_data:
            await handler.handle_incoming_message(
                from_number=message_data["from_number"],
                sender_name=message_data["sender_name"],
                message_body=message_data["message_body"],
                media_url=message_data.get("media_url"),
            )

        return {"status": "received"}
    except HTTPException:
        raise
    except (asyncpg.PostgresError, ConnectionError) as e:
        logger.error("WhatsApp webhook DB/connection error", error=sanitize_error_message(str(e)))
        return {"status": "error"}
    except Exception as e:
        logger.error("Unexpected WhatsApp webhook error", error=sanitize_error_message(str(e)))
        return {"status": "error"}


@app.post("/webhooks/webform", response_model=dict[str, Any], status_code=201)
@limiter.limit(strict_limit)
async def webhook_webform(
    request: Request,
    body: WebFormSubmission,
) -> dict[str, Any]:
    """Web form submission endpoint.

    The request body is validated (Pydantic) BEFORE any infrastructure is touched,
    so invalid submissions are rejected with 422 even if DB/Kafka are unavailable.
    """
    pool = getattr(request.app.state, "db_pool", None)
    if not pool:
        raise ConfigurationError(message="Database not initialized")
    kafka_producer = getattr(request.app.state, "kafka_producer", None)
    kafka_enabled = getattr(request.app.state, "kafka_enabled", False)
    openai_client = getattr(request.app.state, "openai_client", None)

    ticket = await db.create_ticket(
        pool,
        customer_email=body.email,
        subject=body.subject,
        category=body.category,
        priority=body.priority,
        channel="webform",
        initial_message=body.message,
    )

    # Publish AFTER the ticket exists and carry its id, so the worker enriches
    # this ticket instead of creating a duplicate one.
    if kafka_producer is not None:
        handler = WebFormHandler(kafka_producer)
        try:
            await handler.process_submission(body, ticket_id=str(ticket["id"]))
        except Exception as e:
            # Kafka routing must not prevent ticket creation
            logger.warning(
                "Failed to route webform submission to Kafka",
                error=sanitize_error_message(str(e)),
                email=body.email,
            )

    # Without a Kafka broker/worker, run the agent synchronously so the
    # submission still gets an AI reply and the demo stays fully functional.
    if not kafka_enabled and openai_client is not None:
        try:
            agent_context = AgentContext(
                db_pool=pool,
                kafka_producer=kafka_producer or NoOpKafkaProducer(),
                openai_client=openai_client,
                embedding_provider=getattr(app.state, "embedding_provider", None),
            )
            agent = CustomerSuccessAgent(agent_context)
            result = await agent.process_customer_message(
                ticket_id=ticket["id"],
                customer_id=ticket["customer_id"],
                customer_email=body.email,
                customer_name=body.name,
                message=body.message,
                channel="webform",
                ticket_number=ticket["ticket_number"],
            )
            await db.add_message(
                pool,
                ticket_id=ticket["id"],
                customer_id=ticket["customer_id"],
                direction="outbound",
                content=result["response"],
                channel="webform",
            )
        except Exception as e:
            logger.warning(
                "Synchronous agent reply failed",
                error=sanitize_error_message(str(e)),
                email=body.email,
            )

    return {
        "ticket_number": ticket["ticket_number"],
        "message": (
            f"Thank you for your submission. Your ticket number is {ticket['ticket_number']}"
        ),
        "estimated_response": (
            "24 hours for Starter tier, 8 hours for Growth tier, 2 hours for Enterprise tier"
        ),
        "tracking_url": f"https://support.techflow.com/track/{ticket['ticket_number']}",
    }


# ---------------------------------------------------------------------------
# Voice channel (voice messages + phone calls)
# ---------------------------------------------------------------------------
async def _run_voice_agent(
    pool: asyncpg.Pool,
    kafka_producer: KafkaProducerClient,
    openai_client: AsyncOpenAI,
    message: str,
    customer_name: str = "",
    customer_email: str = "",
    customer_phone: str = "",
) -> dict[str, Any]:
    """Create a voice ticket and run the customer success agent synchronously.

    Returns the agent's response plus ticket identifiers so the caller can
    synthesize and reply over voice.
    """
    if not customer_email:
        if customer_phone:
            normalized = re.sub(r"[^\d]", "", customer_phone)
            customer_email = f"voice{normalized}@voice.local"
        else:
            customer_email = "voice@customer.local"

    subject = (message or "Voice message").strip()[:80]
    ticket = await db.create_ticket(
        pool,
        customer_email=customer_email,
        subject=subject,
        category="general",
        priority="medium",
        channel="voice",
        initial_message=message,
    )

    context = AgentContext(
        db_pool=pool,
        kafka_producer=kafka_producer,
        openai_client=openai_client,
        embedding_provider=getattr(app.state, "embedding_provider", None),
    )
    agent = CustomerSuccessAgent(context)
    result = await agent.process_customer_message(
        ticket_id=ticket["id"],
        customer_id=ticket["customer_id"],
        customer_email=customer_email,
        customer_name=customer_name or customer_email.split("@")[0],
        message=message,
        channel="voice",
    )

    return {
        "response": result["response"],
        "ticket_id": result["ticket_id"],
        "ticket_number": ticket["ticket_number"],
    }


@app.post("/webhooks/voice/message", response_model=dict[str, Any])
@limiter.limit(strict_limit)
async def webhook_voice_message(
    request: Request,
    body: VoiceMessageRequest,
) -> dict[str, Any]:
    """Process a voice message: STT → translate → agent → TTS reply."""
    pool = getattr(request.app.state, "db_pool", None)
    kafka_producer = getattr(request.app.state, "kafka_producer", None)
    openai_client = getattr(request.app.state, "openai_client", None)
    if not (pool and kafka_producer and openai_client):
        raise ConfigurationError(message="Infrastructure not initialized")

    handler = VoiceHandler(kafka_producer, openai_client=openai_client)
    audio_bytes = await handler.resolve_audio(body.audio_base64, body.audio_url)
    if not audio_bytes:
        raise ValidationError(message="Provide a valid audio_base64 or audio_url")

    result = await handler.handle_voice_message(
        audio_bytes=audio_bytes,
        filename=body.filename or "voice.wav",
        content_type=body.content_type,
        language=body.language,
        name=body.name,
        email=str(body.email) if body.email else None,
        phone=body.phone,
        run_agent=lambda english: _run_voice_agent(
            pool,
            kafka_producer,
            openai_client,
            english,
            customer_name=body.name or "",
            customer_email=str(body.email) if body.email else "",
            customer_phone=body.phone or "",
        ),
    )
    return result.to_dict()


@app.post("/webhooks/voice/call")
@limiter.limit(strict_limit)
async def webhook_voice_call(request: Request) -> Response:
    """Twilio voice call webhook.

    Returns TwiML: first a ``<Gather input="speech">`` to capture the customer's
    request, then the agent's spoken reply in the customer's own language.
    """
    form = await request.form()
    speech_result = str(form.get("SpeechResult") or "").strip()
    try:
        speech_confidence = float(str(form.get("Confidence") or 1.0))
    except ValueError:
        speech_confidence = 1.0
    from_number = str(form.get("From") or "")

    pool = getattr(request.app.state, "db_pool", None)
    kafka_producer = getattr(request.app.state, "kafka_producer", None)
    openai_client = getattr(request.app.state, "openai_client", None)
    if not (pool and kafka_producer and openai_client):
        raise ConfigurationError(message="Infrastructure not initialized")

    if not speech_result:
        # Greeting: ask the customer to state their issue in one sentence
        twiml = (
            '<Response><Gather input="speech" timeout="4" language="en-US">'
            "<Say>Hello, thank you for calling TechFlow support. "
            "Please tell me in one sentence how I can help you today.</Say>"
            "</Gather><Say>We didn't receive your response. Goodbye.</Say></Response>"
        )
        return Response(content=twiml, media_type="application/xml")

    handler = VoiceHandler(kafka_producer, openai_client=openai_client)

    if speech_confidence < handler.confidence_threshold:
        reply = (
            "I'm sorry, I didn't catch that clearly. "
            "Please call back and speak a little more clearly."
        )
        return Response(
            content=f"<Response><Say>{xml_escape(reply)}</Say></Response>",
            media_type="application/xml",
        )

    english, lang, was_translated = await handler.detect_and_translate(speech_result)
    try:
        agent_result = await _run_voice_agent(
            pool,
            kafka_producer,
            openai_client,
            english,
            customer_name="",
            customer_email="",
            customer_phone=from_number,
        )
        reply = agent_result["response"]
    except Exception as e:
        logger.error("Voice call agent failed", error=sanitize_error_message(str(e)))
        reply = (
            "I'm sorry, I'm having trouble right now. "
            "A human agent will call you back shortly."
        )

    if was_translated:
        reply = await handler.translate_to_language(reply, lang)

    twiml = (
        f'<Response><Say language="{twilio_language(lang)}">'
        f"{xml_escape(reply)}</Say></Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@app.post("/voice/transcribe", response_model=dict[str, Any])
@limiter.limit(strict_limit)
async def voice_transcribe(
    request: Request,
    body: VoiceMessageRequest,
) -> dict[str, Any]:
    """STT-only endpoint: transcribe + translate, no agent run (for testing)."""
    kafka_producer = getattr(request.app.state, "kafka_producer", None)
    openai_client = getattr(request.app.state, "openai_client", None)
    handler = VoiceHandler(kafka_producer, openai_client=openai_client)

    audio_bytes = await handler.resolve_audio(body.audio_base64, body.audio_url)
    if not audio_bytes:
        raise ValidationError(message="Provide a valid audio_base64 or audio_url")

    result = await handler.handle_voice_message(
        audio_bytes=audio_bytes,
        filename=body.filename or "voice.wav",
        content_type=body.content_type,
        language=body.language,
        name=body.name,
        email=str(body.email) if body.email else None,
        phone=body.phone,
        run_agent=None,
    )
    return result.to_dict()


@app.post("/voice/translate", response_model=dict[str, Any])
@limiter.limit(strict_limit)
async def voice_translate(
    request: Request,
    body: TranslateRequest,
) -> dict[str, Any]:
    """Translate text. Without ``target_language``: detect + translate to English."""
    openai_client = getattr(request.app.state, "openai_client", None)
    handler = VoiceHandler(kafka_producer=None, openai_client=openai_client)

    if body.target_language:
        translated = await handler.translate_to_language(body.text, body.target_language)
        return {"text": translated, "target_language": body.target_language}

    english, language, was_translated = await handler.detect_and_translate(body.text)
    return {
        "translated": english,
        "language": language,
        "was_translated": was_translated,
    }


# Customer endpoints
@app.get("/customers/{email}/history")
@limiter.limit("30/minute")
async def get_customer_history(
    request: Request,
    email: str,
    limit: int = 10,
    include_resolved: bool = True,
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """Get customer history."""
    history = await db.get_customer_history(pool, email, limit=limit, include_resolved=include_resolved)

    return {
        "email": email,
        "tickets": [dict(h) for h in history],
        "count": len(history),
    }


# Identity review queue — name similarity flags duplicates but never merges
# automatically (see database.queries.NAME_FUZZY_THRESHOLD); a human decides here.
@app.get("/customers/review-queue")
@limiter.limit("30/minute")
async def list_identity_review_queue(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """List customers flagged as possible duplicates, with their candidate match."""
    rows = await db.list_identity_review_queue(pool, limit=limit, offset=offset)
    return {"queue": rows, "count": len(rows), "limit": limit, "offset": offset}


@app.post("/customers/{customer_id}/merge")
@limiter.limit(strict_limit)
async def merge_customer(
    request: Request,
    customer_id: UUID,
    body: MergeCustomerRequest,
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """Merge ``source_customer_id`` into ``customer_id``. The path customer survives."""
    try:
        result = await db.merge_customers(
            pool,
            target_customer_id=customer_id,
            source_customer_id=body.source_customer_id,
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "merged": True,
        "target_customer_id": str(customer_id),
        "source_customer_id": str(body.source_customer_id),
        "source_email_kept_as_identifier": result["source_email"],
        "moved": result["moved"],
        "customer": result["target"],
    }


@app.post("/customers/{customer_id}/review/dismiss")
@limiter.limit("30/minute")
async def dismiss_identity_review(
    request: Request,
    customer_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """Mark a flagged customer as a distinct person; clears the review flag."""
    customer = await db.dismiss_identity_review(pool, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"dismissed": True, "customer": customer}


# Metrics endpoints
@app.get("/metrics/summary")
async def get_metrics_summary(
    hours: int = 24,
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """Get metrics summary for the last N hours."""
    metrics = await db.get_metrics_summary(pool, hours=hours)

    return {
        "period_hours": hours,
        "metrics": [dict(m) for m in metrics],
        "count": len(metrics),
    }


@app.get("/metrics/dashboard", response_model=DashboardMetricsResponse)
async def get_dashboard_metrics(
    pool: asyncpg.Pool = Depends(get_db),
) -> DashboardMetricsResponse:
    """Get aggregated metrics for dashboard."""
    metrics = await db.get_dashboard_metrics(pool)
    return DashboardMetricsResponse(**metrics)


# Knowledge base endpoints
@app.get("/knowledge-base")
async def search_knowledge_base(
    q: str = Query(..., min_length=1, max_length=500),
    category: str | None = None,
    limit: int = Query(5, ge=1, le=50),
    customer_tier: str = "starter",
    pool: asyncpg.Pool = Depends(get_db),
    provider: EmbeddingProvider | None = Depends(get_embedding_provider),
) -> dict[str, Any]:
    """Search knowledge base using real pgvector cosine similarity."""
    _cb = get_circuit_breaker(EMBEDDING_CIRCUIT_BREAKER)
    embedding = None
    degraded_reason = None

    if provider is None:
        degraded_reason = "No embedding provider configured"
        logger.warning("No embedding provider, falling back to text KB search", query=q)
    else:
        try:
            async with _cb:
                embedding = await provider.embed(q)
        except CircuitBreakerError:
            degraded_reason = "AI service temporarily unavailable"
            logger.warning("Embedding circuit open, falling back to text KB search", query=q)
        except OpenAIAPIError as e:
            degraded_reason = "AI service temporarily unavailable"
            logger.warning(
                "Embedding failed, falling back to text KB search",
                query=q,
                error=sanitize_error_message(str(e)),
            )

    # Degrade to lexical search rather than failing the request outright.
    if embedding is None:
        results = await db.search_knowledge_base_text(
            pool,
            query=q,
            customer_tier=customer_tier,
            category=category,
            max_results=limit,
        )
        return {
            "query": q,
            "results": [dict(r) for r in results] if results else [],
            "count": len(results) if results else 0,
            "search_mode": "text",
            "degraded": True,
            "error": degraded_reason,
        }

    # Restricted to rows indexed by this same model; a provider switch leaves
    # the old vectors in place but incomparable, and this returns nothing.
    results = await db.search_knowledge_base(
        pool,
        embedding=embedding,
        customer_tier=customer_tier,
        category=category,
        max_results=limit,
        embedding_model=provider.model,
    )

    if not results:
        # Empty here means no row carries a vector from this model, so the
        # result must be reported as degraded whether or not lexical finds
        # anything — an unqualified empty "vector" result reads as "no such
        # article", which is a different and wrong answer.
        text_results = await db.search_knowledge_base_text(
            pool,
            query=q,
            customer_tier=customer_tier,
            category=category,
            max_results=limit,
        )
        logger.warning(
            "No comparable vectors for embedding model, using text search",
            query=q,
            embedding_model=provider.model,
            text_results=len(text_results) if text_results else 0,
        )
        return {
            "query": q,
            "results": [dict(r) for r in text_results] if text_results else [],
            "count": len(text_results) if text_results else 0,
            "search_mode": "text",
            "degraded": True,
            "error": "Knowledge base not indexed for the active embedding model",
        }

    return {
        "query": q,
        "results": [dict(r) for r in results] if results else [],
        "count": len(results) if results else 0,
        "search_mode": "vector",
    }


@app.post("/knowledge-base/ingest", status_code=201)
@limiter.limit(strict_limit)
async def ingest_knowledge_base(
    request: Request,
    body: KBArticleRequest,
    pool: asyncpg.Pool = Depends(get_db),
) -> dict[str, Any]:
    """Ingest a knowledge base article."""
    article = await db.add_knowledge_base_article(
        pool,
        title=body.title,
        content=body.content,
        embedding=body.embedding or [],
        category=body.category,
        tags=body.tags,
    )

    return {
        "id": str(article["id"]),
        "title": article["title"],
        "embedded": len(body.embedding) > 0,
    }


# Error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions."""
    logger.warning(
        "HTTP exception",
        status_code=exc.status_code,
        detail=exc.detail,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


def _sanitize_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Make Pydantic error contexts JSON-safe (exceptions aren't serializable)."""
    cleaned: list[dict[str, Any]] = []
    for err in errors:
        item = dict(err)
        ctx = item.get("ctx")
        if isinstance(ctx, dict):
            item["ctx"] = {
                k: (str(v) if isinstance(v, BaseException) else v) for k, v in ctx.items()
            }
        cleaned.append(item)
    return cleaned


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    """Handle structured application errors."""
    logger.error(
        "Application error",
        error_code=exc.error_code,
        message=exc.message,
        path=request.url.path,
        method=request.method,
    )
    resp = to_error_response(exc)
    return JSONResponse(status_code=exc.status_code, content=resp)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Handle Pydantic/FastAPI validation errors."""
    logger.warning(
        "Validation error",
        errors=_sanitize_validation_errors(exc.errors()),
        path=request.url.path,
    )
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": _sanitize_validation_errors(exc.errors()),
        },
    )


@app.exception_handler(PydanticValidationError)
async def pydantic_validation_handler(request: Request, exc: PydanticValidationError):
    """Handle Pydantic validation errors."""
    logger.warning(
        "Pydantic validation error",
        errors=exc.errors(),
        path=request.url.path,
    )
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": exc.errors(),
        },
    )


@app.exception_handler(asyncpg.PostgresError)
async def postgres_error_handler(request: Request, exc: asyncpg.PostgresError):
    """Handle PostgreSQL errors - return safe message, log full details."""
    logger.error(
        "Database error",
        error=sanitize_error_message(str(exc)),
        path=request.url.path,
        method=request.method,
        traceback=tb.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "DATABASE_ERROR",
            "message": "A database error occurred. Please try again.",
        },
    )


@app.exception_handler(ConnectionError)
async def connection_error_handler(request: Request, exc: ConnectionError):
    """Handle connection errors (Kafka, network, etc.)."""
    logger.error(
        "Connection error",
        error=sanitize_error_message(str(exc)),
        path=request.url.path,
        traceback=tb.format_exc(),
    )
    return JSONResponse(
        status_code=502,
        content={
            "error": "CONNECTION_ERROR",
            "message": "A connection error occurred. Please try again.",
        },
    )


@app.exception_handler(OpenAIAPIError)
async def openai_error_handler(request: Request, exc: OpenAIAPIError):
    """Handle OpenAI/OpenRouter API errors."""
    logger.error(
        "OpenAI API error",
        error=sanitize_error_message(str(exc)),
        path=request.url.path,
        traceback=tb.format_exc(),
    )
    return JSONResponse(
        status_code=502,
        content={
            "error": "AI_SERVICE_ERROR",
            "message": "AI service temporarily unavailable.",
        },
    )


@app.exception_handler(CircuitBreakerError)
async def circuit_breaker_handler(request: Request, exc: CircuitBreakerError):
    """Handle circuit breaker open errors - service degraged gracefully."""
    logger.warning(
        "Circuit breaker open, request rejected",
        error=sanitize_error_message(str(exc)),
        path=request.url.path,
    )
    return JSONResponse(
        status_code=503,
        content={
            "error": "SERVICE_UNAVAILABLE",
            "message": "A downstream service is temporarily unavailable. Please try again later.",
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all for unhandled exceptions. Logs full traceback, returns safe JSON."""
    logger.error(
        "Unhandled exception",
        error=sanitize_error_message(str(exc)),
        path=request.url.path,
        method=request.method,
        traceback=tb.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "An unexpected error occurred.",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
