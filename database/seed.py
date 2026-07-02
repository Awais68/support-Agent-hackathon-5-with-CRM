"""Standalone, idempotent database seed/migration script.

Usage:
    python -m database.seed              # Runs schema + migrations + seed data
    python -m database.seed --migrations-only  # Only run pending migrations

Idempotent: safe to run multiple times.
Uses asyncpg pool (matching database/queries.py pattern).
Applies migrations in filename order, tracked in schema_migrations table.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


async def ensure_schema_migrations_table(pool: asyncpg.Pool) -> None:
    """Create the schema_migrations tracking table if it doesn't exist."""
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(255) PRIMARY KEY,
                filename VARCHAR(500) NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                checksum VARCHAR(64)
            )
        """)


async def get_applied_migrations(pool: asyncpg.Pool) -> set[str]:
    """Return set of already-applied migration filenames."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT filename FROM schema_migrations ORDER BY version")
        return {row["filename"] for row in rows}


async def mark_migration_applied(
    pool: asyncpg.Pool, version: str, filename: str, checksum: str = ""
) -> None:
    """Record a migration as applied."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO schema_migrations (version, filename, checksum)
            VALUES ($1, $2, $3)
            ON CONFLICT (version) DO NOTHING
            """,
            version,
            filename,
            checksum,
        )


async def apply_schema(pool: asyncpg.Pool) -> list[str]:
    """Apply schema.sql if not already applied (checks if core tables exist)."""
    async with pool.acquire() as conn:
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        existing = {row["table_name"] for row in tables}

    if "customers" in existing and "tickets" in existing:
        logger.info("Schema already applied (core tables exist), skipping")
        return []

    if not SCHEMA_FILE.exists():
        logger.warning("schema.sql not found at %s", SCHEMA_FILE)
        return []

    sql = SCHEMA_FILE.read_text()
    async with pool.acquire() as conn:
        await conn.execute(sql)

    logger.info("Schema applied from schema.sql")
    return ["schema.sql"]


async def apply_migration_file(pool: asyncpg.Pool, filepath: Path) -> None:
    """Execute a single migration SQL file."""
    sql = filepath.read_text()
    async with pool.acquire() as conn:
        await conn.execute(sql)


async def run_pending_migrations(
    pool: asyncpg.Pool, applied: set[str]
) -> list[str]:
    """Run any migration files not yet applied."""
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    newly_applied = []

    for mfile in migration_files:
        if mfile.name in applied:
            logger.debug("Migration already applied", filename=mfile.name)
            continue

        logger.info("Applying migration", filename=mfile.name)
        try:
            await apply_migration_file(pool, mfile)
            version = mfile.stem.split("_")[0] if "_" in mfile.stem else mfile.stem
            await mark_migration_applied(pool, version, mfile.name)
            newly_applied.append(mfile.name)
            logger.info("Migration applied successfully", filename=mfile.name)
        except Exception as e:
            logger.error("Migration failed", filename=mfile.name, error=str(e))
            raise

    return newly_applied


