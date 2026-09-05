#!/usr/bin/env bash
#
# Setup and run the full end-to-end (Playwright) test suite.
#
# This brings up the REAL infrastructure the e2e tests need:
#   - PostgreSQL + Kafka  (via docker compose)
#   - FastAPI API server  (uvicorn on localhost:8000)
#   - Playwright + Chromium (installed into .venv if missing)
#
# Usage:
#   ./scripts/setup_e2e.sh            # run the Playwright suite
#   ./scripts/setup_e2e.sh --keep     # leave the API server running afterwards
#   ./scripts/setup_e2e.sh --skip-run # only bring up infra, do not run tests
#
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
PIP="${PYTHON} -m pip"
BASE_URL="http://localhost:8000"
API_PID=""

KEEP_SERVER=false
SKIP_RUN=false
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP_SERVER=true ;;
    --skip-run) SKIP_RUN=true ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

cleanup() {
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    echo "==> Stopping API server (pid $API_PID)"
    kill "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# 1. Bring up Postgres + Kafka (auto-initializes schema via initdb mount)
echo "==> Starting PostgreSQL + Kafka via docker compose"
docker compose up -d postgres kafka
echo "==> Waiting for Postgres + Kafka to become healthy"
for i in $(seq 1 60); do
  pg_ok=$(docker compose ps postgres --format json 2>/dev/null | grep -q '"healthy"' && echo yes || echo no)
  kafka_ok=$(docker compose ps kafka --format json 2>/dev/null | grep -q '"healthy"' && echo yes || echo no)
  if [[ "$pg_ok" == "yes" && "$kafka_ok" == "yes" ]]; then
    break
  fi
  sleep 2
done
echo "==> Infra status: postgres=$pg_ok kafka=$kafka_ok"

# 1b. Ensure Kafka topics exist (auto-create can race; create explicitly)
echo "==> Ensuring Kafka topics exist"
for t in \
  inbound.email inbound.whatsapp inbound.webform inbound.voice \
  agent.processing agent.completed notifications.outbound \
  escalations metrics.events dlq; do
  docker compose exec -T kafka kafka-topics \
    --bootstrap-server localhost:9092 --create --if-not-exists \
    --topic "$t" --partitions 1 --replication-factor 1 >/dev/null 2>&1 || true
done

# 1c. Ensure extensions + migrations are applied (initdb only runs on a fresh data dir)
echo "==> Ensuring DB extensions + migrations"
docker compose exec -T postgres psql -U techflow -d techflow -v ON_ERROR_STOP=1 \
  -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
  -c "CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;" \
  -f /dev/stdin <<'SQL'
ALTER TABLE tickets DROP CONSTRAINT IF EXISTS tickets_channel_check;
ALTER TABLE tickets ADD CONSTRAINT tickets_channel_check CHECK (channel IN ('email','whatsapp','webform','voice','api'));
ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_channel_check;
ALTER TABLE messages ADD CONSTRAINT messages_channel_check CHECK (channel IN ('email','whatsapp','webform','voice','api'));
SQL

# 2. Install Playwright + browsers if missing
if ! "$PYTHON" -c "import playwright" 2>/dev/null; then
  echo "==> Installing playwright + chromium"
  "$PIP" install playwright
  "$PYTHON" -m playwright install chromium
fi

# 3. Start the API server (local Postgres is exposed on host port 5433)
echo "==> Starting API server on $BASE_URL"
export DATABASE_URL="postgresql://techflow:techflow@localhost:5433/techflow"
export KAFKA_BOOTSTRAP_SERVERS="localhost:9092"
export API_KEY="test-key-12345"
# Only export these when actually set: exporting an empty value would shadow
# the key that api/main.py loads from .env.
if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  export OPENROUTER_API_KEY
  echo "==> OPENROUTER_API_KEY detected from environment"
else
  echo "==> OPENROUTER_API_KEY not set in shell — falling back to .env"
fi
if [[ -n "${GROQ_API_KEY:-}" ]]; then
  export GROQ_API_KEY
fi

nohup "$PYTHON" -m uvicorn api.main:app --host 0.0.0.0 --port 8000 \
  > /tmp/specifyplus-api.log 2>&1 &
API_PID=$!

for i in $(seq 1 60); do
  if curl -sf "$BASE_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
if ! curl -sf "$BASE_URL/health" >/dev/null 2>&1; then
  echo "==> ERROR: API server did not become healthy. Logs:"
  tail -50 /tmp/specifyplus-api.log
  exit 1
fi
echo "==> API server healthy (pid $API_PID)"

if [[ "$SKIP_RUN" == "true" ]]; then
  echo "==> Infra ready. Leaving API server running (use --kill to stop later)."
  exit 0
fi

# 4. Run the Playwright e2e suite
echo "==> Running Playwright e2e tests"
set +e
"$PYTHON" -m pytest tests/test_e2e_playwright.py -v
EXIT_CODE=$?
set -e

if [[ "$KEEP_SERVER" == "true" ]]; then
  echo "==> Leaving API server running (pid $API_PID). Stop with: kill $API_PID"
else
  echo "==> Stopping API server"
  kill "$API_PID" 2>/dev/null || true
  API_PID=""
fi

exit "$EXIT_CODE"