"""Async database queries for TechFlow CRM Digital FTE."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional, List, Dict, Any
from uuid import UUID
import asyncpg
import structlog
from exceptions import sanitize_error_message


FUZZY_THRESHOLD_DEFAULT = 0.3
EMBEDDING_DIM = 1536


@dataclass
class FuzzyMatchResult:
    customer: Optional[Dict[str, Any]] = None
    match_field: Optional[str] = None  # 'email' or 'name'
    similarity: float = 0.0

logger = structlog.get_logger(__name__)


async def register_pgvector_codec(pool: asyncpg.Pool) -> None:
    """Register pgvector codec with asyncpg pool."""
    async with pool.acquire() as conn:
        await conn.set_type_codec("vector", encoder=str, decoder=list, schema="public")


# Customers queries
async def fuzzy_search_customers(
    pool: asyncpg.Pool,
    search_term: str,
    search_field: str = "email",
    threshold: float = FUZZY_THRESHOLD_DEFAULT,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Fuzzy search customers by name or email using pg_trgm similarity().

    Returns results above the threshold, sorted by descending similarity.
    search_field must be 'email' or 'name'.
    """
    valid = {"email", "name"}
    if search_field not in valid:
        raise ValueError(f"search_field must be one of {valid}, got '{search_field}'")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, email, name, company, tier, created_at, updated_at, metadata,
                   similarity({search_field}, $1) AS similarity
            FROM customers
            WHERE {search_field} % $1
              AND similarity({search_field}, $1) >= $2
            ORDER BY similarity DESC
            LIMIT $3
            """,
            search_term,
            threshold,
            limit,
        )
        return [dict(row) for row in rows]


async def get_customer(pool: asyncpg.Pool, email: str) -> Optional[Dict[str, Any]]:
    """Get customer by email."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, name, company, tier, created_at, updated_at, metadata FROM customers WHERE email = $1",
            email,
        )
        return dict(row) if row else None


async def create_customer(
    pool: asyncpg.Pool, email: str, name: str, company: str = "", tier: str = "starter"
) -> Dict[str, Any]:
    """Create a new customer."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO customers (email, name, company, tier)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (email) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id, email, name, company, tier, created_at, updated_at
            """,
            email,
            name,
            company,
            tier,
        )
        return dict(row)


async def get_customer_by_identifier(
    pool: asyncpg.Pool,
    identifier_type: str,
    identifier_value: str,
) -> Optional[Dict[str, Any]]:
    """Get customer by any identifier type (email, phone, web_session)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT c.id, c.email, c.name, c.company, c.tier, c.created_at, c.updated_at, c.metadata
            FROM customers c
            JOIN customer_identifiers ci ON ci.customer_id = c.id
            WHERE ci.identifier_type = $1 AND ci.identifier_value = $2
            """,
            identifier_type,
            identifier_value,
        )
        return dict(row) if row else None


async def add_customer_identifier(
    pool: asyncpg.Pool,
    customer_id: UUID,
    identifier_type: str,
    identifier_value: str,
) -> Dict[str, Any]:
    """Add a new identifier to an existing customer."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
            VALUES ($1, $2, $3)
            ON CONFLICT (identifier_type, identifier_value) DO NOTHING
            RETURNING id, customer_id, identifier_type, identifier_value, created_at
            """,
            customer_id,
            identifier_type,
            identifier_value,
        )
        return dict(row) if row else {"error": "Identifier already exists"}


async def link_identifiers(
    pool: asyncpg.Pool,
    customer_id: UUID,
    identifiers: List[tuple[str, str]],
) -> None:
    """Link multiple identifier (type, value) pairs to a customer, skipping conflicts."""
    async with pool.acquire() as conn:
        for id_type, id_value in identifiers:
            await conn.execute(
                """
                INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
                VALUES ($1, $2, $3)
                ON CONFLICT (identifier_type, identifier_value) DO NOTHING
                """,
                customer_id,
                id_type,
                id_value,
            )


