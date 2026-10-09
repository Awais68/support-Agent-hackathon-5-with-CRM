"""Async database queries for TechFlow CRM Digital FTE."""

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import structlog

FUZZY_THRESHOLD_DEFAULT = 0.3

# An email is linked to an existing customer only on an EXACT match after
# normalize_email(). A near-miss is a different mailbox: alice1@acmecorp.com
# is edit distance 1 from alice@acmecorp.com and belongs to whoever registered
# it, so linking it handed a stranger Alice's ticket history (AUDIT S2). Fuzzy
# email (same domain + small local-part edit distance) is only a review hint on
# the new customer, never a link.
EMAIL_LOCAL_MAX_DISTANCE = int(os.getenv("EMAIL_FUZZY_MAX_DISTANCE", "2"))

# Name similarity is NOT an identity signal. Measured with pg_trgm on real pairs:
#   'Maria Garcia' / 'Mario Garcia'  = 0.786  (different people)
#   'Sarah Chen'   / 'Sarah Cheng'   = 0.769  (different people)
#   'Muhammad Ali' / 'Muhammad Alam' = 0.688  (different people)
#   'John Smith'   / 'Jon Smith'     = 0.615  (same person, typo)
# Every genuine typo scores BELOW several false matches, so no threshold can
# separate them. A name match therefore never auto-links an identifier to an
# existing customer (that would expose one person's ticket history to another);
# it only records a review flag. Read-only lookups may return a name match, but
# only when corroborated by domain/company and when it is unambiguous.
NAME_FUZZY_THRESHOLD = float(os.getenv("NAME_FUZZY_THRESHOLD", "0.6"))
NAME_FUZZY_AMBIGUITY_MARGIN = float(os.getenv("NAME_FUZZY_AMBIGUITY_MARGIN", "0.08"))
EMBEDDING_DIM = 1536


def normalize_email(email: str) -> str:
    """Canonical form for identity matching: trimmed and lower-cased."""
    return (email or "").strip().lower()


@dataclass
class FuzzyMatchResult:
    customer: dict[str, Any] | None = None
    match_field: str | None = None  # 'email' or 'name'
    similarity: float = 0.0


logger = structlog.get_logger(__name__)


async def init_pgvector_connection(conn: asyncpg.Connection) -> None:
    """asyncpg ``init`` callback: register the pgvector codec on a connection.

    Must be passed to ``asyncpg.create_pool(init=...)`` so *every* pooled
    connection gets the codec, not just the one that happens to be acquired.
    """
    await conn.set_type_codec("vector", encoder=str, decoder=list, schema="public")


async def register_pgvector_codec(pool: asyncpg.Pool) -> None:
    """Back-compat helper for callers that already own a pool.

    Only affects the single connection it acquires; prefer ``init_pgvector_connection``.
    """
    async with pool.acquire() as conn:
        await init_pgvector_connection(conn)


# Customers queries
async def fuzzy_search_customers(
    pool: asyncpg.Pool,
    search_term: str,
    search_field: str = "email",
    threshold: float = FUZZY_THRESHOLD_DEFAULT,
    limit: int = 5,
) -> list[dict[str, Any]]:
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


