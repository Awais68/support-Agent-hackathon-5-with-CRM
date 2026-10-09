#!/usr/bin/env bash
# Idempotent database migration runner (Render pre-deploy command and the
# docker-compose `migrate` service).
# Applies database/schema.sql + database/migrations/*.sql exactly once each,
# in filename order. Each file runs in one transaction together with its
# bookkeeping row, so a failure leaves nothing half-applied.
set -uo pipefail

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL is not set" >&2
    exit 1
fi

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c \
    "CREATE TABLE IF NOT EXISTS _migrations_applied (name TEXT PRIMARY KEY);" || exit 1

# Baseline for databases created before this runner existed: the old
# docker-compose init mounted schema.sql + 001 into docker-entrypoint-initdb.d,
# leaving the tables but no bookkeeping. Neither file is re-runnable, so
# record them as applied; 002+ are written to apply on top.
tracked=$(psql "$DATABASE_URL" -tAc "SELECT count(*) FROM _migrations_applied") || exit 1
has_core=$(psql "$DATABASE_URL" -tAc "SELECT to_regclass('public.customers') IS NOT NULL") || exit 1
if [ "$tracked" = "0" ] && [ "$has_core" = "t" ]; then
    echo "baseline: existing schema without bookkeeping; marking schema + 001 as applied"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c \
        "INSERT INTO _migrations_applied (name) VALUES ('schema'), ('001_initial.sql') ON CONFLICT DO NOTHING" || exit 1
fi

apply() {
    local name="$1"
    local file="$2"
    local applied
    applied=$(psql "$DATABASE_URL" -tAc "SELECT 1 FROM _migrations_applied WHERE name = '$name'")
    if [ "$applied" = "1" ]; then
        echo "skip $name (already applied)"
        return 0
    fi
    echo "applying $name"
    if psql "$DATABASE_URL" --single-transaction -v ON_ERROR_STOP=1 -q -f "$file" \
        -c "INSERT INTO _migrations_applied (name) VALUES ('$name')"; then
        echo "applied $name"
    else
        echo "FAILED: $name" >&2
        return 1
    fi
}

apply "schema" database/schema.sql || exit 1
for f in database/migrations/*.sql; do
    apply "$(basename "$f")" "$f" || exit 1
done

echo "Migrations complete."