async def seed_baseline_data(pool: asyncpg.Pool) -> dict[str, int]:
    """Seed baseline data — idempotent (ON CONFLICT DO NOTHING everywhere)."""
    counts: dict[str, int] = {}
    async with pool.acquire() as conn:
        # Seed sample customers
        customer_result = await conn.execute("""
            INSERT INTO customers (email, name, company, tier, metadata) VALUES
                ('alice@acmecorp.com', 'Alice Johnson', 'Acme Corp', 'enterprise',
                 '{"account_manager":"bob@techflow.com"}'::jsonb),
                ('bob@startupinc.com', 'Bob Smith', 'Startup Inc', 'growth', '{}'::jsonb),
                ('carol@smallbiz.com', 'Carol Davis', 'Small Biz LLC', 'starter', '{}'::jsonb),
                ('david@enterprise.io', 'David Wilson', 'Enterprise Inc', 'enterprise',
                 '{"account_manager":"alice@techflow.com"}'::jsonb),
                ('eva@mediumco.com', 'Eva Martinez', 'Medium Co', 'growth', '{}'::jsonb),
                ('frank@bootstrap.com', 'Frank Chen', 'Bootstrap Labs', 'starter', '{}'::jsonb),
                ('grace@bigtech.com', 'Grace Lee', 'BigTech Solutions', 'enterprise',
                 '{"account_manager":"charlie@techflow.com"}'::jsonb),
                ('henry@devshop.com', 'Henry Brown', 'Dev Shop', 'growth', '{}'::jsonb),
                ('iris@analytics.io', 'Iris Patel', 'Analytics Pro', 'enterprise', '{}'::jsonb),
                ('jack@cloudnative.com', 'Jack Taylor', 'CloudNative Corp', 'growth', '{}'::jsonb)
            ON CONFLICT (email) DO NOTHING
        """)
        counts["customers"] = parse_insert_count(customer_result)

        # Seed customer_identifiers for existing customers
        id_result = await conn.execute("""
            INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
            SELECT id, 'email', email FROM customers
            ON CONFLICT (identifier_type, identifier_value) DO NOTHING
        """)
        counts["customer_identifiers"] = parse_insert_count(id_result)

        # Seed sample tickets (up to 50)
        ticket_result = await conn.execute("""
            INSERT INTO tickets (ticket_number, customer_id, subject, category, priority, status, channel, assigned_to)
            SELECT
                'TKT-' || to_char(CURRENT_DATE, 'YYYYMMDD') || '-' || LPAD(
                    ROW_NUMBER() OVER (ORDER BY c.email)::TEXT, 4, '0'
                ),
                c.id,
                CASE (ROW_NUMBER() OVER (ORDER BY c.email) % 5)
                    WHEN 1 THEN 'How to set up data connectors?'
                    WHEN 2 THEN 'Anomaly detection not working'
                    WHEN 3 THEN 'Billing question about enterprise plan'
                    WHEN 4 THEN 'API authentication error'
                    ELSE 'Dashboard performance issues'
                END,
                CASE (ROW_NUMBER() OVER (ORDER BY c.email) % 4)
                    WHEN 1 THEN 'technical'
                    WHEN 2 THEN 'billing'
                    WHEN 3 THEN 'onboarding'
                    ELSE 'general'
                END,
                CASE (ROW_NUMBER() OVER (ORDER BY c.email) % 4)
                    WHEN 1 THEN 'high'
                    WHEN 2 THEN 'medium'
                    WHEN 3 THEN 'low'
                    ELSE 'critical'
                END,
                CASE (ROW_NUMBER() OVER (ORDER BY c.email) % 5)
                    WHEN 1 THEN 'open'
                    WHEN 2 THEN 'in_progress'
                    WHEN 3 THEN 'resolved'
                    WHEN 4 THEN 'escalated'
                    ELSE 'closed'
                END,
                CASE (ROW_NUMBER() OVER (ORDER BY c.email) % 3)
                    WHEN 1 THEN 'email'
                    WHEN 2 THEN 'whatsapp'
                    ELSE 'webform'
                END,
                'support-agent-' || (ROW_NUMBER() OVER (ORDER BY c.email) % 3 + 1)::TEXT
            FROM customers c
            WHERE NOT EXISTS (
                SELECT 1 FROM tickets t WHERE t.customer_id = c.id
            )
            LIMIT 50
        """)
        counts["tickets"] = parse_insert_count(ticket_result)

        # Seed knowledge base articles
        kb_result = await conn.execute("""
            INSERT INTO knowledge_base (title, content, category, tags, source, tier) VALUES
                ('Getting Started with TechFlow',
                 'TechFlow Analytics is a modern data platform. Start by connecting your first data source...',
                 'onboarding', ARRAY['setup', 'basics'], 'internal-wiki', 'all'),
                ('Account Setup and Billing',
                 'Your account includes storage, API calls, and support. Billing is monthly.',
                 'billing', ARRAY['billing', 'account'], 'internal-wiki', 'all'),
                ('Data Connector Configuration',
                 'Data connectors allow TechFlow to read from your databases, APIs, and warehouses.',
                 'technical', ARRAY['connectors', 'setup'], 'internal-wiki', 'all'),
                ('Anomaly Detection Setup',
                 'Anomaly detection uses AI to identify unusual patterns.',
                 'technical', ARRAY['ai', 'detection'], 'internal-wiki', 'growth'),
                ('API Documentation',
                 'TechFlow API v2 supports REST and GraphQL. Authentication uses API keys.',
                 'technical', ARRAY['api', 'reference'], 'api-docs', 'all'),
                ('Enterprise Features',
                 'Enterprise customers get SSO, advanced RBAC, dedicated support.',
                 'billing', ARRAY['enterprise', 'features'], 'internal-wiki', 'enterprise'),
                ('Troubleshooting Connection Issues',
                 'If your connector fails, check firewall rules, credentials, and network connectivity.',
                 'technical', ARRAY['troubleshooting', 'connectors'], 'kb-article', 'all'),
                ('Performance Optimization',
                 'Query optimization, indexing strategies, and caching can improve dashboard load times.',
                 'technical', ARRAY['performance', 'optimization'], 'kb-article', 'growth'),
                ('Dashboard Customization',
                 'Create custom dashboards by dragging widgets, changing colors, and adding filters.',
                 'product', ARRAY['dashboards', 'ui'], 'internal-wiki', 'all'),
                ('Exporting Data',
                 'Export data to CSV, Parquet, or directly to cloud storage via SFTP/S3 integration.',
                 'product', ARRAY['export', 'integration'], 'internal-wiki', 'all')
            ON CONFLICT DO NOTHING
        """)
        counts["knowledge_base"] = parse_insert_count(kb_result)

    return counts


