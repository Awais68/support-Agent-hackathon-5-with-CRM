"""FastAPI application for TechFlow CRM Digital FTE."""

import os
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import FastAPI, Depends, HTTPException, Request, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, EmailStr
from openai import AsyncOpenAI
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from kafka_client import KafkaProducerClient
from database import queries as db
from api.websocket_manager import WebSocketManager
from channels.web_form_handler import WebFormHandler, WebFormSubmission
from channels.whatsapp_handler import WhatsAppHandler

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
        raise HTTPException(status_code=500, detail="Database not initialized")
    return request.app.state.db_pool


async def get_kafka(request: Request) -> KafkaProducerClient:
    """Get Kafka producer from request state."""
    if not request.app.state.kafka_producer:
        raise HTTPException(status_code=500, detail="Kafka not initialized")
    return request.app.state.kafka_producer


async def get_openai(request: Request) -> AsyncOpenAI:
    """Get OpenAI client from request state."""
    if not request.app.state.openai_client:
        raise HTTPException(status_code=500, detail="OpenAI client not initialized")
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
    app.state.db_pool = await asyncpg.create_pool(db_url)
    await db.register_pgvector_codec(app.state.db_pool)
    logger.info("Database pool initialized")

    # Initialize Kafka
    kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    app.state.kafka_producer = KafkaProducerClient(kafka_bootstrap)
    await app.state.kafka_producer.start()
    logger.info("Kafka producer initialized")

    # Initialize OpenAI
    app.state.openai_client = AsyncOpenAI()

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


# Health check endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check(pool: asyncpg.Pool = Depends(get_db)) -> HealthResponse:
    """Health check endpoint."""
    try:
        # Check database
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_status = "ok"
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        db_status = "error"

    return HealthResponse(
        status="healthy" if db_status == "ok" else "degraded",
        db=db_status,
        kafka="ok",  # Kafka health check would require additional setup
    )