async def get_customer(pool: asyncpg.Pool, email: str) -> dict[str, Any] | None:
    """Get customer by email."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, name, company, tier, created_at, updated_at, metadata FROM customers WHERE email = $1",
            email,
        )
        return dict(row) if row else None


async def create_customer(
    pool: asyncpg.Pool, email: str, name: str, company: str = "", tier: str = "starter"
) -> dict[str, Any]:
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
) -> dict[str, Any] | None:
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
) -> dict[str, Any]:
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
    identifiers: list[tuple[str, str]],
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
) -> dict[str, Any]:
    """Get a customer by exact identifier, or create a new one.

    Emails match only exactly after normalize_email(). Similar emails or names
    are recorded as a review hint on the NEW customer, never linked.
    """
    if identifier_type == "email":
        identifier_value = normalize_email(identifier_value)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # ── Step 1: exact match via identifier table ──
            existing = await conn.fetchrow(
                """
                SELECT c.id, c.email, c.name, c.company, c.tier, c.created_at, c.updated_at, c.metadata
                FROM customers c
                JOIN customer_identifiers ci ON ci.customer_id = c.id
                WHERE ci.identifier_type = $1
                  AND (ci.identifier_value = $2
                       OR ($1 = 'email' AND lower(ci.identifier_value) = $2))
                LIMIT 1
                """,
                identifier_type,
                identifier_value,
            )
            if existing:
                return dict(existing)

            # ── Step 1b: exact email on the customer row (no identifier yet) ──
            if identifier_type == "email":
                existing = await conn.fetchrow(
                    """
                    SELECT id, email, name, company, tier, created_at, updated_at, metadata
                    FROM customers
                    WHERE lower(email) = $1
                    LIMIT 1
                    """,
                    identifier_value,
                )
                if existing:
                    await conn.execute(
                        """
                        INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
                        VALUES ($1, 'email', $2)
                        ON CONFLICT (identifier_type, identifier_value) DO NOTHING
                        """,
                        existing["id"],
                        identifier_value,
                    )
                    return dict(existing)

            # ── Steps 2-3: similar email or name → review hint only, never a link ──
            duplicate_hint: dict[str, Any] = {}
            candidate = None
            if identifier_type == "email":
                candidate = await conn.fetchrow(
                    """
                    SELECT id, name, similarity(email, $1) AS sim
                    FROM customers
                    WHERE email % $1
                      AND split_part(lower(email), '@', 2) = split_part($1, '@', 2)
                      AND levenshtein(
                            split_part(lower(email), '@', 1), split_part($1, '@', 1)
                          ) <= $2
                      AND similarity(email, $1) >= $3
                    ORDER BY sim DESC
                    LIMIT 1
                    """,
                    identifier_value,
                    EMAIL_LOCAL_MAX_DISTANCE,
                    fuzzy_threshold,
                )
            # See NAME_FUZZY_THRESHOLD: a name match cannot tell a typo apart from a
            # different person either.
            if candidate is None and name and name != "Customer":
                candidate = await conn.fetchrow(
                    """
                    SELECT id, name, similarity(name, $1) AS sim
                    FROM customers
                    WHERE name % $1
                      AND similarity(name, $1) >= $2
                    ORDER BY sim DESC
                    LIMIT 1
                    """,
                    name,
                    NAME_FUZZY_THRESHOLD,
                )
            if candidate:
                duplicate_hint = {
                    "possible_duplicate_of": str(candidate["id"]),
                    "possible_duplicate_name": candidate["name"],
                    "possible_duplicate_similarity": float(candidate["sim"]),
                    "needs_identity_review": True,
                }
                logger.info(
                    "possible_duplicate_customer",
                    identifier_type=identifier_type,
                    candidate_id=str(candidate["id"]),
                    similarity=float(candidate["sim"]),
                )

            # ── Step 4: create new customer + identifier ──
            if identifier_type == "email":
                customer_email = identifier_value
            else:
                customer_email = f"{identifier_value}@{identifier_type}.techflow.io"

            row = await conn.fetchrow(
                """
                INSERT INTO customers (email, name, tier, metadata)
                VALUES ($1, $2, $3, $4::jsonb)
                RETURNING id, email, name, company, tier, created_at, updated_at, metadata
                """,
                customer_email,
                name,
                tier,
                json.dumps(duplicate_hint),
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
    name: str | None = None,
    company: str | None = None,
) -> dict[str, Any] | None:
    """Look up a customer by exact (normalized) email.

    ``name`` and ``company`` are accepted for callers but never used to pick a
    customer: a similar email or name belongs to a different person as often
    as not (AUDIT S2). Similar customers surface through the review queue
    instead. The result carries ``match_type = "exact_email"``.
    """
    normalized = normalize_email(email)
    if not normalized:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, email, name, company, tier, created_at, updated_at, metadata
            FROM customers
            WHERE lower(email) = $1
               OR id = (
                    SELECT customer_id FROM customer_identifiers
                    WHERE identifier_type = 'email' AND lower(identifier_value) = $1
                    LIMIT 1
               )
            LIMIT 1
            """,
            normalized,
        )
        if not row:
            return None
        match = dict(row)
        match["match_type"] = "exact_email"
        return match


async def get_customer_history(
    pool: asyncpg.Pool, email: str, limit: int = 10, include_resolved: bool = True
) -> list[dict[str, Any]]:
    """Get customer's ticket history. Resolves customer via identifier table for cross-channel support."""
    async with pool.acquire() as conn:
        customer_id = await conn.fetchval(
            """
            SELECT customer_id FROM customer_identifiers
            WHERE identifier_type = 'email' AND lower(identifier_value) = $1
            LIMIT 1
            """,
            normalize_email(email),
        )
    if not customer_id:
        return []
    return await get_customer_history_by_id(pool, customer_id, limit, include_resolved)