async def get_customer_or_create_by_identifier(
    pool: asyncpg.Pool,
    identifier_type: str,
    identifier_value: str,
    name: str = "Customer",
    tier: str = "starter",
    fuzzy_threshold: float = FUZZY_THRESHOLD_DEFAULT,
) -> Dict[str, Any]:
    """Get a customer by identifier, with fuzzy fallback on email/name, or create new."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            # ── Step 1: exact match via identifier table ──
            existing = await conn.fetchrow(
                """
                SELECT c.id, c.email, c.name, c.company, c.tier, c.created_at, c.updated_at, c.metadata
                FROM customers c
                JOIN customer_identifiers ci ON ci.customer_id = c.id
                WHERE ci.identifier_type = $1 AND ci.identifier_value = $2
                """,
                identifier_type,
                identifier_value,
            )
            if existing:
                return dict(existing)

            fuzzy_matched = False

            # ── Step 2: fuzzy fallback on email ──
            if identifier_type == "email":
                fuzzy_row = await conn.fetchrow(
                    """
                    SELECT id, email, name, company, tier, created_at, updated_at, metadata,
                           similarity(email, $1) AS sim
                    FROM customers
                    WHERE email % $1
                    ORDER BY sim DESC
                    LIMIT 1
                    """,
                    identifier_value,
                )
                if fuzzy_row and fuzzy_row["sim"] >= fuzzy_threshold:
                    await conn.execute(
                        """
                        INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (identifier_type, identifier_value) DO NOTHING
                        """,
                        fuzzy_row["id"],
                        identifier_type,
                        identifier_value,
                    )
                    fuzzy_matched = True
                    existing = fuzzy_row

            # ── Step 3: fuzzy fallback on name ──
            if not fuzzy_matched and name and name != "Customer":
                fuzzy_name_row = await conn.fetchrow(
                    """
                    SELECT id, email, name, company, tier, created_at, updated_at, metadata,
                           similarity(name, $1) AS sim
                    FROM customers
                    WHERE name % $1
                    ORDER BY sim DESC
                    LIMIT 1
                    """,
                    name,
                )
                if fuzzy_name_row and fuzzy_name_row["sim"] >= fuzzy_threshold:
                    await conn.execute(
                        """
                        INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (identifier_type, identifier_value) DO NOTHING
                        """,
                        fuzzy_name_row["id"],
                        identifier_type,
                        identifier_value,
                    )
                    fuzzy_matched = True
                    existing = fuzzy_name_row

            if fuzzy_matched:
                return dict(existing)

            # ── Step 4: create new customer + identifier ──
            if identifier_type == "email":
                customer_email = identifier_value
            else:
                customer_email = f"{identifier_value}@{identifier_type}.techflow.io"

            row = await conn.fetchrow(
                """
                INSERT INTO customers (email, name, tier)
                VALUES ($1, $2, $3)
                RETURNING id, email, name, company, tier, created_at, updated_at, metadata
                """,
                customer_email,
                name,
                tier,
            )
            customer = dict(row)

            await conn.execute(
                """
                INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
                VALUES ($1, $2, $3)
                ON CONFLICT (identifier_type, identifier_value) DO NOTHING
                """,
                customer["id"],
                identifier_type,
                identifier_value,
            )

            if identifier_type != "email":
                await conn.execute(
                    """
                    INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
                    VALUES ($1, 'email', $2)
                    ON CONFLICT (identifier_type, identifier_value) DO NOTHING
                    """,
                    customer["id"],
                    customer_email,
                )

            return customer


async def find_customer_by_name_email(
    pool: asyncpg.Pool,
    email: str,
    name: Optional[str] = None,
    fuzzy_threshold: float = FUZZY_THRESHOLD_DEFAULT,
) -> Optional[Dict[str, Any]]:
    """
    Look up a customer by email (exact first, then fuzzy), optionally by name.
    Returns the best match or None.
    """
    async with pool.acquire() as conn:
        # Exact email match
        row = await conn.fetchrow(
            "SELECT id, email, name, company, tier, created_at, updated_at, metadata FROM customers WHERE email = $1",
            email,
        )
        if row:
            return dict(row)

        # Fuzzy email match
        fuzzy_row = await conn.fetchrow(
            """
            SELECT id, email, name, company, tier, created_at, updated_at, metadata,
                   similarity(email, $1) AS sim
            FROM customers
            WHERE email % $1
            ORDER BY sim DESC
            LIMIT 1
            """,
            email,
        )
        if fuzzy_row and fuzzy_row["sim"] >= fuzzy_threshold:
            return dict(fuzzy_row)

        # Fuzzy name match if provided
        if name:
            name_row = await conn.fetchrow(
                """
                SELECT id, email, name, company, tier, created_at, updated_at, metadata,
                       similarity(name, $1) AS sim
                FROM customers
                WHERE name % $1
                ORDER BY sim DESC
                LIMIT 1
                """,
                name,
            )
            if name_row and name_row["sim"] >= fuzzy_threshold:
                return dict(name_row)

        return None


async def get_customer_history(
    pool: asyncpg.Pool, email: str, limit: int = 10, include_resolved: bool = True
) -> List[Dict[str, Any]]:
    """Get customer's ticket history. Resolves customer via identifier table for cross-channel support."""
    status_filter = "(status IN ('open', 'in_progress', 'escalated', 'closed'))" if not include_resolved else "(1=1)"

    async with pool.acquire() as conn:
        customer_id = await conn.fetchval(
            """
            SELECT customer_id FROM customer_identifiers
            WHERE identifier_type = 'email' AND identifier_value = $1
            """,
            email,
        )
        if not customer_id:
            return []

        rows = await conn.fetch(
            f"""
            SELECT
                t.id, t.ticket_number, t.subject, t.category, t.priority, t.status,
                t.channel, t.created_at, t.updated_at, t.resolved_at,
                COUNT(m.id) AS message_count
            FROM tickets t
            LEFT JOIN messages m ON t.id = m.ticket_id
            WHERE t.customer_id = $1
            AND {status_filter}
            GROUP BY t.id
            ORDER BY t.created_at DESC
            LIMIT $2
            """,
            customer_id,
            limit,
        )
        return [dict(row) for row in rows]


