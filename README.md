# SpecifyPlus — Customer Success Digital FTE

An AI customer-support agent for a fictional SaaS company, **TechFlow Analytics**. It takes customer
messages from **Email (Gmail)**, **WhatsApp (Twilio)**, a **Next.js web form**, and **Voice** (recorded
audio or a Twilio phone call). It works out who the customer is across channels and opens or updates a
ticket. It then either answers with an LLM that searches the knowledge base or escalates to a human.

- **Who it's for:** support teams who want a first-line agent that is always on, plus the developers
  who run it.
- **What it solves:** it sorts and answers routine questions on all channels. Sensitive cases (pricing
  or refunds, legal threats, angry or urgent customers) go to a human. Every conversation, ticket, and
  agent run is stored in one Postgres database.

> This README was written by reading the source and running the project from a fresh clone (see
> [Verification log](#verification-log)). Where the code doesn't show something, it says
> *"Not determined from code"*.

---

## Table of Contents

1. [Tech Stack](#tech-stack)
2. [Folder Structure](#folder-structure)
3. [Architecture & Workflow](#architecture--workflow)
4. [Core Features](#core-features)
5. [Setup & Installation](#setup--installation)
6. [Environment Variables](#environment-variables)
7. [Scripts & Commands](#scripts--commands)
8. [API Endpoints](#api-endpoints)
9. [Database](#database)
10. [Deployment](#deployment)
11. [Known Issues](#known-issues)
12. [Verification log](#verification-log)

---

## Tech Stack

### Backend (Python ≥ 3.13; `.python-version` = `3.13`)

Version constraints come from `pyproject.toml` and `requirements.txt`. The resolved versions are what
`uv sync` installed on 2026-10-09.

| Area | Library | Constraint |
|---|---|---|
| Web framework | `fastapi`, `uvicorn[standard]` | `>=0.109.0`, `>=0.27.0` |
| LLM client | `openai` (OpenAI-compatible client used for DeepSeek, OpenRouter, Gemini, Groq) | `>=1.45.0` |
| Agent SDK | `openai-agents` | `>=0.0.2` (declared; the agent loop is hand-written in `agent/customer_success_agent.py`) |
| Database | `asyncpg`, `pgvector`, `sqlalchemy` | `>=0.29.0`, `>=0.2.0`, `>=2.0.0` (SQLAlchemy isn't used by app code) |
| Message queue | `aiokafka` | `>=0.10.0` |
| Gmail | `google-auth-oauthlib`, `google-auth-httplib2`, `google-api-python-client` | `>=1.2.0`, `>=0.2.0`, `>=2.105.0` |
| WhatsApp / voice calls | `twilio` | `>=9.0.0` |
| Sentiment | `vaderSentiment` | `>=3.3.2` |
| Config / validation | `python-dotenv`, `pydantic[email]`, `pydantic-settings` | `>=1.0.0`, `>=2.0.0`, `>=2.0.0` |
| Resilience / logging | `tenacity`, `structlog` | `>=8.2.0`, `>=24.1.0` |
| HTTP / files | `httpx`, `aiofiles` | `>=0.26.0`, `>=23.2.0` |
| Rate limiting | `slowapi`, `redis[hiredis]` | `>=0.1.9`, `>=5.0.0` |
| Monitoring | `prometheus-client` | `>=0.19.0` |
| Dev (`[dev]` extra) | `pytest`, `pytest-asyncio`, `pytest-cov`, `locust`, `black`, `ruff`, `mypy`, `playwright`, `pytest-playwright`, `gtts` | see `pyproject.toml` |

Build backend: `hatchling`. Lint: `ruff` (py313, line length 100). Format: `black`.

### Frontend (`web-form/`)

| Library | Version (`package.json`) |
|---|---|
| `next` | `^14.2.0` (App Router, `output: "standalone"`) |
| `react`, `react-dom` | `^18.3.1` |
| `react-hook-form`, `@hookform/resolvers`, `zod` | `^7.50.0`, `^3.3.4`, `^3.22.4` |
| `typescript` | `^5.3.3` |
| `tailwindcss`, `postcss`, `autoprefixer` | `^3.4.1`, `^8.4.32`, `^10.4.17` |

### Infrastructure & external services

| Component | Version / provider | Where |
|---|---|---|
| PostgreSQL + pgvector | `pgvector/pgvector:0.8.0-pg16`. Extensions: `vector`, `uuid-ossp`, `pg_trgm`, `fuzzystrmatch` | `docker-compose.yml`, `database/schema.sql` |
| Kafka | `confluentinc/cp-kafka:7.6.0` + `cp-zookeeper:7.6.0` (or KRaft via `docker-compose.kraft.yml`) | compose, `k8s/kafka.yaml` |
| Prometheus / Grafana | `prom/prometheus:v2.50.1`, `grafana/grafana:10.3.3`, `postgres-exporter:v0.15.0` | `docker-compose.yml`, `monitoring/` |
| Chat LLM | **DeepSeek** (`deepseek-chat`) if `DEEPSEEK_API_KEY` is set, otherwise **OpenRouter** (`openai/gpt-4o`) | `chat_provider.py` |
| Embeddings | **Gemini** `gemini-embedding-001` (1536 dims) if `GEMINI_API_KEY` is set, otherwise the chat provider with `EMBEDDING_MODEL` | `embeddings_provider.py` |
| Speech-to-text | Groq Whisper `whisper-large-v3-turbo`, falling back to OpenAI `whisper-1` | `channels/voice_handler.py` |
| Text-to-speech | OpenAI `tts-1` (voice `alloy`), falling back to gTTS, then text only | `channels/voice_handler.py` |
| Voice translation | Chat provider, falling back to Groq `llama-3.3-70b-versatile` | `channels/voice_handler.py` |
| Messaging | Twilio (WhatsApp + Voice), Gmail API | `channels/` |
| Hosting targets | Docker Compose, Kubernetes (HPA + KEDA), Render | `k8s/`, `render.yaml` |

---

## Folder Structure

Only tracked files are shown. Local-only folders such as `.venv/`, `.kilo/`, and `specs/` are left out.

```text
specifyplus/
├── main.py                    # Entrypoint: RUN_MODE=api → uvicorn, RUN_MODE=worker → message processor
├── env_config.py              # Layered .env loading (.env.<ENVIRONMENT> over .env); no validation
├── chat_provider.py           # Builds the chat client: DeepSeek, otherwise OpenRouter
├── embeddings_provider.py     # Builds the embeddings client: Gemini, otherwise chat provider
├── embeddings_service.py      # EmbeddingsService class (not referenced by app code)
├── kafka_client.py            # aiokafka producer/consumer, topic names, NoOp producer, DLQ routing
├── metrics.py                 # Prometheus metric definitions
├── mcp_server.py              # In-process tool registry (not a real MCP server; never populated)
├── exceptions.py              # AppError hierarchy → JSON error responses
├── agent/
│   ├── customer_success_agent.py  # Core agent: gate → LLM tool-calling loop → format → persist
│   ├── tools.py                   # The 5 tools the LLM can call + OpenAI tool schemas
│   ├── tools_executor.py          # Alternative ToolExecutor (used only by tests / mcp_server)
│   ├── pre_processing_gate.py     # Regex + sentiment rules: ALLOW / ESCALATE / DEFLECT
│   ├── sentiment_analyzer.py      # VADER sentiment, emotion, urgency, aspect scores, drop detection
│   ├── prompts.py                 # System prompt, classification prompt, per-channel addenda
│   └── formatters.py              # Per-channel reply formatting (email / WhatsApp / web)
├── api/
│   ├── main.py                # FastAPI app: lifespan, auth middleware, all routes, error handlers
│   ├── rate_limiter.py        # slowapi limiter (Redis or in-memory)
│   └── websocket_manager.py   # In-memory per-ticket WebSocket broadcast
├── channels/
│   ├── gmail_handler.py       # Gmail OAuth, inbox polling → Kafka, send_reply (unused)
│   ├── whatsapp_handler.py    # Twilio webhook parsing/validation → Kafka, send_message (unused)
│   ├── web_form_handler.py    # WebFormSubmission model + publish to Kafka
│   └── voice_handler.py       # STT, confidence gate, translation, TTS
├── workers/
│   ├── message_processor.py   # Kafka consumer → identity resolution → ticket → agent
│   └── metrics_collector.py   # Every 300 s: aggregate DB metrics → metrics table + Kafka
├── database/
│   ├── schema.sql             # Base schema (tables, indexes, extensions)
│   ├── migrations/001–010     # Seed data + incremental schema changes
│   ├── queries.py             # All SQL (asyncpg): tickets, customers, identity, KB, metrics
│   └── seed.py                # Migration runner + idempotent seed + KB embedding backfill
├── context/                   # Company profile, brand voice, escalation rules, product docs, sample tickets
├── utils/circuit_breaker.py   # Named async circuit breakers (openai, kafka, embeddings, …)
├── web-form/                  # Next.js 14 customer portal (support form, voice recorder, ticket tracking)
│   └── src/
│       ├── app/page.tsx                     # Home: VoiceRecorder + SupportForm
│       ├── app/ticket/[id]/page.tsx         # Ticket tracking page
│       ├── app/api/tickets/[id]/route.ts    # Server-side proxy to GET /tickets/{id} (adds X-API-Key)
│       ├── components/                      # SupportForm, SuccessMessage, TicketStatus, VoiceRecorder
│       └── lib/api.ts                       # Fetch/WebSocket helpers, zod schema
├── chaos/                     # Chaos experiments (docker kill/restart/network) + safety guard + runner
├── k8s/                       # Namespace, deployments, services, HPA, KEDA, PDB, Kafka, Postgres, ingress
├── monitoring/                # prometheus.yml + Grafana dashboard JSON
├── scripts/                   # Migrations, e2e setup, secret scan, Gmail auth, load/voice/Gemini tests
├── tests/                     # pytest suite + locustfile
├── examples/                  # Python and Node API client examples
├── .github/workflows/         # ci, cd, chaos, secret-scan
├── .githooks/                 # pre-commit / pre-push → scripts/check-secrets.sh
├── Dockerfile                 # Backend image (API; also used for the worker)
├── docker-compose.yml         # Full local stack
├── docker-compose.kraft.yml   # Single-node KRaft Kafka (no ZooKeeper)
├── render.yaml                # Render blueprint (API + worker + web form + Postgres)
└── *.md                       # Extra docs: ARCHITECTURE, DEPLOYMENT, CHAOS_TESTING, QUICK_START, …
```

---

## Architecture & Workflow

```mermaid
flowchart LR
    subgraph Channels
      GM[Gmail inbox] -->|poll every 60s| W
      WA[Twilio WhatsApp] -->|POST /webhooks/whatsapp| API
      WF[Next.js web form] -->|POST /webhooks/webform| API
      VO[Browser mic / Twilio call] -->|POST /webhooks/voice/*| API
    end

    API[FastAPI api/main.py] -->|inbound.whatsapp / inbound.webform| K[(Kafka)]
    W[Worker workers/message_processor.py] -->|inbound.email| K
    K -->|consume inbound.*| W

    W --> AG[CustomerSuccessAgent]
    API -->|voice: always sync<br/>webform: sync if Kafka off| AG

    AG --> G{Pre-processing gate}
    G -->|ESCALATE| ESC[status=escalated + escalations topic]
    G -->|DEFLECT| CAN[canned reply]
    G -->|ALLOW| LLM[DeepSeek / OpenRouter tool loop]
    LLM --> T[tools: KB search, history,<br/>create ticket, escalate, send_response]
    T --> DB[(Postgres + pgvector)]
    AG --> DB
    AG -->|notifications.outbound<br/>agent.completed| K

    WF -->|GET /api/tickets/id → GET /tickets/id| API
    API --> DB
```

### Request flow, end to end

1. **Ingress**
   - **Web form:** the browser `POST`s to `/webhooks/webform`. The API validates `WebFormSubmission` and
     resolves or creates the customer. It creates the ticket synchronously (channel `webform`), then
     publishes to `inbound.webform`. It returns `201 {ticket_number, …}` right away.
   - **WhatsApp:** Twilio `POST`s form data to `/webhooks/whatsapp`. If `TWILIO_AUTH_TOKEN` is set, the
     signature is checked. The message is published to `inbound.whatsapp`.
   - **Email:** the worker polls Gmail for `is:unread` every 60 s, publishes each message to
     `inbound.email`, and marks it read.
   - **Voice:** `/webhooks/voice/message` takes base64 audio or an audio URL and runs STT, a confidence
     gate, translation to English, the agent, translation back, and TTS. All of this happens
     **synchronously** in the API. `/webhooks/voice/call` returns TwiML for Twilio phone calls.
2. **Worker** (`workers/message_processor.py`): consumer group `techflow-message-processor` reads
   `inbound.email`, `inbound.whatsapp`, and `inbound.webform`. It resolves the customer across channels
   (exact identifier → fuzzy email local-part via `levenshtein` → name trigram flagged for review),
   creates or reuses the ticket, and calls the agent.
3. **Agent** (`agent/customer_success_agent.py`):
   1. Classifies the message with the LLM (the result is logged only).
   2. Runs the **pre-processing gate** (`pre_processing_gate.py`):
      - pricing/refund regex → ESCALATE
      - legal regex → ESCALATE (critical)
      - questions about internal details → DEFLECT
      - sentiment < 0.3 → ESCALATE
      - urgency ≥ 0.5 → ESCALATE
   3. On ALLOW, runs an OpenAI-style tool-calling loop (max 10 turns, through the `openai` circuit
      breaker).
   4. Formats the reply for the channel and stores the agent run.
   5. Publishes to `notifications.outbound` and `agent.completed`.
4. **Response**
   - **Web form:** the customer reads the reply on `/ticket/[id]`. The Next.js API route proxies
     `GET /tickets/{id}`. A WebSocket on `/ws/tickets/{id}` pushes updates from API-side changes.
   - **Voice:** the reply comes back in the HTTP response as text plus optional audio, or as TwiML.
   - **Email / WhatsApp:** the reply is stored and published to `notifications.outbound`, but
     **nothing consumes that topic**, so it is never sent (see [Known Issues](#known-issues)).

### Kafka topics (`kafka_client.py`)

`inbound.email`, `inbound.whatsapp`, `inbound.webform`, `inbound.voice` (analytics only),
`agent.processing`, `agent.completed`, `notifications.outbound`, `escalations`, `metrics.events`, `dlq`.

### Degraded mode (`ENABLE_KAFKA=false`)

- The API uses a `NoOpKafkaProducer`, and the web form runs the agent synchronously inside the request.
- **WhatsApp messages are silently dropped.**
- The worker runs only the metrics collector: no Gmail polling and no consumer.

---

## Core Features

| Feature | What it does | Implemented in |
|---|---|---|
| Multi-channel intake | Email, WhatsApp, web form, voice note, phone call | `channels/*.py`, `api/main.py` (`/webhooks/*`), `workers/message_processor.py` |
| Cross-channel identity resolution | Links email, phone, and web-session identifiers to one customer. Uses fuzzy email/name matching and a review queue, and supports merging customers. | `database/queries.py` (`resolve_customer…`, `merge_customers`), `/customers/*` endpoints |
| Ticketing | Ticket numbers like `TKT-YYYYMMDD-XXXXXX`; status lifecycle `open / in_progress / resolved / escalated / closed` | `database/queries.py`, `/tickets*` endpoints |
| LLM agent with tools | `search_knowledge_base`, `create_ticket`, `get_customer_history`, `escalate_to_human`, `send_response` | `agent/tools.py`, `agent/customer_success_agent.py` |
| Knowledge-base search | pgvector HNSW cosine search filtered by `embedding_model`. Falls back to trigram text search when there is no provider, the circuit is open, the call errors, or no vectors exist. | `agent/tools.py`, `database/queries.py`, `/knowledge-base` |
| Escalation gate | Deterministic regex and sentiment rules that run before the LLM | `agent/pre_processing_gate.py` |
| Sentiment & emotion | VADER score (0..1), emotion, urgency, 8 aspect scores, sentiment-drop detection | `agent/sentiment_analyzer.py` |
| Channel formatting | Email greeting and footer; WhatsApp replies split at about 300 chars; web replies with tracking info | `agent/formatters.py` |
| Voice pipeline | STT with a confidence gate, language detection and translation, TTS, Twilio TwiML | `channels/voice_handler.py`, `/webhooks/voice/*`, `/voice/*` |
| Live ticket updates | WebSocket broadcast of `ticket_update` and `new_message` | `api/websocket_manager.py`, `web-form/src/components/TicketStatus.tsx` |
| Rate limiting & auth | `X-API-Key` middleware; slowapi limits per IP, optionally stored in Redis | `api/main.py`, `api/rate_limiter.py` |
| Circuit breakers | Per-service breakers, configurable with `CB_<NAME>_*` env vars | `utils/circuit_breaker.py` |
| Metrics | Prometheus on internal port 9100 (`METRICS_PORT`); DB-aggregated dashboard metrics every 300 s | `metrics.py`, `workers/metrics_collector.py`, `/metrics/*` |
| Chaos testing | 6 docker-based experiments behind a safety guard | `chaos/` |
| Secret scanning | gitleaks in CI plus local git hooks | `.github/workflows/secret-scan.yml`, `scripts/check-secrets.sh` |

---

## Setup & Installation

**Prerequisites:** Git, [uv](https://docs.astral.sh/uv/) (it installs Python 3.13 for you), Node.js 20+,
and Docker.

These steps were run on a fresh clone. Each step is marked either ✅ verified or ⚠️ not run, with the
reason.

### 1. Clone and install the backend ✅

```bash
git clone <repo-url> specifyplus && cd specifyplus
uv sync --extra dev          # creates .venv with Python 3.13 and all deps
```

### 2. Start Postgres and apply all migrations ✅

`docker-compose.yml` only runs `schema.sql` and `001_initial.sql` on first boot. Apply migrations
002–010 yourself, or KB search and seeding will break (see Known Issues).

```bash
docker compose up -d postgres            # host port 5433, user/pass/db = techflow
# apply 002..010 (001 already ran via the init mount)
for f in $(ls database/migrations/*.sql | tail -n +2); do
  docker exec -i techflow-postgres psql -U techflow -d techflow -v ON_ERROR_STOP=1 < "$f"
done
```

### 3. Configure environment ✅

```bash
cp .env.example .env
```

Then edit `.env`. These are the minimum values needed to boot the API without Kafka:

```dotenv
DATABASE_URL=postgresql://techflow:techflow@localhost:5433/techflow
ENABLE_KAFKA=false
DEEPSEEK_API_KEY=<your key>     # or OPENROUTER_API_KEY — at least one is required or startup fails
API_KEY=local-dev-key           # value clients must send as X-API-Key
# optional: GEMINI_API_KEY=<key> to enable semantic KB search
```

### 4. Run the API ✅

```bash
uv run uvicorn api.main:app --reload --port 8000
# or: uv run python main.py      (RUN_MODE defaults to "api")

curl localhost:8000/health
# {"status":"healthy","db":"ok","kafka":"disabled"}
curl -H "X-API-Key: $API_KEY" "localhost:8000/tickets?limit=2"   # the API_KEY value from .env
```

### 5. Run the worker (needs Kafka) ⚠️ not run during verification

```bash
docker compose up -d zookeeper kafka
# in .env: ENABLE_KAFKA=true, KAFKA_BOOTSTRAP_SERVERS=localhost:9092
RUN_MODE=worker uv run python main.py
```

Gmail polling also needs `gmail_credentials.json` and an OAuth token. Run
`uv run python scripts/gmail_auth.py` to create one.

### 6. Run the web form ✅ (build verified; dev server not started)

```bash
cd web-form
npm ci
cp .env.local.example .env.local
# set API_KEY_SECRET in .env.local to the SAME value as the backend's API_KEY
npm run dev                      # http://localhost:3000
```

### 7. Run tests ✅

```bash
uv run python -m pytest -m "not slow and not integration and not e2e"   # what CI runs
uv run python -m pytest          # everything; tests that need a live stack skip without one
bash scripts/setup_e2e.sh        # full e2e: compose postgres+kafka, Playwright, live API
```

Tests that need live services skip unless you point them at one:

| Variable | Used by | Value |
|---|---|---|
| `OUTBOUND_TEST_DATABASE_URL` (or `DATABASE_URL`) | `test_notification_sender.py`, `test_kb_category.py` | Any PostgreSQL with pgvector; each test uses a throwaway schema |
| `OUTBOUND_TEST_KAFKA` | Kafka test in `test_notification_sender.py` (`integration`) | e.g. `localhost:9092` |
| `E2E_API_KEY` | `test_e2e_playwright.py` (`e2e`, needs the API on `:8000` and `playwright install chromium`) | Optional. Falls back to `API_KEY_SECRET` (export it from `.env`). Set it when the server uses `API_KEY` or another key; `tests/conftest.py` overwrites `API_KEY` with the in-process test key, so the tests can't read it |

### Full stack with Docker Compose ⚠️ not run, and fails as written

`docker compose up --build` starts Postgres, ZooKeeper, Kafka, the API, the worker, the web form,
postgres-exporter, Prometheus, and Grafana (on port 3001). The `web-form` image **fails to build** on a
clean checkout because `web-form/public/` is missing. See Known Issues.

---

## Environment Variables

Precedence (`env_config.py`): shell env > `.env.<ENVIRONMENT>` > `.env`. `ENVIRONMENT` defaults to
`development`.

### Core / API

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | Picks which `.env.<ENVIRONMENT>` file to layer on top of `.env` |
| `RUN_MODE` | `api` | `worker` runs the message processor; any other value runs the API (`main.py`) |
| `API_HOST` / `API_PORT` / `API_RELOAD` | `0.0.0.0` / `8000` / `false` | uvicorn settings used by `main.py` |
| `PORT` | `8000` | Port used by the Dockerfile CMD (for Render) |
| `API_KEY` | — | Required `X-API-Key` value. Checked first. |
| `API_KEY_SECRET` | — | Read when `API_KEY` is unset. With neither set the API refuses to start (except `RUN_MODE=test`). The web form's server route uses it to call the API. |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:8000` | Comma-separated list of allowed origins |
| `DEBUG` | empty | Truthy values add tracebacks to error responses |
| `RATE_LIMIT_PER_MINUTE` | `100` | Global per-IP limit |
| `STRICT_RATE_LIMIT_PER_MINUTE` | `10` | Limit for webhooks, reply, ingest, and merge |
| `REDIS_URL` | empty (in-memory) | Shared rate-limit storage, e.g. `redis://localhost:6379/0` |

### Database

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | differs per entrypoint (see Known Issues) | Postgres DSN, e.g. `postgresql://techflow:techflow@localhost:5433/techflow` |
| `DATABASE_POOL_MIN` / `DATABASE_POOL_MAX` | `1` / `5` | asyncpg pool size |
| `DATABASE_SSL` | `disable` | Passed to asyncpg as `ssl=` (`require` on Render/Neon) |

### LLM and embeddings

| Variable | Default | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | When set, DeepSeek is the chat provider |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | `https://api.deepseek.com` / `deepseek-chat` | |
| `OPENROUTER_API_KEY` | — | Chat provider when DeepSeek isn't set |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | |
| `OPENAI_MODEL` | `openai/gpt-4o` | Chat model used on OpenRouter |
| `GEMINI_API_KEY` | — | When set, embeddings go to Gemini |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` | |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | Requested with `dimensions=1536` |
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Embedding model used when Gemini isn't set |

### Voice

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — | Groq Whisper STT and Groq chat |
| `OPENAI_API_KEY` | — | OpenAI Whisper fallback and OpenAI TTS |
| `STT_PROVIDER` / `TTS_PROVIDER` | `auto` / `auto` | `auto, groq, openai` / `auto, openai, gtts, none` |
| `STT_MODEL` / `STT_FALLBACK_MODEL` | `whisper-large-v3-turbo` / `whisper-1` | |
| `STT_CONFIDENCE_THRESHOLD` | `0.5` | Below this, the agent asks the caller to repeat |
| `TTS_MODEL` / `TTS_VOICE` | `tts-1` / `alloy` | |
| `GROQ_CHAT_MODEL` | `llama-3.3-70b-versatile` | Fallback model for voice translation |

### Kafka

| Variable | Default | Purpose |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Use `kafka:29092` inside compose |
| `ENABLE_KAFKA` | `true` | `false` turns on degraded mode |
| `KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | Any other value turns on SASL |
| `KAFKA_SASL_MECHANISM` / `KAFKA_SASL_USERNAME` / `KAFKA_SASL_PASSWORD` | `PLAIN` / `""` / `""` | |

### Channels

| Variable | Default | Purpose |
|---|---|---|
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_NUMBER` | — | Twilio credentials. The token is needed to verify WhatsApp webhook signatures. |
| `TWILIO_WHATSAPP_WEBHOOK_URL` (or `TWILIO_WEBHOOK_URL`) | the request URL | Public URL Twilio signs; set it when a proxy terminates TLS |
| `REQUIRE_TWILIO_SIGNATURE` | `true` | Reject WhatsApp webhooks that cannot be verified (no token). Set `false` only for local testing |
| `GMAIL_CREDENTIALS_FILE` / `GMAIL_TOKEN_FILE` | `gmail_credentials.json` / `gmail_token.json` | Gmail OAuth files |

### Identity resolution & circuit breakers

| Variable | Default | Purpose |
|---|---|---|
| `EMAIL_FUZZY_MAX_DISTANCE` | `2` | Max Levenshtein distance on the email local part |
| `NAME_FUZZY_THRESHOLD` / `NAME_FUZZY_AMBIGUITY_MARGIN` | `0.6` / `0.08` | Name trigram match (flags for review only) |
| `CB_<NAME>_FAILURE_THRESHOLD` | `5` | Applies to `OPENAI`, `KAFKA`, `TWILIO`, `GMAIL`, `EMBEDDINGS` |
| `CB_<NAME>_RECOVERY_TIMEOUT` / `CB_<NAME>_HALF_OPEN_MAX_CALLS` | `30.0` / `1` | |

### Frontend (`web-form/.env.local`; `NEXT_PUBLIC_*` values are baked in at build time)

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend URL the browser calls |
| `NEXT_PUBLIC_SUPPORT_EMAIL` | — | mailto link on the success screen |
| `NEXT_PUBLIC_COMPANY_NAME` | — | Set by deploy configs, but no source file reads it |
| `API_KEY_SECRET` | — (route returns 500 if it's missing) | Server-side key for proxying `GET /tickets/{id}` |

### Tooling-only

- **Example clients:** `SPECIFYPLUS_URL`, `SPECIFYPLUS_API_KEY`
- **Chaos:** `TECHFLOW_API_URL`, `TECHFLOW_API_KEY`, `TECHFLOW_DOCKER_NETWORK`, `TECHFLOW_ENV`,
  `TECHFLOW_CONFIRM_CHAOS`, `KUBECONFIG_CONTEXT`, `CHAOS_*`
- **Compose:** `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `GRAFANA_USER`, `GRAFANA_PASSWORD`,
  `COMPANY_NAME`, `SUPPORT_EMAIL`

> `.env.example` also lists about 28 variables that **no code reads**, including `ENABLE_GMAIL_POLLING`,
> `ENABLE_VOICE`, `ENABLE_WEB_FORM`, `LOG_LEVEL`, `API_WORKERS`, `WORKER_MODE`, `GMAIL_CLIENT_ID`, and
> `METRICS_WINDOW_MINUTES`. Setting them does nothing.

---

## Scripts & Commands

### Backend

| Command | What it does |
|---|---|
| `uv run python main.py` | Starts the API, or the worker when `RUN_MODE=worker` |
| `uv run uvicorn api.main:app --reload` | Starts the API directly |
| `uv run python -m workers.message_processor` | Starts the worker directly |
| `uv run python -m database.seed [--migrations-only]` | Applies schema and migrations (tracked in `schema_migrations`), seeds baseline data, and backfills KB embeddings. `--migrations-only` currently still seeds. |
| `bash scripts/render_migrate.sh` | Idempotent `psql` migration runner (tracked in `_migrations_applied`); used by Render |
| `bash scripts/setup_e2e.sh [--keep] [--skip-run]` | Brings up Postgres and Kafka, creates topics, installs Playwright, starts the API, runs the Playwright e2e tests |
| `uv run python scripts/gmail_auth.py [--console]` | Gmail OAuth flow that writes `gmail_token.json` |
| `uv run python scripts/load_test.py --base-url … --requests N --concurrency C` | aiohttp load generator |
| `uv run locust -f tests/locustfile.py` | Locust load test |
| `uv run python scripts/test_gemini.py` | Live smoke test for Gemini embeddings and chat |
| `bash scripts/voice_mic_test.sh` | Records from the mic (`arecord`), posts to `/webhooks/voice/message`, plays the reply (`ffplay`) |
| `bash scripts/check-secrets.sh pre-commit\|pre-push` | Local secret scan; turn on with `git config core.hooksPath .githooks` |
| `uv run python -m chaos.runner -e 01..06\|all [-n] [-y] [-o out.json]` | Chaos experiments against the compose stack |
| `uv run pytest` / `ruff check .` / `black --check .` / `mypy .` | Tests, lint, format check, type check |

### Frontend (`web-form/package.json`)

| Script | What it does |
|---|---|
| `npm run dev` | `next dev` on port 3000 |
| `npm run build` | `next build` (standalone output) ✅ verified |
| `npm run start` | `next start` |
| `npm run lint` | `next lint`. ⚠️ No ESLint config exists, so it opens an interactive prompt. |

---

## API Endpoints

All endpoints are in `api/main.py`. Every route needs the `X-API-Key` header **except** `/health`,
`/webhooks/whatsapp`, `/webhooks/webform`, `/webhooks/voice/message`,
`/webhooks/voice/call`, and the WebSocket. The default rate limit is 100/min per IP. *Strict* means
10/min.
Limits are per process unless `REDIS_URL` is set, and behind the web-form proxy or an ingress
every user shares one bucket: see [docs/RATE_LIMITING.md](docs/RATE_LIMITING.md).

Error responses use the shape `{"error": "<CODE>", "message": "...", "details"?}`, with status 401,
404, 422, 429, 500, 502, or 503.

### Health & metrics

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/health` | — | `{status: "healthy"\|"degraded", db: "ok"\|"error", kafka: "ok"\|"disabled"\|"error"}` |
| GET | `:9100/metrics` (internal port `METRICS_PORT`, not the API port) | — | Prometheus text format; not exposed by ingress, Render or the compose host mapping |
| GET | `/metrics/summary` | `?hours=24` | `{period_hours, metrics, count}` |
| GET | `/metrics/dashboard` | — | `{status_counts, avg_resolution_hours, escalation_rate_percent, channel_counts, total_tickets}` |

### Tickets

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/tickets` (20/min) | `{name, email, subject, category="general", priority="medium", message}` | `201 {ticket_number, ticket_id, status, created_at}`. Doesn't run the agent. |
| GET | `/tickets` | `?status=&limit=20 (1-100)&offset=0` | `{tickets, total, page, limit}` |
| GET | `/tickets/{id}` | UUID or `TKT-…` | ticket + `messages[]` + `agent_runs[]`; 404 if not found |
| PATCH | `/tickets/{uuid}/status` | `{status}` | updated ticket; broadcasts `ticket_update` |
| GET | `/tickets/{uuid}/messages` | `?limit=50 (1-200)` | `{ticket_id, messages, count}` |
| POST | `/tickets/{uuid}/reply` (strict) | `{message}` (1-5000 chars) | `201 {message_id, sent_at}`. Stored only, not sent on any channel. |
| WS | `/ws/tickets/{uuid}` | — | Pushes `{"event": "ticket_update"\|"new_message", "data": {...}}` |

### Webhooks (public)

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/webhooks/webform` (strict) | `{name, email, subject, message (10-1000), category, priority, company?, phone?}` | `201 {ticket_number, message, estimated_response, tracking_url}` |
| POST | `/webhooks/whatsapp` (strict) | Twilio form: `From, Body, MessageSid, AccountSid, NumMedia, MediaUrl0` | `{"status": "received"}`; 403 on a bad signature |
| POST | `/webhooks/voice/message` (strict) | `{audio_base64? \| audio_url?, filename, content_type?, language?, name?, email?, phone?}` | `{transcript, language, confidence, needs_clarification, agent_response, audio_base64, ticket_number, …}` |
| POST | `/webhooks/voice/call` (strict) | Twilio form: `SpeechResult, Confidence, From` | TwiML XML |

### Voice utilities

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/voice/transcribe` (strict) | same as `/webhooks/voice/message` | STT and translation result, without running the agent |
| POST | `/voice/translate` (strict) | `{text, target_language?}` | `{text, target_language}` or `{translated, language, was_translated}` |

### Customers

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/customers/{email}/history` (30/min) | `?limit=10&include_resolved=true` | `{email, tickets, count}` |
| GET | `/customers/review-queue` (30/min) | `?limit=50&offset=0` | `{queue, count, limit, offset}` |
| POST | `/customers/{uuid}/merge` (strict) | `{source_customer_id, reason?}` | `{merged, target_customer_id, source_customer_id, moved, customer, …}` |
| POST | `/customers/{uuid}/review/dismiss` (30/min) | — | `{dismissed: true, customer}` |

### Knowledge base

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/knowledge-base` | `?q=…&category=&limit=5&customer_tier=starter` | `{query, results, count, search_mode: "vector"\|"text", degraded?, error?}` |
| POST | `/knowledge-base/ingest` (strict) | `{title, content, category, tags[], embedding[]}` | `201 {id, title, embedded}`. Doesn't compute an embedding; the caller supplies it. |

Interactive docs are at `/docs`, which also needs `X-API-Key`.

---

## Database

PostgreSQL 16 with the `vector`, `uuid-ossp`, `pg_trgm`, and `fuzzystrmatch` extensions. Every primary
key is a UUID, and every foreign key uses `ON DELETE CASCADE`.

```mermaid
erDiagram
    customers ||--o{ tickets : has
    customers ||--o{ messages : sends
    customers ||--o{ agent_runs : ""
    customers ||--o{ customer_identifiers : "known by"
    tickets ||--o{ messages : contains
    tickets ||--o{ agent_runs : "processed by"

    customers { uuid id PK
      varchar email UK
      varchar name
      varchar company
      varchar tier "starter|growth|enterprise"
      jsonb metadata "identity-review flags" }
    customer_identifiers { uuid id PK
      uuid customer_id FK
      varchar identifier_type "email|phone|web_session"
      varchar identifier_value "UNIQUE(type,value)" }
    tickets { uuid id PK
      varchar ticket_number UK "TKT-YYYYMMDD-XXXXXX"
      uuid customer_id FK
      varchar subject
      varchar category
      varchar priority "low|medium|high|critical"
      varchar status "open|in_progress|resolved|escalated|closed"
      varchar channel "email|whatsapp|webform|voice|api"
      timestamp resolved_at
      varchar assigned_to }
    messages { uuid id PK
      uuid ticket_id FK
      uuid customer_id FK
      varchar direction "inbound|outbound"
      text content
      varchar channel
      float sentiment_score "0..1"
      jsonb metadata "emotion, urgency, aspects" }
    agent_runs { uuid id PK
      uuid ticket_id FK
      uuid customer_id FK
      text input_message
      text output_message
      varchar model
      text_array tool_calls
      varchar status "pending|running|completed|failed"
      jsonb result
      int duration_ms }
    knowledge_base { uuid id PK
      varchar title "UNIQUE (mig 010)"
      text content
      varchar category
      text_array tags
      vector embedding "1536, HNSW cosine"
      text embedding_model "mig 009"
      varchar tier "all|starter|growth|enterprise" }
    metrics { uuid id PK
      timestamp timestamp
      varchar metric_name
      float metric_value
      varchar metric_type
      jsonb labels }
```

### Migrations (`database/migrations/`)

| File | Change |
|---|---|
| 001 | Seed data: 10 customers, tickets, 10 KB articles, agent runs, metrics. No DDL. |
| 002 | `customer_identifiers` table, plus a backfill of email identifiers |
| 003 | `messages.sentiment_score` |
| 004 | Multi-channel seed: phone and web-session identifiers, a WhatsApp ticket |
| 005 | Comment on `messages.metadata` only |
| 006 | `pg_trgm` GIN indexes on `customers.name` and `customers.email` |
| 007 | Channel CHECK constraints extended with `voice` and `api` |
| 008 | `fuzzystrmatch` extension |
| 009 | `knowledge_base.embedding_model` and a partial index |
| 010 | Dedupes KB titles and adds a unique index on `title` |

**How migrations get applied:**

- **Fresh database:** run `schema.sql`, then `001`–`010` in order. This order was verified clean on
  `pgvector:0.8.0-pg16`.
- **Render:** `scripts/render_migrate.sh`.
- **Kubernetes:** `python -m database.seed` (`k8s/job-db-init.yaml`).
- **Docker Compose:** only `schema.sql` and `001`. You need to apply the rest by hand.

---

## Deployment

### Docker

- **`Dockerfile`:** two stages on `python:3.13-slim`. Runs `pip install -r requirements.txt`, which
  includes dev tools, and runs as non-root `appuser`. CMD is
  `uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}`.
- **`web-form/Dockerfile`:** three stages on `node:20-alpine` with Next.js standalone output.
  `NEXT_PUBLIC_*` values are build args.

### Docker Compose

`docker-compose.yml` defines these services:

| Service | Port(s) |
|---|---|
| postgres | 5433 |
| zookeeper | 2181 |
| kafka | 9092 (host), 29092 (internal) |
| api | 8000 (runs with `--reload` and a bind mount) |
| worker | — |
| web-form | 3000 |
| postgres-exporter | 9187 |
| prometheus | 9090 |
| grafana | 3001 |

`docker-compose.kraft.yml` is a single-node Kafka in KRaft mode, with no ZooKeeper.

### Kubernetes (`k8s/`, namespace `techflow`)

**Deployments**

| Deployment | Image | Replicas | Scaling |
|---|---|---|---|
| api | `techflow-api:latest` | 2 | HPA 2–5 at 70% CPU; PDB `minAvailable: 1` |
| worker | `techflow-api:latest` with `RUN_MODE=worker` | 2 | KEDA `ScaledObject`, 2–10 on Kafka lag and CPU; PDB |
| web-form | `ghcr.io/specifyplus/web-form:latest` | 2 | — |

**Other manifests**

- Kafka StatefulSet (3 brokers) and ZooKeeper
- Postgres StatefulSet
- DB init Job (`python -m database.seed`)
- nginx Ingress (`techflow.example.com`, a placeholder host)
- OCI block storage class

Apply order: `namespace` → `storage-class` → secrets (the secrets manifest is not in the repo; create
`techflow-secrets` yourself) → `configmap` → `postgres` → `kafka` → `job-db-init` → deployments,
services, HPA, PDB → `keda-install` and `scaledobject-worker` → `ingress`.

### Render (`render.yaml`)

| Resource | Type | Notes |
|---|---|---|
| `techflow-db` | Free Postgres | |
| `techflow-api` | Docker web service | `preDeployCommand: bash scripts/render_migrate.sh`, `ENABLE_KAFKA=false`, `DATABASE_SSL=require` |
| `techflow-worker` | Docker worker | Runs `python main.py` with `RUN_MODE=worker` |
| `techflow-webform` | Next.js web service | `rootDir: web-form` |

### CI/CD (`.github/workflows/`)

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | Push to `main`/`develop`, PRs to `main` | Backend: ruff, black, mypy (non-blocking), pytest with a pgvector service. Frontend: `npm ci`, lint, build. |
| `cd.yml` | Push to `main` | Builds and pushes `ghcr.io/<repo>/api` and `ghcr.io/<repo>/web-form` (`:sha`, `:latest`), then `kubectl set image` with `secrets.KUBE_CONFIG` |
| `chaos.yml` | Weekly cron (Mon 03:00) or manual | Runs `chaos.runner` against the `staging` environment |
| `secret-scan.yml` | Every push and PR | gitleaks over full history, plus a forbidden-filename check |

---

## Known Issues

Found while reading the code and running it. The ones marked **(verified)** were reproduced on this
machine. The items marked ✅ were fixed on branch `fix/known-issues`; see
[`FIX_REPORT.md`](FIX_REPORT.md) for how each fix was verified.

### Fixed on `fix/known-issues`

- ✅ **The web form Docker image didn't build** (`public/` missing; the npm 11 lockfile broke
  `npm ci` on Node 20).
- ✅ **Email and WhatsApp replies were never delivered.** `workers/notification_sender.py` now
  consumes `notifications.outbound` with retries, a DLQ, and idempotency (`outbound_deliveries`,
  migration 011). Real delivery still needs a Gmail token and Twilio credentials; it was verified
  with mocked providers.
- ✅ **Compose applied only `schema.sql` + `001`.** The new `migrate` and `kb-embed` services apply
  schema + all migrations idempotently and embed the KB.
- ✅ **The k8s worker ran uvicorn, and KEDA watched the wrong topic and group.**
- ✅ **CORS preflight returned 401.** Origins now come from `CORS_ORIGINS`.
- ✅ **`npm run lint` was interactive.**
- ✅ **The browser couldn't reach the API in Compose, and the web form had no `API_KEY_SECRET`
  there.** This is fixed for Compose only; k8s and Render still have the problem (item 6 below).
- ✅ **Security:**
  - The `test-key-12345` fallback is removed.
  - The Twilio signature is required on `/webhooks/voice/call`, and the API key on
    `/webhooks/voice/message`.
  - An SSRF guard now protects `audio_url`.
- ✅ **Agent replies read as operator notes.** Only the `send_response` body is delivered now
  (`customer_reply`); the agent's final message travels separately as `internal_note`, and a guard
  (`agent/reply_guard.py`) blocks third-person/narrating text and escalates instead.
- ✅ **The form category emptied KB search.** `general`/`feedback` no longer filter, `bug` maps to
  `technical`, and an empty filtered search retries the whole KB.
- ✅ **The sentiment gate escalated calm technical questions.** Failure words ("error", "broken",
  "429 failing") no longer count as negative; explicit hostility and critical incidents escalate.
  Eval (`python -m agent.gate_eval`, 42 messages): precision 0.54→0.95, recall 0.75→1.00.
- ✅ **The Playwright tests are marked `e2e`** and read `E2E_API_KEY` (falls back to `API_KEY_SECRET`).
- ✅ **`uv.lock` is committed;** `uv sync --frozen` works.

### Broken or non-functional

1. **Missing Gmail token/credential files become root-owned directories** through the compose bind
   mounts.
2. **In Kubernetes, the worker liveness/readiness probes are no-ops** (`sys.exit(0)`), and the
   worker Deployment lacks `GEMINI_*`/`DEEPSEEK_*` env.
3. **The browser can't reach the API in Kubernetes or Render.** `NEXT_PUBLIC_API_URL` is baked into
   the browser bundle as a cluster-internal name, and `API_INTERNAL_URL`/`API_KEY_SECRET` aren't set
   for the web form there. Compose is fixed.
4. **WhatsApp signature validation uses `request.url`,** so it fails behind a TLS-terminating
   proxy/ingress. The voice webhook already has a `TWILIO_VOICE_WEBHOOK_URL` override; WhatsApp has none.
5. **`KafkaProducerClient` retries only connection/timeout errors,** so the first publish to a cold
   broker can fail.
6. **The k8s Ingress routes `/api` to the backend,** which has no `/api` prefix. It also shadows the
   Next.js `/api/tickets/[id]` route.
7. **WhatsApp messages are silently dropped when `ENABLE_KAFKA=false`,** for example on Render, which
   ships with Kafka off.
8. **`database/seed.py --migrations-only` still seeds data and backfills embeddings.** The flag only
   changes log text.
9. **Two migration runners keep separate tracking tables** (`schema_migrations` in `seed.py` and
   `_migrations_applied` in `render_migrate.sh`). Running both on the same DB re-applies the
   non-idempotent `001`, which then fails on the unique title index.
10. **Gate regexes are partly fixed.** The legal stems now match ("lawsuit", "litigation"), but the
    emotion stems in `agent/sentiment_analyzer.py` still end in `\b` ("frustrated" is missed), and the
    pricing patterns are too broad: any question mentioning "bill", "invoice" or "cost" escalates.
11. **Every agent run is recorded as `completed`,** even when it fails (`queries.complete_agent_run`),
    so failure metrics always read 0.
12. **`chaos.yml` can't work.** It runs on a fresh GitHub runner and health-checks `localhost:8000`
    without starting any stack. Chaos experiment 01 also expects a restart policy that compose doesn't
    define.
13. **The Grafana dashboard isn't loaded.** There are no provisioning files, and the login defaults to
    `admin/admin`.
14. **Two producers still publish to `notifications.outbound`:** the `send_response` tool event uses
    `content`, the final reply uses `customer_reply`. Only the latter is delivered.

### Security and risk

- **Public endpoints that cost money:**
  - `/webhooks/webform` is public, and each request triggers LLM spend (rate limit 10/min per IP).
  - WhatsApp webhooks without a verifiable signature are refused unless `REQUIRE_TWILIO_SIGNATURE=false`.
- **`audio_url` DNS-rebinding window:** `utils/safe_fetch.py` resolves the host and then connects
  separately. The host allowlist mitigates this.
- **`database/seed.py` logs `db_url`, password included.**
- **WebSocket `/ws/tickets/{uuid}` has no auth,** so anyone with a ticket UUID can subscribe.
- **No upper bound** on `limit` for `/customers/{email}/history` or on `hours` for `/metrics/summary`.
- **The worker logs the full `DATABASE_URL`, credentials included** (`workers/message_processor.py`).
- **The production image installs dev tooling** (pytest, locust, playwright, black, mypy), because
  `requirements.txt` mixes runtime and dev dependencies.
- **`:latest` image tags** in k8s. `techflow-api:latest` isn't in any registry, while CD pushes to
  `ghcr.io/<repo>/api`. `pgvector/pgvector:pg16-latest` is used in `k8s/postgres.yaml`.
- **`npm audit` reports vulnerabilities** in the installed `next@14.2.x` tree, including critical and
  high advisories.

### Hardcoded, inconsistent, or unused

- **`DATABASE_URL` defaults differ by entrypoint:**
  - API: `postgresql://techflow:techflow@localhost/techflow`
  - worker: `postgresql://localhost/techflow`
  - seed: `…@localhost:5432/techflow`
  - `.env.example`: `…/specifyplus`
- **Tracking URLs point at a fake domain and disagree with each other:**
  `https://support.techflow.com/track/…` vs `/tickets/…`.
- **API key defaults disagree:** `scripts/load_test.py` uses `test-api-key-2024`, while everything else
  uses `test-key-12345`.
- **Hardcoded placeholders:**
  - `SYSTEM_PROMPT` says "GPT-4o" even when DeepSeek is the provider.
  - `agent_runs.model` always defaults to `gpt-4o`.
  - The OG URL in `layout.tsx` is `http://localhost:3000`.
  - The Terms and Privacy links in the form are `href="#"`.
- **`mcp_server.py` isn't an MCP server.** It's an in-process registry, and `initialize_default_tools`
  is never called. `agent/tools_executor.py`, `embeddings_service.py`, most `metrics.py` counters, and
  several helpers in `formatters.py` and `prompts.py` are unused.
- **Worker metrics are invisible.** The worker process exposes no `/metrics`, so Prometheus never sees
  agent metrics produced there. Across the codebase, only the sentiment gauges are ever updated.
- **About 28 `.env.example` variables are never read** (feature flags, Gmail client ID and secret,
  `LOG_LEVEL`, …). About 25 variables the code does read are missing from `.env.example`, such as
  `RUN_MODE`, `API_KEY`, `REQUIRE_TWILIO_SIGNATURE`, `GMAIL_CREDENTIALS_FILE`, and `CB_*`.
- **`.env.example` misdescribes the loader.** It says unset `ENVIRONMENT` falls back to `.env`; in fact
  `ENVIRONMENT` defaults to `development`, and `.env.development` is layered on top of `.env`.
- **`pyproject.toml` lists `specs` under `only-include`,** but `specs/` isn't tracked in git.
- **Unused or deprecated config:**
  - `uv` is installed in the Docker builder but never used.
  - `kubectl --record` is deprecated (`cd.yml`).
  - `k8s/storage-class.yaml` defines `standard` twice.
  - `k8s/configmap.yaml` lists 2 of 3 Kafka brokers, and its `cors-origins` value is never wired in.
- **Duplicate messages:** in the synchronous web-form path, the reply can be stored twice, once by the
  `send_response` tool and once by `api/main.py`.
- **Gmail blocks the worker:** the handler uses blocking Google API calls inside async code, and
  `InstalledAppFlow.run_local_server` would hang a headless worker if no token exists.
- **The test count badge in the old README said 136.** On `main` the suite had 141 passing and 12
  skipped. On `fix/known-issues` it has 244 passing with the live stack (230 under the CI filter).

---

## Verification log

These were run on 2026-10-09 against a fresh `git clone` (Linux, Node 24.13, uv-managed Python
3.13.11).

| Step | Result |
|---|---|
| `uv sync --extra dev` | ✅ installed |
| `uv run pytest --ignore=tests/test_e2e_playwright.py` | ✅ **141 passed**, 17 warnings |
| `uv run pytest tests/test_e2e_playwright.py` | ⏭️ 12 skipped (no live stack) |
| `psql` `schema.sql` + migrations 001–010 on `pgvector/pgvector:0.8.0-pg16` | ✅ all applied |
| API boot (`ENABLE_KAFKA=false`, dummy LLM key) → `GET /health` | ✅ `{"status":"healthy","db":"ok","kafka":"disabled"}` |
| `GET /tickets` without a key / with a key | ✅ 401 / 200 with seeded tickets |
| `GET /knowledge-base?q=password` with an invalid LLM key | ✅ degrades to `search_mode: "text"` |
| CORS preflight `OPTIONS /tickets` | ❌ 401 (Known Issue 8) |
| API boot with no reachable DB | ❌ startup aborts. A DB is required. |
| `web-form`: `npm ci && npm run build` | ✅ builds (`/`, `/ticket/[id]`, `/api/tickets/[id]`) |
| `web-form`: `npm run lint` | ❌ interactive ESLint setup prompt |
| `web-form`: `docker build .` | ❌ `"/app/public": not found` |
| Worker, Kafka, full `docker compose up`, k8s, Render | ⚠️ Not run |

After the fixes on `fix/known-issues`, these were re-run on the same day:
- full `docker compose up --build` (all healthchecks green)
- Chrome E2E
- 12/12 Playwright tests live
- the worker + Kafka outbound flow
- `kubeconform` plus a server dry-run of the k8s manifests

Render was not run. Details are in [`FIX_REPORT.md`](FIX_REPORT.md).