async def get_customer_history_by_id(
    pool: asyncpg.Pool, customer_id: UUID, limit: int = 10, include_resolved: bool = True
) -> list[dict[str, Any]]:
    """Ticket history for one customer id (the agent's tools are bound to it)."""
    status_filter = (
        "(status IN ('open', 'in_progress', 'escalated', 'closed'))"
        if not include_resolved
        else "(1=1)"
    )
    async with pool.acquire() as conn:
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
    customer_id: UUID | None = None,
) -> dict[str, Any]:
    """Create a new ticket with auto-generated ticket number."""
    customer_email = normalize_email(customer_email)
    max_attempts = 3
    original_customer_id = customer_id

    async with pool.acquire() as conn:
        for attempt in range(max_attempts):
            customer_id = original_customer_id
            try:
                async with conn.transaction():
                    ticket_number = (
                        f"TKT-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
                    )

                    if not customer_id:
                        # Exact normalized email only; a look-alike address is a
                        # different customer (AUDIT S2).
                        customer_row = await conn.fetchrow(
                            """
                            SELECT id FROM customers WHERE lower(email) = $1
                            UNION ALL
                            SELECT customer_id FROM customer_identifiers
                            WHERE identifier_type = 'email' AND lower(identifier_value) = $1
                            LIMIT 1
                            """,
                            customer_email,
                        )
                        if customer_row:
                            customer_id = customer_row["id"]
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
            except asyncpg.UniqueViolationError:
                if attempt == max_attempts - 1:
                    raise


async def get_ticket(pool: asyncpg.Pool, ticket_id: UUID) -> dict[str, Any] | None:
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


async def get_ticket_by_number(pool: asyncpg.Pool, ticket_number: str) -> dict[str, Any] | None:
    """Get ticket by its human-facing TKT-… number.

    The success screen and tracking URL only ever carry the ticket_number, so
    the lookup path has to accept it as well as the internal UUID.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                t.id, t.ticket_number, t.customer_id, t.subject, t.category, t.priority,
                t.status, t.channel, t.created_at, t.updated_at, t.resolved_at, t.assigned_to,
                c.email AS customer_email, c.name AS customer_name, c.tier
            FROM tickets t
            JOIN customers c ON t.customer_id = c.id
            WHERE t.ticket_number = $1
            """,
            ticket_number,
        )
        return dict(row) if row else None