# Tickets queries
async def create_ticket(
    pool: asyncpg.Pool,
    customer_email: str,
    subject: str,
    category: str = "general",
    priority: str = "medium",
    channel: str = "email",
    initial_message: str = "",
    customer_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """Create a new ticket with auto-generated ticket number."""
    ticket_number = f"TKT-{datetime.now().strftime('%Y%m%d')}-{int(datetime.now().timestamp() * 1000) % 10000:04d}"

    async with pool.acquire() as conn:
        async with conn.transaction():
            if not customer_id:
                customer_row = await conn.fetchrow(
                    "SELECT id FROM customers WHERE email = $1", customer_email
                )
                if customer_row:
                    customer_id = customer_row["id"]
                else:
                    fuzzy_row = await conn.fetchrow(
                        """
                        SELECT id, similarity(email, $1) AS sim
                        FROM customers
                        WHERE email % $1
                        ORDER BY sim DESC
                        LIMIT 1
                        """,
                        customer_email,
                    )
                    if fuzzy_row and fuzzy_row["sim"] >= FUZZY_THRESHOLD_DEFAULT:
                        customer_id = fuzzy_row["id"]
                    else:
                        customer_row = await conn.fetchrow(
                            "INSERT INTO customers (email, name, tier) VALUES ($1, $2, $3) RETURNING id",
                            customer_email,
                            customer_email.split("@")[0],
                            "starter",
                        )
                        customer_id = customer_row["id"]

            # Register the email identifier so customer history resolves
            # regardless of which channel created the ticket.
            await conn.execute(
                """
                INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
                VALUES ($1, 'email', $2)
                ON CONFLICT (identifier_type, identifier_value) DO NOTHING
                """,
                customer_id,
                customer_email,
            )

            # Create ticket
            ticket_row = await conn.fetchrow(
                """
                INSERT INTO tickets (ticket_number, customer_id, subject, category, priority, channel)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, ticket_number, customer_id, status, created_at
                """,
                ticket_number,
                customer_id,
                subject,
                category,
                priority,
                channel,
            )

            # Add initial message if provided
            if initial_message:
                await conn.execute(
                    """
                    INSERT INTO messages (ticket_id, customer_id, direction, content, channel)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    ticket_row["id"],
                    customer_id,
                    "inbound",
                    initial_message,
                    channel,
                )

            return dict(ticket_row)


async def get_ticket(pool: asyncpg.Pool, ticket_id: UUID) -> Optional[Dict[str, Any]]:
    """Get ticket with full details."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                t.id, t.ticket_number, t.customer_id, t.subject, t.category, t.priority,
                t.status, t.channel, t.created_at, t.updated_at, t.resolved_at, t.assigned_to,
                c.email AS customer_email, c.name AS customer_name, c.tier
            FROM tickets t
            JOIN customers c ON t.customer_id = c.id
            WHERE t.id = $1
            """,
            ticket_id,
        )
        return dict(row) if row else None


async def update_ticket_status(
    pool: asyncpg.Pool, ticket_id: UUID, status: str
) -> Optional[Dict[str, Any]]:
    """Update ticket status."""
    resolved_at = datetime.now(UTC).replace(tzinfo=None) if status == "resolved" else None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE tickets
            SET status = $1, updated_at = CURRENT_TIMESTAMP, resolved_at = $2
            WHERE id = $3
            RETURNING id, ticket_number, status, updated_at
            """,
            status,
            resolved_at,
            ticket_id,
        )
        return dict(row) if row else None


async def list_tickets(
    pool: asyncpg.Pool,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
) -> tuple[List[Dict[str, Any]], int]:
    """List tickets with pagination."""
    async with pool.acquire() as conn:
        # Get total count
        count_query = "SELECT COUNT(*) as count FROM tickets"
        if status:
            count_query += " WHERE status = $1"
            total = await conn.fetchval(count_query, status)
        else:
            total = await conn.fetchval(count_query)

        # Get tickets
        query = """
            SELECT
                t.id, t.ticket_number, t.subject, t.category, t.priority, t.status,
                t.channel, t.created_at, t.updated_at,
                c.email AS customer_email
            FROM tickets t
            JOIN customers c ON t.customer_id = c.id
        """
        if status:
            query += " WHERE t.status = $1 ORDER BY t.created_at DESC LIMIT $2 OFFSET $3"
            rows = await conn.fetch(query, status, limit, offset)
        else:
            query += " ORDER BY t.created_at DESC LIMIT $1 OFFSET $2"
            rows = await conn.fetch(query, limit, offset)

        return [dict(row) for row in rows], total


# Messages queries
async def add_message(
    pool: asyncpg.Pool,
    ticket_id: UUID,
    customer_id: UUID,
    direction: str,
    content: str,
    channel: str,
) -> Dict[str, Any]:
    """Add a message to a ticket."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO messages (ticket_id, customer_id, direction, content, channel)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, ticket_id, direction, content, channel, created_at
            """,
            ticket_id,
            customer_id,
            direction,
            content,
            channel,
        )
        return dict(row)


