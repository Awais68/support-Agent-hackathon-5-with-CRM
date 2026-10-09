"""Ticket categories must not hide the knowledge base.

The web form offers general/technical/billing/bug/feedback; the KB is tagged
onboarding/billing/product/technical. "general" used to become a KB filter
that matched no article, so every general question found nothing.

The search tests run the real SQL against PostgreSQL (pgvector + pg_trgm) in a
throwaway schema. Set OUTBOUND_TEST_DATABASE_URL (or DATABASE_URL); otherwise
they skip.
"""

import os
import uuid

import asyncpg
import pytest

from database import queries as db

MODEL = "test-embed"

# (title, content, category, embedding) — trimmed copies of the seeded articles.
ARTICLES = [
    (
        "Getting Started with TechFlow",
        "Create your workspace, invite teammates and connect your first data source.",
        "onboarding",
        [1, 0, 0],
    ),
    (
        "Account Setup and Billing",
        "Update your payment method, download invoices and change your plan.",
        "billing",
        [0, 1, 0],
    ),
    (
        "Exporting Data",
        "Export any dashboard or report to CSV or Excel from the Export menu.",
        "product",
        [0, 0, 1],
    ),
    (
        "Troubleshooting Connection Issues",
        "If a connector fails to sync, check credentials and firewall rules.",
        "technical",
        [0.7, 0, 0.7],
    ),
    (
        "API Documentation",
        "Authenticate API requests with a bearer token. Rate limits return 429.",
        "technical",
        [0.6, 0.6, 0],
    ),
]


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("general", None),
        ("feedback", None),
        ("bug", "technical"),
        ("Technical", "technical"),
        ("billing", "billing"),
        ("onboarding", "onboarding"),
        ("product", "product"),
        ("unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_kb_category_filter(category, expected):
    assert db.kb_category_filter(category) == expected


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
    schema = f"kb_test_{uuid.uuid4().hex[:8]}"
    try:
        admin = await asyncpg.connect(url, timeout=3)
    except (TimeoutError, OSError, asyncpg.PostgresError) as e:
        pytest.skip(f"PostgreSQL not reachable: {e}")
    await admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await admin.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    await admin.execute(f"CREATE SCHEMA {schema}")
    p = await asyncpg.create_pool(
        url, min_size=1, max_size=2, server_settings={"search_path": f"{schema},public"}
    )
    async with p.acquire() as conn:
        await conn.execute("""
            CREATE TABLE knowledge_base (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                title TEXT NOT NULL, content TEXT NOT NULL, category TEXT,
                tags TEXT[] DEFAULT ARRAY[]::TEXT[], source TEXT,
                tier TEXT DEFAULT 'all', embedding vector(3), embedding_model TEXT
            )
            """)
        for title, content, category, emb in ARTICLES:
            await conn.execute(
                "INSERT INTO knowledge_base (title, content, category, embedding,"
                " embedding_model) VALUES ($1, $2, $3, $4::text::vector, $5)",
                title,
                content,
                category,
                str(emb),
                MODEL,
            )
    try:
        yield p
    finally:
        await p.close()
        await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        await admin.close()


async def _text(pool, query, category):
    rows = await db.search_knowledge_base_text(pool, query=query, category=category)
    return [r["title"] for r in rows]


async def _vector(pool, embedding, category):
    rows = await db.search_knowledge_base(
        pool, embedding=str(embedding), category=category, embedding_model=MODEL
    )
    return [r["title"] for r in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["general", "feedback", None])
async def test_general_question_finds_relevant_article_lexical(pool, category):
    titles = await _text(pool, "How do I export my dashboard to CSV?", category)
    assert titles and titles[0] == "Exporting Data"


@pytest.mark.asyncio
async def test_general_question_finds_relevant_article_semantic(pool):
    titles = await _vector(pool, [0, 0.1, 1], "general")
    assert titles[0] == "Exporting Data"
    assert len(titles) == len(ARTICLES)  # no category filter applied


@pytest.mark.asyncio
async def test_specific_category_still_filters(pool):
    assert set(await _vector(pool, [0, 0.1, 1], "technical")) == {
        "Troubleshooting Connection Issues",
        "API Documentation",
    }
    assert await _text(pool, "invoices payment plan", "billing") == ["Account Setup and Billing"]


@pytest.mark.asyncio
async def test_bug_category_searches_technical_articles(pool):
    titles = await _vector(pool, [1, 0, 0], "bug")
    assert set(titles) == {"Troubleshooting Connection Issues", "API Documentation"}


@pytest.mark.asyncio
async def test_empty_filtered_search_falls_back_to_whole_kb(pool):
    # No onboarding article mentions invoices; the billing one must still be found.
    assert await _text(pool, "download invoices", "onboarding") == ["Account Setup and Billing"]