async def update_ticket_status(
    pool: asyncpg.Pool, ticket_id: UUID, status: str
) -> dict[str, Any] | None:
    """Update ticket status."""
    resolved_at = datetime.now(UTC).replace(tzinfo=None) if status == "resolved" else None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE tickets
            SET status = $1,
                updated_at = CURRENT_TIMESTAMP,
                -- Stamp resolved_at on resolution; keep the existing value otherwise
                -- instead of wiping the original resolution time.
                resolved_at = COALESCE($2, resolved_at)
            WHERE id = $3
            RETURNING id, ticket_number, status, updated_at
            """,
            status,
            resolved_at,
            ticket_id,
        )
        return dict(row) if row else None


async def list_tickets(
    pool: asyncpg.Pool, status: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
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
) -> dict[str, Any]:
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
) -> list[dict[str, Any]]:
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
) -> list[dict[str, Any]]:
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
    aspect_scores: dict[str, float] | None = None,
) -> dict[str, Any] | None:
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
            json.dumps(metadata_update),
            message_id,
        )
        return dict(row) if row else None


# Knowledge base queries
# Ticket categories (web form: general/technical/billing/bug/feedback) are not
# the same vocabulary as knowledge_base.category (onboarding/billing/product/
# technical). Filtering on a ticket category with no articles, e.g. "general",
# silently returned nothing. Only categories that exist in the KB filter;
# anything else searches the whole KB.
KB_CATEGORY_ALIASES: dict[str, str] = {
    "technical": "technical",
    "bug": "technical",
    "billing": "billing",
    "onboarding": "onboarding",
    "product": "product",
}


def kb_category_filter(category: str | None) -> str | None:
    """Map a ticket/agent category to a KB category, or None for no filter."""
    if not category:
        return None
    return KB_CATEGORY_ALIASES.get(category.strip().lower())


async def search_knowledge_base(
    pool: asyncpg.Pool,
    embedding: list[float],
    customer_tier: str = "starter",
    category: str | None = None,
    max_results: int = 5,
    embedding_model: str | None = None,
) -> list[dict[str, Any]]:
    """Search knowledge base by vector similarity.

    ``embedding_model`` restricts the search to rows indexed by the same model
    that produced ``embedding``. Vectors from two different models are not
    comparable — cosine distance between them is noise — so without this filter
    a provider switch silently returns near-random articles at plausible-looking
    ranks. Callers should treat an empty result as "degrade to lexical search"
    rather than "no such article".

    ``category`` is mapped with :func:`kb_category_filter`; if the filtered
    search finds nothing, the search is repeated without the category.
    """
    category = kb_category_filter(category)
    rows = await _search_knowledge_base_vector(
        pool, embedding, customer_tier, category, max_results, embedding_model
    )
    if not rows and category:
        rows = await _search_knowledge_base_vector(
            pool, embedding, customer_tier, None, max_results, embedding_model
        )
    return rows


async def _search_knowledge_base_vector(
    pool: asyncpg.Pool,
    embedding: list[float],
    customer_tier: str,
    category: str | None,
    max_results: int,
    embedding_model: str | None,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        # Build query with tier filtering
        where_clause = "(kb.tier = $3 OR kb.tier = 'all')"
        params = [embedding, max_results, customer_tier]

        if category:
            where_clause += f" AND kb.category = ${len(params) + 1}"
            params.append(category)

        if embedding_model:
            where_clause += (
                " AND kb.embedding IS NOT NULL" f" AND kb.embedding_model = ${len(params) + 1}"
            )
            params.append(embedding_model)

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


async def search_knowledge_base_text(
    pool: asyncpg.Pool,
    query: str,
    customer_tier: str = "starter",
    category: str | None = None,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Lexical KB search used when embeddings are unavailable or incomparable.

    Keeps the knowledge base usable when the embedding provider is down, out of
    credits, the circuit breaker is open, or the stored vectors came from a
    different embedding model.

    Matching is per-word, not per-phrase. A real support message reads like
    "my dashboard is loading very slowly", and no article contains that phrase,
    so a plain ILIKE on the whole string matched nothing exactly when the
    fallback was needed most. The tsquery below is ``plainto_tsquery`` rewritten
    from AND to OR semantics, which keeps stemming and stopword removal while
    letting any single meaningful term hit; ILIKE and trigram matching stay as
    extra recall for short queries and misspellings.

    ``category`` is mapped with :func:`kb_category_filter`; if the filtered
    search finds nothing, the search is repeated without the category.
    """
    category = kb_category_filter(category)
    rows = await _search_knowledge_base_lexical(pool, query, customer_tier, category, max_results)
    if not rows and category:
        rows = await _search_knowledge_base_lexical(pool, query, customer_tier, None, max_results)
    return rows


async def _search_knowledge_base_lexical(
    pool: asyncpg.Pool,
    query: str,
    customer_tier: str,
    category: str | None,
    max_results: int,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        where_clause = "(kb.tier = $2 OR kb.tier = 'all')"
        params = [query, customer_tier, max_results]

        if category:
            where_clause += f" AND kb.category = ${len(params) + 1}"
            params.append(category)

        rows = await conn.fetch(
            f"""
            WITH q AS (
                SELECT to_tsquery(
                    'english',
                    NULLIF(
                        replace(plainto_tsquery('english', $1)::text, ' & ', ' | '),
                        ''
                    )
                ) AS tsq
            )
            SELECT kb.id, kb.title, kb.content, kb.category, kb.tags, kb.source, kb.tier,
                   GREATEST(similarity(kb.title, $1), similarity(kb.content, $1))
                     + COALESCE(
                         ts_rank(
                             to_tsvector('english', kb.title || ' ' || kb.content),
                             q.tsq
                         ),
                         0
                       ) AS similarity
            FROM knowledge_base kb CROSS JOIN q
            WHERE {where_clause}
              AND (
                    (q.tsq IS NOT NULL
                     AND to_tsvector('english', kb.title || ' ' || kb.content) @@ q.tsq)
                 OR kb.title ILIKE '%' || $1 || '%'
                 OR kb.content ILIKE '%' || $1 || '%'
                 OR kb.title % $1
                 OR kb.content % $1
              )
            ORDER BY similarity DESC
            LIMIT $3
            """,
            *params,
        )
        return [dict(row) for row in rows]


async def add_knowledge_base_article(
    pool: asyncpg.Pool,
    title: str,
    content: str,
    embedding: list[float],
    category: str = "general",
    tags: list[str] | None = None,
    source: str = "internal",
    tier: str = "all",
) -> dict[str, Any]:
    """Add a knowledge base article, replacing any article with the same title.

    knowledge_base.title carries a unique index (migration 010), so a plain
    INSERT turned every re-ingest of an existing article into a 500.
    """
    tags = list(tags) if tags else []
    # pgvector requires a non-empty vector; use a zero vector when no embedding
    # was provided (e.g. embedding provider unavailable) so ingestion still works.
    if not embedding:
        embedding = [0.0] * EMBEDDING_DIM
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO knowledge_base (title, content, embedding, category, tags, source, tier)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (title) DO UPDATE SET
                content = EXCLUDED.content,
                embedding = EXCLUDED.embedding,
                category = EXCLUDED.category,
                tags = EXCLUDED.tags,
                source = EXCLUDED.source,
                tier = EXCLUDED.tier,
                updated_at = CURRENT_TIMESTAMP
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
) -> dict[str, Any] | None:
    """Update sentiment score on the latest inbound message for a ticket."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE messages
            SET sentiment_score = $1,
                metadata = metadata || jsonb_build_object('sentiment_score', $2::text)
            WHERE id = (
                SELECT id FROM messages
                WHERE ticket_id = $3 AND direction = 'inbound'
                ORDER BY created_at DESC
                LIMIT 1
            )
            RETURNING id, ticket_id, sentiment_score
            """,
            sentiment_score,
            str(sentiment_score),
            ticket_id,
        )
        return dict(row) if row else None