async def get_ticket_messages(
    pool: asyncpg.Pool, ticket_id: UUID, limit: int = 50
) -> List[Dict[str, Any]]:
    """Get messages for a ticket."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, direction, content, channel, created_at, sentiment_score, metadata
            FROM messages
            WHERE ticket_id = $1
            ORDER BY created_at ASC
            LIMIT $2
            """,
            ticket_id,
            limit,
        )
        return [dict(row) for row in rows]


async def get_message_sentiment_history(
    pool: asyncpg.Pool, ticket_id: UUID, limit: int = 10
) -> List[Dict[str, Any]]:
    """Get recent inbound message sentiment scores for a ticket, ordered by time."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, sentiment_score, created_at, metadata->>'emotion' AS emotion,
                   (metadata->>'urgency_score')::FLOAT AS urgency_score
            FROM messages
            WHERE ticket_id = $1 AND direction = 'inbound' AND sentiment_score IS NOT NULL
            ORDER BY created_at DESC
            LIMIT $2
            """,
            ticket_id,
            limit,
        )
        return [dict(row) for row in rows]


async def update_message_emotion_data(
    pool: asyncpg.Pool,
    message_id: UUID,
    sentiment_score: float,
    emotion: str,
    urgency_score: float,
    aspect_scores: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Update a message with full sentiment/emotion/urgency/aspect data in metadata."""
    async with pool.acquire() as conn:
        metadata_update = {
            "emotion": emotion,
            "urgency_score": urgency_score,
            "aspect_scores": aspect_scores or {},
        }
        row = await conn.fetchrow(
            """
            UPDATE messages
            SET sentiment_score = $1,
                metadata = metadata || $2::jsonb
            WHERE id = $3
            RETURNING id, ticket_id, sentiment_score
            """,
            sentiment_score,
            metadata_update,
            message_id,
        )
        return dict(row) if row else None


# Knowledge base queries
async def search_knowledge_base(
    pool: asyncpg.Pool,
    embedding: List[float],
    customer_tier: str = "starter",
    category: Optional[str] = None,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    """Search knowledge base by vector similarity."""
    async with pool.acquire() as conn:
        # Build query with tier filtering
        where_clause = "(kb.tier = $3 OR kb.tier = 'all')"
        params = [embedding, max_results, customer_tier]

        if category:
            where_clause += " AND kb.category = $4"
            params.append(category)

        rows = await conn.fetch(
            f"""
            SELECT id, title, content, category, tags, source, tier,
                   1 - (embedding <=> $1) AS similarity
            FROM knowledge_base kb
            WHERE {where_clause}
            ORDER BY embedding <=> $1
            LIMIT $2
            """,
            *params,
        )
        return [dict(row) for row in rows]


async def add_knowledge_base_article(
    pool: asyncpg.Pool,
    title: str,
    content: str,
    embedding: List[float],
    category: str = "general",
    tags: List[str] = [],
    source: str = "internal",
    tier: str = "all",
) -> Dict[str, Any]:
    """Add a knowledge base article."""
    # pgvector requires a non-empty vector; use a zero vector when no embedding
    # was provided (e.g. embedding provider unavailable) so ingestion still works.
    if not embedding:
        embedding = [0.0] * EMBEDDING_DIM
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO knowledge_base (title, content, embedding, category, tags, source, tier)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, title, category, tier, created_at
            """,
            title,
            content,
            embedding,
            category,
            tags,
            source,
            tier,
        )
        return dict(row)