def parse_insert_count(result: str) -> int:
    """Parse Postgres INSERT result like 'INSERT 0 5' to return the count."""
    try:
        parts = result.split()
        if len(parts) >= 2:
            return int(parts[-1])
    except (ValueError, IndexError):
        pass
    return 0


async def seed(pool: asyncpg.Pool) -> dict[str, any]:
    """Run the full seed pipeline. Idempotent."""
    results = {}

    # 1. Ensure migrations tracking table exists
    await ensure_schema_migrations_table(pool)

    # 2. Apply core schema if needed
    schema_files = await apply_schema(pool)
    results["schema_applied"] = schema_files

    # 3. Register pgvector codec
    try:
        async with pool.acquire() as conn:
            await conn.set_type_codec("vector", encoder=str, decoder=list, schema="public")
    except Exception:
        logger.warning("Could not register pgvector codec (vector type may not exist yet)")

    # 4. Run pending migrations
    applied = await get_applied_migrations(pool)
    newly_applied = await run_pending_migrations(pool, applied)
    results["migrations_applied"] = newly_applied

    # 5. Seed baseline data
    counts = await seed_baseline_data(pool)
    results["data_counts"] = counts

    return results


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Database seed/migration tool")
    parser.add_argument("--migrations-only", action="store_true",
                        help="Only run pending migrations, skip data seeding")
    args = parser.parse_args()

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://***REDACTED_USER***:***REDACTED_PASS***@localhost:5432/techflow",
    )

    logger.info("Connecting to database", db_url=db_url)
    pool = await asyncpg.create_pool(db_url)

    try:
        results = await seed(pool)

        if results.get("schema_applied"):
            logger.info("Schema files applied", files=results["schema_applied"])
        if results.get("migrations_applied"):
            logger.info("Migrations applied", count=len(results["migrations_applied"]),
                        files=results["migrations_applied"])
        else:
            logger.info("No pending migrations")

        if not args.migrations_only:
            counts = results.get("data_counts", {})
            if any(counts.values()):
                logger.info("Seed data inserted", counts=counts)
            else:
                logger.info("No new seed data needed (idempotent — all data already present)",
                            counts=counts)
        else:
            logger.info("Migrations-only mode, skipping data seed")

        logger.info("Seed complete", results_summary={
            k: v for k, v in results.items() if k != "data_counts"
        })
    except Exception as e:
        logger.error("Seed failed", error=str(e))
        sys.exit(1)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