# Prometheus metrics endpoint
@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
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
async def create_ticket(
    request: CreateTicketRequest,
    pool: asyncpg.Pool = Depends(get_db),
) -> CreateTicketResponse:
    """Create a new support ticket."""
    try:
        ticket = await db.create_ticket(
            pool,
            customer_email=request.email,
            subject=request.subject,
            category=request.category,
            priority=request.priority,
            channel="api",
            initial_message=request.message,
        )

        tid = str(ticket["id"])
        await ws_manager.broadcast_ticket_update(
            tid,
            {
                "ticket_id": tid,
                "ticket_number": ticket["ticket_number"],
                "status": ticket["status"],
                "subject": request.subject,
                "customer_email": request.email,
                "created_at": ticket["created_at"].isoformat(),
            },
        )

        return CreateTicketResponse(
            ticket_number=ticket["ticket_number"],
            ticket_id=tid,
            status=ticket["status"],
            created_at=ticket["created_at"].isoformat(),
        )
    except Exception as e:
        logger.error("Error creating ticket", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create ticket")


@app.get("/tickets")
async def list_tickets(
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
    """List all tickets with pagination."""
    try:
        tickets, total = await db.list_tickets(pool, status=status, limit=limit, offset=offset)

        return {
            "tickets": [dict(t) for t in tickets],
            "total": total,
            "page": offset // limit + 1,
            "limit": limit,
        }
    except Exception as e:
        logger.error("Error listing tickets", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to list tickets")


@app.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: UUID,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
    """Get full ticket details."""
    try:
        ticket = await db.get_ticket(pool, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        messages = await db.get_ticket_messages(pool, ticket_id)
        agent_runs = await db.get_agent_runs(pool, ticket_id)

        return {
            **dict(ticket),
            "messages": [dict(m) for m in messages],
            "agent_runs": [dict(ar) for ar in agent_runs],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting ticket", error=str(e), ticket_id=str(ticket_id))
        raise HTTPException(status_code=500, detail="Failed to get ticket")


@app.patch("/tickets/{ticket_id}/status")
async def update_ticket_status(
    ticket_id: UUID,
    request: UpdateStatusRequest,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
    """Update ticket status."""
    try:
        ticket = await db.update_ticket_status(pool, ticket_id, request.status)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        await ws_manager.broadcast_ticket_update(str(ticket_id), dict(ticket))

        return dict(ticket)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating ticket status", error=str(e), ticket_id=str(ticket_id))
        raise HTTPException(status_code=500, detail="Failed to update ticket")


@app.get("/tickets/{ticket_id}/messages")
async def get_ticket_messages(
    ticket_id: UUID,
    limit: int = 50,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
    """Get messages for a ticket."""
    try:
        messages = await db.get_ticket_messages(pool, ticket_id, limit=limit)

        return {
            "ticket_id": str(ticket_id),
            "messages": [dict(m) for m in messages],
            "count": len(messages),
        }
    except Exception as e:
        logger.error("Error getting ticket messages", error=str(e), ticket_id=str(ticket_id))
        raise HTTPException(status_code=500, detail="Failed to get messages")


@app.post("/tickets/{ticket_id}/reply", status_code=201)
async def reply_to_ticket(
    ticket_id: UUID,
    request: ReplyRequest,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
    """Send a reply to a ticket."""
    try:
        # Get ticket to verify it exists
        ticket = await db.get_ticket(pool, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        # Add message
        message = await db.add_message(
            pool,
            ticket_id=ticket_id,
            customer_id=UUID(ticket["customer_id"]),
            direction="outbound",
            content=request.message,
            channel=ticket["channel"],
        )

        await ws_manager.broadcast_new_message(
            str(ticket_id),
            {
                "message_id": str(message["id"]),
                "content": request.message,
                "direction": "outbound",
                "created_at": message["created_at"].isoformat(),
            },
        )

        return {
            "message_id": str(message["id"]),
            "sent_at": message["created_at"].isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error replying to ticket", error=str(e), ticket_id=str(ticket_id))
        raise HTTPException(status_code=500, detail="Failed to send reply")


# Webhook endpoints
@app.post("/webhooks/whatsapp")
async def webhook_whatsapp(
    request: Request,
    kafka_producer: KafkaProducerClient = Depends(get_kafka),
) -> Dict[str, str]:
    """WhatsApp webhook from Twilio."""
    try:
        form_data = await request.form()
        handler = WhatsAppHandler(kafka_producer)

        # Validate webhook
        body = await request.body()
        signature = request.headers.get("X-Twilio-Signature", "")
        if not handler.validate_webhook(body.decode(), signature):
            logger.warning("Invalid WhatsApp webhook signature")
            # Still process for now, but log it

        # Parse and handle message
        message_data = await handler.parse_webhook(dict(form_data))
        if message_data:
            await handler.handle_incoming_message(
                from_number=message_data["from_number"],
                sender_name=message_data["sender_name"],
                message_body=message_data["message_body"],
                media_url=message_data.get("media_url"),
            )

        return {"status": "received"}
    except Exception as e:
        logger.error("Error processing WhatsApp webhook", error=str(e))
        return {"status": "error"}


@app.post("/webhooks/webform", response_model=Dict[str, Any], status_code=201)
async def webhook_webform(
    request: WebFormSubmission,
    pool: asyncpg.Pool = Depends(get_db),
    kafka_producer: KafkaProducerClient = Depends(get_kafka),
) -> Dict[str, Any]:
    """Web form submission endpoint."""
    try:
        handler = WebFormHandler(kafka_producer)

        # Process submission
        result = await handler.process_submission(request)

        # Create ticket entry
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
    except Exception as e:
        logger.error("Error processing web form submission", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid form submission")


# Customer endpoints
@app.get("/customers/{email}/history")
async def get_customer_history(
    email: str,
    limit: int = 10,
    include_resolved: bool = True,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
    """Get customer history."""
    try:
        history = await db.get_customer_history(pool, email, limit=limit, include_resolved=include_resolved)

        return {
            "email": email,
            "tickets": [dict(h) for h in history],
            "count": len(history),
        }
    except Exception as e:
        logger.error("Error getting customer history", error=str(e), email=email)
        raise HTTPException(status_code=500, detail="Failed to get customer history")


# Metrics endpoints
@app.get("/metrics/summary")
async def get_metrics_summary(
    hours: int = 24,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
    """Get metrics summary for the last N hours."""
    try:
        metrics = await db.get_metrics_summary(pool, hours=hours)

        return {
            "period_hours": hours,
            "metrics": [dict(m) for m in metrics],
            "count": len(metrics),
        }
    except Exception as e:
        logger.error("Error getting metrics summary", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to get metrics")


@app.get("/metrics/dashboard", response_model=DashboardMetricsResponse)
async def get_dashboard_metrics(
    pool: asyncpg.Pool = Depends(get_db),
) -> DashboardMetricsResponse:
    """Get aggregated metrics for dashboard."""
    try:
        metrics = await db.get_dashboard_metrics(pool)

        return DashboardMetricsResponse(**metrics)
    except Exception as e:
        logger.error("Error getting dashboard metrics", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to get dashboard metrics")


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
    try:
        # Get embedding for the query
        embedding_resp = await openai_client.embeddings.create(
            input=q,
            model="text-embedding-3-small",
        )
        embedding = embedding_resp.data[0].embedding

        # Search database using pgvector
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
    except Exception as e:
        logger.error("Error searching knowledge base", error=str(e), query=q)
        raise HTTPException(status_code=500, detail="Failed to search knowledge base")


@app.post("/knowledge-base/ingest", status_code=201)
async def ingest_knowledge_base(
    request: KBArticleRequest,
    pool: asyncpg.Pool = Depends(get_db),
) -> Dict[str, Any]:
    """Ingest a knowledge base article."""
    try:
        # In production, would embed content and insert
        article = await db.add_knowledge_base_article(
            pool,
            title=request.title,
            content=request.content,
            embedding=request.embedding or [],
            category=request.category,
            tags=request.tags,
        )

        return {
            "id": str(article["id"]),
            "title": article["title"],
            "embedded": len(request.embedding) > 0,
        }
    except Exception as e:
        logger.error("Error ingesting knowledge base article", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to ingest article")


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
