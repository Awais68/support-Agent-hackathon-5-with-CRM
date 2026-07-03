-- Migration 006: Enable pg_trgm extension + GIN trigram indexes for fuzzy name/email matching
--
-- ── NeonDB Note ──
-- On NeonDB, CREATE EXTENSION works the same as standard PostgreSQL.
-- However, because Neon is a serverless PostgreSQL with shared storage,
-- extension creation requires a few extra considerations:
--
-- 1. Extensions must be installed in the `public` schema (or a schema you
--    own) — `pg_trgm` is installed in `public` by default, so this is fine.
-- 2. Neon supports all PostgreSQL built-in extensions. `pg_trgm` is included
--    and requires no additional setup beyond `CREATE EXTENSION`.
-- 3. If you get a "permission denied" error, verify your database user has
--    the necessary privileges by running:
--       GRANT CREATE ON SCHEMA public TO your_user;
-- 4. Extensions are persistent across Neon's cold-start/warm cycles — once
--    created, they remain available.
-- 5. To verify the extension is active:
--       SELECT * FROM pg_extension WHERE extname = 'pg_trgm';
-- 6. The GIN trigram indexes created below will be auto-maintained by
--    PostgreSQL (autovacuum handles them). No manual maintenance needed.
--
-- Reference: https://neon.tech/docs/postgresql/extensions
-- Enables similarity()-based fuzzy search on customers.name and customers.email
-- to catch typos and minor variations during identity resolution.

-- 1. Enable pg_trgm extension (idempotent)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 2. GIN trigram indexes on customers name and email
-- These accelerate the % operator and similarity() function used in fuzzy search.
CREATE INDEX IF NOT EXISTS idx_customers_name_trgm ON customers USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_customers_email_trgm ON customers USING gin (email gin_trgm_ops);

COMMENT ON EXTENSION pg_trgm IS
    'pg_trgm: trigram-based fuzzy text matching, used for identity resolution fallback';

COMMENT ON INDEX idx_customers_name_trgm IS
    'GIN trigram index on customers.name — speeds up fuzzy name matching';

COMMENT ON INDEX idx_customers_email_trgm IS
    'GIN trigram index on customers.email — speeds up fuzzy email matching';
