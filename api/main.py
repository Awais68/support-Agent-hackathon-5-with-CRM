"""FastAPI application for TechFlow CRM Digital FTE."""

import os
import traceback as tb
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import FastAPI, Depends, HTTPException, Request, Header, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, EmailStr
from pydantic import ValidationError as PydanticValidationError
from openai import AsyncOpenAI, APIError as OpenAIAPIError
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from kafka_client import KafkaProducerClient
from database import queries as db
from api.websocket_manager import WebSocketManager
from channels.web_form_handler import WebFormHandler, WebFormSubmission
from channels.whatsapp_handler import WhatsAppHandler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from api.rate_limiter import limiter, rate_limit_exceeded_handler, strict_limit

from exceptions import (
    AppError,
    NotFoundError,
    ConfigurationError,
    sanitize_error_message,
    to_error_response,
)
from utils.circuit_breaker import get_circuit_breaker, CircuitBreakerError

logger = structlog.get_logger(__name__)


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


class UpdateStatusRequest(BaseModel):
    status: str = Field(..., description="New status: open, in_progress, resolved, escalated, closed")


class ReplyRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)


class MetricsResponse(BaseModel):
    metric_name: str
    metric_value: float
    timestamp: str


class DashboardMetricsResponse(BaseModel):
    status_counts: Dict[str, int]
    avg_resolution_hours: float
    escalation_rate_percent: float
    channel_counts: Dict[str, int]
    total_tickets: int


class KBSearchRequest(BaseModel):
    q: str
    category: Optional[str] = None
    limit: int = 5


class KBArticleRequest(BaseModel):
    title: str
    content: str
    category: str = "general"
    tags: List[str] = []
    embedding: List[float] = []


# Global state
db_pool: Optional[asyncpg.Pool] = None
kafka_producer: Optional[KafkaProducerClient] = None
ws_manager: WebSocketManager = WebSocketManager()


# Dependency injection
async def get_db(request: Request) -> asyncpg.Pool:
    """Get database pool from request state."""
    if not request.app.state.db_pool:
        raise ConfigurationError(message="Database not initialized")
    return request.app.state.db_pool


async def get_kafka(request: Request) -> KafkaProducerClient:
    """Get Kafka producer from request state."""
    if not request.app.state.kafka_producer:
        raise ConfigurationError(message="Kafka not initialized")
    return request.app.state.kafka_producer


async def get_openai(request: Request) -> AsyncOpenAI:
    """Get OpenRouter client from request state (handles chat + embeddings)."""
    if not request.app.state.openai_client:
        raise ConfigurationError(message="OpenAI client not initialized")
    return request.app.state.openai_client


async def verify_api_key(
    request: Request, x_api_key: Optional[str] = Header(None)
) -> bool:
    """Verify API key for non-webhook endpoints."""
    # Skip verification for webhook, health, and metrics endpoints
    if request.url.path in ["/health", "/webhooks/whatsapp", "/webhooks/webform", "/metrics"]:
        return True

    api_key = os.getenv("API_KEY", "test-key-12345")
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
    )
    await db.register_pgvector_codec(app.state.db_pool)
    logger.info("Database pool initialized")

    # Initialize Kafka
    kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    app.state.kafka_producer = KafkaProducerClient(kafka_bootstrap)
    await app.state.kafka_producer.start()
    logger.info("Kafka producer initialized")

    # Initialize OpenRouter client (handles both chat completions and embeddings)
    app.state.openai_client = AsyncOpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )

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

    return HealthResponse(
        status="healthy" if db_status == "ok" else "degraded",
        db=db_status,
        kafka="ok",
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
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
    """List all tickets with pagination."""
    tickets, total = await db.list_tickets(pool, status=status, limit=limit, offset=offset)

    return {
        "tickets": [dict(t) for t in tickets],
        "total": total,
        "page": offset // limit + 1,
        "limit": limit,
    }


@app.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
    """Get full ticket details."""
    ticket = await db.get_ticket(pool, ticket_id)
    if not ticket:
        raise NotFoundError(message=f"Ticket {ticket_id} not found")

    messages = await db.get_ticket_messages(pool, ticket_id)
    agent_runs = await db.get_agent_runs(pool, ticket_id)

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
) -> Dict[str, Any]:
    """Update ticket status."""
    ticket = await db.update_ticket_status(pool, ticket_id, request.status)
    if not ticket:
        raise NotFoundError(message=f"Ticket {ticket_id} not found")

    await ws_manager.broadcast_ticket_update(str(ticket_id), dict(ticket))

    return dict(ticket)