async def update_message_sentiment(
    pool: asyncpg.Pool,
    ticket_id: UUID,
    sentiment_score: float,
) -> Optional[Dict[str, Any]]:
    """Update sentiment score on the latest inbound message for a ticket."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE messages
            SET sentiment_score = $1,
                metadata = metadata || jsonb_build_object('sentiment_score', $1::text)
            WHERE id = (
                SELECT id FROM messages
                WHERE ticket_id = $2 AND direction = 'inbound'
                ORDER BY created_at DESC
                LIMIT 1
            )
            RETURNING id, ticket_id, sentiment_score
            """,
            sentiment_score,
            ticket_id,
        )
        return dict(row) if row else None


# Agent runs queries
async def create_agent_run(
    pool: asyncpg.Pool,
    ticket_id: UUID,
    customer_id: UUID,
    input_message: str,
    output_message: Optional[str] = None,
    tool_calls: List[str] = [],
    status: str = "running",
) -> Dict[str, Any]:
    """Record an agent run."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO agent_runs (ticket_id, customer_id, input_message, output_message, tool_calls, status)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, ticket_id, status, created_at
            """,
            ticket_id,
            customer_id,
            input_message,
            output_message,
            tool_calls,
            status,
        )
        return dict(row)


async def complete_agent_run(
    pool: asyncpg.Pool,
    agent_run_id: UUID,
    output_message: str,
    tokens_used: int = 0,
    result: Dict[str, Any] = {},
) -> Dict[str, Any]:
    """Mark agent run as completed."""
    duration_ms = None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE agent_runs
            SET
                status = 'completed',
                output_message = $1,
                tokens_used = $2,
                result = $3,
                completed_at = CURRENT_TIMESTAMP,
                duration_ms = EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - created_at))::INT
            WHERE id = $4
            RETURNING id, ticket_id, status, duration_ms, completed_at
            """,
            output_message,
            tokens_used,
            result,
            agent_run_id,
        )
        return dict(row) if row else {}


async def get_agent_runs(
    pool: asyncpg.Pool, ticket_id: UUID, limit: int = 10
) -> List[Dict[str, Any]]:
    """Get agent runs for a ticket."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, ticket_id, input_message, output_message, status,
                   created_at, completed_at, duration_ms, tokens_used
            FROM agent_runs
            WHERE ticket_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            ticket_id,
            limit,
        )
        return [dict(row) for row in rows]