# Agent runs queries
async def create_agent_run(
    pool: asyncpg.Pool,
    ticket_id: UUID,
    customer_id: UUID,
    input_message: str,
    output_message: str | None = None,
    tool_calls: list[str] | None = None,
    status: str = "running",
) -> dict[str, Any]:
    """Record an agent run."""
    tool_calls = list(tool_calls) if tool_calls else []
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
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark agent run as completed."""
    result = dict(result) if result else {}
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
            json.dumps(result),
            agent_run_id,
        )
        return dict(row) if row else {}


async def get_agent_runs(
    pool: asyncpg.Pool, ticket_id: UUID, limit: int = 10
) -> list[dict[str, Any]]:
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
    labels: dict[str, str] | None = None,
) -> None:
    """Record a metric."""
    if labels is None:
        labels = {}
    if isinstance(labels, (dict, list)):
        labels = json.dumps(labels)
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
) -> list[dict[str, Any]]:
    """Get metrics summary for the last N hours."""
    # metrics.timestamp is TIMESTAMP WITHOUT TIME ZONE, so asyncpg rejects a
    # tz-aware cutoff ("can't subtract offset-naive and offset-aware datetimes").
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).replace(tzinfo=None)

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


async def get_dashboard_metrics(pool: asyncpg.Pool) -> dict[str, Any]:
    """Get aggregated metrics for dashboard."""
    async with pool.acquire() as conn:
        # Tickets by status
        status_counts = await conn.fetch(
            "SELECT status, COUNT(*) as count FROM tickets GROUP BY status"
        )

        # Avg resolution time
        avg_resolution = await conn.fetchval("""
            SELECT AVG(EXTRACT(EPOCH FROM (resolved_at - created_at))/3600) as avg_hours
            FROM tickets
            WHERE resolved_at IS NOT NULL
            """)

        # Escalation rate
        total_tickets = await conn.fetchval("SELECT COUNT(*) FROM tickets")
        escalated = await conn.fetchval("SELECT COUNT(*) FROM tickets WHERE status = 'escalated'")
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


# ── Identity review queue + customer merge ──────────────────────────────────
# Name similarity flags a possible duplicate but never merges automatically
# (see NAME_FUZZY_THRESHOLD). These helpers let a human resolve that flag.


async def list_identity_review_queue(
    pool: asyncpg.Pool, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    """Customers flagged as possible duplicates, newest first, with their candidate."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                c.id, c.email, c.name, c.company, c.tier, c.created_at,
                (c.metadata->>'possible_duplicate_of')::uuid       AS candidate_id,
                c.metadata->>'possible_duplicate_name'             AS candidate_name,
                (c.metadata->>'possible_duplicate_similarity')::float AS similarity,
                cand.email                                          AS candidate_email,
                cand.company                                        AS candidate_company,
                cand.tier                                           AS candidate_tier,
                (SELECT COUNT(*) FROM tickets t WHERE t.customer_id = c.id)    AS ticket_count,
                (SELECT COUNT(*) FROM tickets t WHERE t.customer_id = cand.id) AS candidate_ticket_count
            FROM customers c
            LEFT JOIN customers cand
                   ON cand.id = (c.metadata->>'possible_duplicate_of')::uuid
            WHERE c.metadata->>'needs_identity_review' = 'true'
            ORDER BY c.created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
        return [dict(r) for r in rows]


