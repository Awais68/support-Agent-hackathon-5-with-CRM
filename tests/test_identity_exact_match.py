"""S2: customers are linked on exact (normalized) email only.

The audit showed alice1@acmecorp.com being merged into alice@acmecorp.com
(same domain, edit distance 1): the stranger's ticket landed on Alice's
record and the agent's history lookup returned Alice's tickets. These tests
run the real SQL against PostgreSQL (pg_trgm + fuzzystrmatch) in a throwaway
schema. Set OUTBOUND_TEST_DATABASE_URL (or DATABASE_URL); otherwise they skip.
"""

import json
import os
import uuid

import asyncpg
import pytest

from database import queries as db

# integration: CI runs it in the live-stack job, which has PostgreSQL.
pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

ALICE = "alice@acmecorp.com"


def _db_url() -> str | None:
    url = os.getenv("OUTBOUND_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return None
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    url = _db_url()
    if not url:
        pytest.skip("no OUTBOUND_TEST_DATABASE_URL / DATABASE_URL")
    schema = f"identity_test_{uuid.uuid4().hex[:8]}"
    try:
        admin = await asyncpg.connect(url, timeout=3)
    except (TimeoutError, OSError, asyncpg.PostgresError) as e:
        pytest.skip(f"PostgreSQL not reachable: {e}")
    await admin.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    await admin.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")
    await admin.execute(f"CREATE SCHEMA {schema}")
    p = await asyncpg.create_pool(
        url, min_size=1, max_size=2, server_settings={"search_path": f"{schema},public"}
    )
    async with p.acquire() as conn:
        await conn.execute("""
            CREATE TABLE customers (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email VARCHAR(255) UNIQUE NOT NULL, name VARCHAR(255),
                company VARCHAR(255), tier VARCHAR(50) DEFAULT 'starter',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata JSONB DEFAULT '{}'
            );
            CREATE TABLE customer_identifiers (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                customer_id UUID NOT NULL REFERENCES customers(id),
                identifier_type VARCHAR(50) NOT NULL,
                identifier_value VARCHAR(500) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (identifier_type, identifier_value)
            );
            CREATE TABLE tickets (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                ticket_number VARCHAR(50) UNIQUE NOT NULL,
                customer_id UUID NOT NULL REFERENCES customers(id),
                subject TEXT, category VARCHAR(50), priority VARCHAR(20),
                status VARCHAR(50) DEFAULT 'open', channel VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            );
            CREATE TABLE messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                ticket_id UUID REFERENCES tickets(id), customer_id UUID,
                direction VARCHAR(20), content TEXT, channel VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
    try:
        yield p
    finally:
        await p.close()
        await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        await admin.close()


async def _alice(pool) -> dict:
    alice = await db.get_customer_or_create_by_identifier(pool, "email", ALICE, name="Alice")
    await db.create_ticket(pool, customer_email=ALICE, subject="Alice's private invoice issue")
    return alice


@pytest.mark.parametrize(
    "lookalike", ["alice1@acmecorp.com", "alicee@acmecorp.com", "alce@acmecorp.com"]
)
async def test_lookalike_email_creates_new_customer(pool, lookalike):
    alice = await _alice(pool)

    other = await db.get_customer_or_create_by_identifier(pool, "email", lookalike, name="Mallory")

    assert other["id"] != alice["id"]
    assert other["name"] == "Mallory"
    # The stranger's history must not expose Alice's tickets.
    assert await db.get_customer_history(pool, lookalike) == []


async def test_create_ticket_does_not_attach_lookalike_to_existing_customer(pool):
    alice = await _alice(pool)

    ticket = await db.create_ticket(pool, customer_email="alice1@acmecorp.com", subject="Hi")

    assert ticket["customer_id"] != alice["id"]
    history = await db.get_customer_history(pool, ALICE)
    assert [t["subject"] for t in history] == ["Alice's private invoice issue"]


async def test_find_customer_never_returns_a_lookalike(pool):
    await _alice(pool)
    assert await db.find_customer_by_name_email(pool, "alice1@acmecorp.com", name="Alice") is None


async def test_lookalike_is_flagged_for_review_not_linked(pool):
    alice = await _alice(pool)
    other = await db.get_customer_or_create_by_identifier(
        pool, "email", "alice1@acmecorp.com", name="Mallory"
    )
    meta = other["metadata"]
    meta = meta if isinstance(meta, dict) else json.loads(meta)
    assert meta.get("needs_identity_review") is True
    assert meta.get("possible_duplicate_of") == str(alice["id"])


async def test_exact_email_still_links_after_normalization(pool):
    alice = await _alice(pool)

    same = await db.get_customer_or_create_by_identifier(
        pool, "email", "  ALICE@AcmeCorp.com ", name="Alice"
    )
    ticket = await db.create_ticket(pool, customer_email="Alice@ACMECORP.com ", subject="Again")
    found = await db.find_customer_by_name_email(pool, "ALICE@acmecorp.com")

    assert same["id"] == alice["id"]
    assert ticket["customer_id"] == alice["id"]
    assert found is not None and found["id"] == alice["id"]
    assert len(await db.get_customer_history(pool, " Alice@AcmeCorp.com")) == 2