@app.get("/tickets/{ticket_id}/messages")
async def get_ticket_messages(
    ticket_id: UUID,
    limit: int = 50,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
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
) -> Dict[str, Any]:
    """Send a reply to a ticket."""
    ticket = await db.get_ticket(pool, ticket_id)
    if not ticket:
        raise NotFoundError(message=f"Ticket {ticket_id} not found")

    message = await db.add_message(
        pool,
        ticket_id=ticket_id,
        customer_id=UUID(ticket["customer_id"]),
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
) -> Dict[str, str]:
    """WhatsApp webhook from Twilio."""
    try:
        form_data = await request.form()
        handler = WhatsAppHandler(kafka_producer)

        body = await request.body()
        signature = request.headers.get("X-Twilio-Signature", "")
        if not handler.validate_webhook(body.decode(), signature):
            logger.warning("Invalid WhatsApp webhook signature")

        message_data = await handler.parse_webhook(dict(form_data))
        if message_data:
            await handler.handle_incoming_message(
                from_number=message_data["from_number"],
                sender_name=message_data["sender_name"],
                message_body=message_data["message_body"],
                media_url=message_data.get("media_url"),
            )

        return {"status": "received"}
    except (asyncpg.PostgresError, ConnectionError) as e:
        logger.error("WhatsApp webhook DB/connection error", error=sanitize_error_message(str(e)))
        return {"status": "error"}
    except Exception as e:
        logger.error("Unexpected WhatsApp webhook error", error=sanitize_error_message(str(e)))
        return {"status": "error"}


@app.post("/webhooks/webform", response_model=Dict[str, Any], status_code=201)
@limiter.limit(strict_limit)
async def webhook_webform(
    request: WebFormSubmission,
    pool: asyncpg.Pool = Depends(get_db),
    kafka_producer: KafkaProducerClient = Depends(get_kafka),
) -> Dict[str, Any]:
    """Web form submission endpoint."""
    handler = WebFormHandler(kafka_producer)
    await handler.process_submission(request)

    ticket = await db.create_ticket(
        pool,
        customer_email=request.email,
        subject=request.subject,
        category=request.category,
        priority=request.priority,
        channel="webform",
        initial_message=request.message,
    )

    return {
        "ticket_number": ticket["ticket_number"],
        "message": f"Thank you for your submission. Your ticket number is {ticket['ticket_number']}",
        "estimated_response": "24 hours for Starter tier, 8 hours for Growth tier, 2 hours for Enterprise tier",
        "tracking_url": f"https://support.techflow.com/track/{ticket['ticket_number']}",
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
) -> Dict[str, Any]:
    """Get customer history."""
    history = await db.get_customer_history(pool, email, limit=limit, include_resolved=include_resolved)

    return {
        "email": email,
        "tickets": [dict(h) for h in history],
        "count": len(history),
    }


# Metrics endpoints
@app.get("/metrics/summary")
async def get_metrics_summary(
    hours: int = 24,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
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
    q: str,
    category: Optional[str] = None,
    limit: int = 5,
    customer_tier: str = "starter",
    pool: asyncpg.Pool = Depends(get_db),
    openai_client: AsyncOpenAI = Depends(get_openai),
) -> Dict[str, Any]:
    """Search knowledge base using real pgvector cosine similarity."""
    embedding_model = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
    _cb = get_circuit_breaker("openai")
    try:
        async with _cb:
            embedding_resp = await openai_client.embeddings.create(
                input=q,
                model=embedding_model,
            )
    except CircuitBreakerError:
        logger.warning("OpenAI circuit open, KB search unavailable", query=q)
        return {
            "query": q,
            "results": [],
            "count": 0,
            "error": "AI service temporarily unavailable",
        }
    embedding = embedding_resp.data[0].embedding

    results = await db.search_knowledge_base(
        pool,
        embedding=embedding,
        customer_tier=customer_tier,
        category=category,
        max_results=limit,
    )

    return {
        "query": q,
        "results": [dict(r) for r in results] if results else [],
        "count": len(results) if results else 0,
    }


@app.post("/knowledge-base/ingest", status_code=201)
@limiter.limit(strict_limit)
async def ingest_knowledge_base(
    request: Request,
    body: KBArticleRequest,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
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
