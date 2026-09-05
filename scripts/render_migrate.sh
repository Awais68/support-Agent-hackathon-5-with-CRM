#!/usr/bin/env bash
# Idempotent database migration runner for Render pre-deploy command.
# Applies database/schema.sql + database/migrations/*.sql exactly once each.
set -uo pipefail

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL is not set" >&2
    exit 1
fi

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c \
    "CREATE TABLE IF NOT EXISTS _migrations_applied (name TEXT PRIMARY KEY);"

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
    if psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$file"; then
        psql "$DATABASE_URL" -q -c "INSERT INTO _migrations_applied (name) VALUES ('$name')"
        echo "applied $name"
    else
        echo "FAILED: $name" >&2
        return 1
    fi
}

apply "schema" database/schema.sql
for f in database/migrations/*.sql; do
    apply "$(basename "$f")" "$f"
done

echo "Migrations complete."