async def dismiss_identity_review(pool: asyncpg.Pool, customer_id: UUID) -> dict[str, Any] | None:
    """Mark a flagged customer as 'not a duplicate' without merging."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE customers
            SET metadata = (metadata - 'needs_identity_review')
                           || jsonb_build_object('identity_reviewed_at', $2::text),
                updated_at = NOW()
            WHERE id = $1
            RETURNING id, email, name, company, tier, created_at, updated_at, metadata
            """,
            customer_id,
            datetime.now(UTC).isoformat(),
        )
        return dict(row) if row else None


async def merge_customers(
    pool: asyncpg.Pool,
    target_customer_id: UUID,
    source_customer_id: UUID,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Merge ``source`` into ``target``: the target survives, the source is deleted.

    Every row that references the source (tickets, messages, agent_runs,
    identifiers) is repointed at the target first, so the final DELETE cannot
    cascade any real data away. The source's email is kept as an identifier on
    the target so replies to the old address still resolve.

    Raises ValueError when the ids are equal or either customer does not exist.
    """
    if target_customer_id == source_customer_id:
        raise ValueError("target_customer_id and source_customer_id must differ")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # ORDER BY id gives both rows a stable lock order, so two concurrent
            # merges of the same pair cannot deadlock or delete each other's target.
            locked = await conn.fetch(
                "SELECT id, email, name FROM customers WHERE id = ANY($1::uuid[]) ORDER BY id FOR UPDATE",
                [target_customer_id, source_customer_id],
            )
            found = {r["id"]: dict(r) for r in locked}
            if target_customer_id not in found:
                raise ValueError(f"target customer {target_customer_id} not found")
            if source_customer_id not in found:
                raise ValueError(f"source customer {source_customer_id} not found")

            source = found[source_customer_id]

            # Drop source identifiers the target already owns, otherwise the
            # repoint below would violate UNIQUE(identifier_type, identifier_value).
            await conn.execute(
                """
                DELETE FROM customer_identifiers src
                WHERE src.customer_id = $1
                  AND EXISTS (
                      SELECT 1 FROM customer_identifiers tgt
                      WHERE tgt.customer_id = $2
                        AND tgt.identifier_type = src.identifier_type
                        AND tgt.identifier_value = src.identifier_value
                  )
                """,
                source_customer_id,
                target_customer_id,
            )

            moved: dict[str, int] = {}
            for table in ("tickets", "messages", "agent_runs", "customer_identifiers"):
                status = await conn.execute(
                    f"UPDATE {table} SET customer_id = $1 WHERE customer_id = $2",
                    target_customer_id,
                    source_customer_id,
                )
                moved[table] = int(status.split()[-1])

            # Keep the old address reachable.
            await conn.execute(
                """
                INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
                VALUES ($1, 'email', $2)
                ON CONFLICT (identifier_type, identifier_value) DO NOTHING
                """,
                target_customer_id,
                source["email"],
            )

            merge_record = {
                "id": str(source_customer_id),
                "email": source["email"],
                "name": source["name"],
                "merged_at": datetime.now(UTC).isoformat(),
                "reason": reason,
            }
            merged = await conn.fetchrow(
                """
                UPDATE customers
                SET metadata = (metadata - 'needs_identity_review'
                                         - 'possible_duplicate_of'
                                         - 'possible_duplicate_name'
                                         - 'possible_duplicate_similarity')
                               || jsonb_build_object(
                                      'merged_from',
                                      COALESCE(metadata->'merged_from', '[]'::jsonb) || $2::jsonb
                                  ),
                    updated_at = NOW()
                WHERE id = $1
                RETURNING id, email, name, company, tier, created_at, updated_at, metadata
                """,
                target_customer_id,
                json.dumps([merge_record]),
            )

            await conn.execute("DELETE FROM customers WHERE id = $1", source_customer_id)

            logger.info(
                "customers_merged",
                target_id=str(target_customer_id),
                source_id=str(source_customer_id),
                moved=moved,
            )
            return {
                "target": dict(merged),
                "source_email": source["email"],
                "source_deleted": True,
                "moved": moved,
            }