# Metrics queries
async def record_metric(
    pool: asyncpg.Pool,
    metric_name: str,
    metric_value: float,
    metric_type: str = "gauge",
    labels: Dict[str, str] = {},
) -> None:
    """Record a metric."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO metrics (metric_name, metric_value, metric_type, labels)
            VALUES ($1, $2, $3, $4)
            """,
            metric_name,
            metric_value,
            metric_type,
            labels,
        )


async def get_metrics_summary(
    pool: asyncpg.Pool, hours: int = 24, limit: int = 100
) -> List[Dict[str, Any]]:
    """Get metrics summary for the last N hours."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT metric_name, metric_value, metric_type, labels, timestamp
            FROM metrics
            WHERE timestamp >= $1
            ORDER BY timestamp DESC
            LIMIT $2
            """,
            cutoff,
            limit,
        )
        return [dict(row) for row in rows]


async def get_dashboard_metrics(pool: asyncpg.Pool) -> Dict[str, Any]:
    """Get aggregated metrics for dashboard."""
    async with pool.acquire() as conn:
        # Tickets by status
        status_counts = await conn.fetch(
            "SELECT status, COUNT(*) as count FROM tickets GROUP BY status"
        )

        # Avg resolution time
        avg_resolution = await conn.fetchval(
            """
            SELECT AVG(EXTRACT(EPOCH FROM (resolved_at - created_at))/3600) as avg_hours
            FROM tickets
            WHERE resolved_at IS NOT NULL
            """
        )

        # Escalation rate
        total_tickets = await conn.fetchval("SELECT COUNT(*) FROM tickets")
        escalated = await conn.fetchval(
            "SELECT COUNT(*) FROM tickets WHERE status = 'escalated'"
        )
        escalation_rate = (escalated / total_tickets * 100) if total_tickets > 0 else 0

        # Tickets by channel
        channel_counts = await conn.fetch(
            "SELECT channel, COUNT(*) as count FROM tickets GROUP BY channel"
        )

        return {
            "status_counts": {row["status"]: row["count"] for row in status_counts},
            "avg_resolution_hours": float(avg_resolution) if avg_resolution else 0,
            "escalation_rate_percent": float(escalation_rate),
            "channel_counts": {row["channel"]: row["count"] for row in channel_counts},
            "total_tickets": total_tickets,
        }
