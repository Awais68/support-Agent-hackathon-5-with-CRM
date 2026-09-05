# TechFlow Analytics — CRM Digital FTE Factory

A complete "Customer Success Digital FTE" (AI agent replacing a human support employee) built with **OpenAI Agents SDK**, **FastAPI**, **Kafka**, **PostgreSQL + pgvector**, and **Kubernetes**.

## 🎯 Overview

Autonomous customer support system handling support inquiries across **4 channels** (Email, WhatsApp, Web Form, **Voice**) using GPT-4o agent with 5 specialized tools. Automatically creates tickets, searches knowledge bases, escalates complex issues, and maintains full conversation history. The **voice channel** adds speech-to-text, text-to-speech, and automatic translation so customers can call or leave a voice message in any language and the agent understands them on the first utterance.

## 🚀 Quick Start

### 1. Environment Setup

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 2. Start Services

```bash
docker compose up --build
```

This starts:
- **PostgreSQL** (port 5432)
- **Zookeeper + Kafka** (port 9092)
- **FastAPI API** (port 8000)
- **Message Processor Worker** (background)

### 3. Verify

```bash
curl http://localhost:8000/health
# {"status":"healthy","db":"ok","kafka":"ok"}
```

### 4. Test Web Form

```bash
curl -X POST http://localhost:8000/webhooks/webform \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John",
    "email": "john@example.com",
    "subject": "How do I set up a connector?",
    "message": "I need help setting up my first data connector.",
    "category": "onboarding",
    "priority": "medium"
  }'
```

## 📚 API Contract (14 Endpoints)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | System health check |
| POST | `/tickets` | Create ticket |
| GET | `/tickets` | List tickets (paginated) |
| GET | `/tickets/{id}` | Get ticket with messages + agent runs |
| PATCH | `/tickets/{id}/status` | Update ticket status |
| GET | `/tickets/{id}/messages` | Get conversation history |
| POST | `/tickets/{id}/reply` | Add message to ticket |
| POST | `/webhooks/whatsapp` | Twilio WhatsApp webhook |
| POST | `/webhooks/webform` | Web form submission |
| POST | `/webhooks/voice/message` | Voice message (audio → STT → agent → TTS reply) |
| POST | `/webhooks/voice/call` | Twilio voice call webhook (TwiML + speech gather) |
| POST | `/voice/transcribe` | STT-only: transcribe + translate audio |
| POST | `/voice/translate` | Detect language + translate (or translate to a target language) |
| GET | `/customers/{email}/history` | Get customer's ticket history |
| GET | `/metrics/summary` | Get metrics for last N hours |
| GET | `/metrics/dashboard` | Get aggregated dashboard metrics |
| GET | `/knowledge-base` | Search knowledge base |
| POST | `/knowledge-base/ingest` | Add KB article |

**Authentication**: All endpoints except `/health` and `/webhooks/*` require `X-API-Key` header.

## 🤖 Agent Tools (5)

1. **search_knowledge_base** — Find KB articles by query (pgvector similarity)
2. **create_ticket** — Generate TKT-YYYYMMDD-NNNN number & log issue
3. **get_customer_history** — Retrieve previous interactions
4. **escalate_to_human** — Route to human agent queue
5. **send_response** — Send channel-specific response (email/WhatsApp/webform)

## 🎙️ Voice Channel (calls + voice messages)

The voice channel lets customers **call in** or **leave a voice message** and talk to
the agent in any language — no typing required.

Pipeline: `audio → STT → language detect → translate → agent → translate back → TTS`

- **Speech-to-text** — Groq Whisper (`whisper-large-v3-turbo`, the most accurate
  real-time model) with automatic fallback to OpenAI Whisper. Raw transcripts are
  routed to the `inbound.voice` Kafka topic.
- **First-time accuracy** — a confidence gate (`STT_CONFIDENCE_THRESHOLD`, default
  `0.5`) guarantees the agent **never guesses**. If the transcript is unclear it
  asks the customer to repeat, echoing back what it heard.
- **NLP + translation** — language is detected and the request is translated to
  English for the agent; the reply is translated back into the customer's language
  and spoken via TTS (OpenAI TTS → gTTS offline fallback).
- **Voice calls** — the `/webhooks/voice/call` webhook returns TwiML using
  `<Gather input="speech">`; the agent's answer is spoken with `<Say>` in the
  customer's language.
- **Web form** — the frontend has a built-in recorder ("Talk to a Support Agent").
  No STT keys? Everything degrades gracefully to a clarification prompt.

```bash
# Voice message (base64 audio)
curl -X POST http://localhost:8000/webhooks/voice/message \
  -H "Content-Type: application/json" \
  -d '{"audio_base64":"<base64>","filename":"voice.webm","name":"Ali"}'

# Test translation
curl -X POST http://localhost:8000/voice/translate \
  -H "Content-Type: application/json" -H "X-API-Key: test-key-12345" \
  -d '{"text":"میں اپنا پاس ورڈ ری سیٹ کرنا چاہتا ہوں"}'
```

## 📊 Architecture

```
Customer Input (Email/WhatsApp/Web/Voice Call/Voice Message)
        ↓
  Channel Handlers  (+ VoiceHandler: STT → translate → TTS)
        ↓
  Kafka (10 Topics, incl. inbound.voice)
        ↓
  Message Processor Worker
        ↓
  OpenAI Agent (gpt-4o) + 5 Tools
        ↓
  PostgreSQL (6 Tables + pgvector)
        ↓
  Response Output
```

## 🔧 Configuration

### Database URL

```
postgresql://techflow:techflow@localhost/techflow
```

### Environment Variables

```bash
DATABASE_URL=postgresql://...
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
OPENAI_API_KEY=sk-...
API_KEY=test-key-12345
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_WHATSAPP_NUMBER=+1234567890
GMAIL_CREDENTIALS_FILE=gmail_credentials.json
CORS_ORIGINS=http://localhost:3000,http://localhost:8000

# Voice channel
GROQ_API_KEY=gsk_...          # STT (Whisper via Groq — fastest + most accurate)
OPENAI_API_KEY=sk-...         # TTS (and STT fallback)
STT_CONFIDENCE_THRESHOLD=0.5  # below this the agent asks to repeat, never guesses
TTS_PROVIDER=auto             # openai | gtts | none
```

## 📋 Project Structure

```
specifyplus/
├── api/                    # FastAPI application
├── agent/                  # Agent orchestration
├── channels/               # Email/WhatsApp/Web handlers
├── database/               # PostgreSQL layer
├── workers/                # Message processor + metrics
├── context/                # Static context data
├── tests/                  # Unit/integration/load tests
├── k8s/                    # Kubernetes manifests
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## 🧪 Testing

```bash
# Unit + integration tests (no infrastructure required)
pytest tests/test_agent.py tests/test_voice.py -v

# API e2e tests (validation/auth — no infrastructure required)
pytest tests/test_e2e.py -v

# Full end-to-end tests (real Postgres + Kafka + API server + Playwright)
./scripts/setup_e2e.sh

# Load test
locust -f tests/load_test.py --headless -u 50 -r 5 --run-time 60s
```

`tests/test_e2e_playwright.py` needs a live stack: PostgreSQL, Kafka, the API on
`http://localhost:8000`, and Chromium. `scripts/setup_e2e.sh` brings it all up and
runs the suite. If any prerequisite is missing the suite **skips gracefully**
instead of failing collection.

## 🚀 Deployment

### Docker Compose
```bash
docker compose up --build
```

### Kubernetes (apply order)

```bash
# 1. Namespace must exist first
kubectl apply -f k8s/namespace.yaml

# 2. Config & secrets (no runtime deps)
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/configmap.yaml

# 3. Data layer (PostgreSQL + Kafka)
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/kafka.yaml

# 4. Database schema init (runs after postgres is ready)
kubectl apply -f k8s/job-db-init.yaml

# 5. Application deployments (depend on DB + Kafka)
kubectl apply -f k8s/deployment-api.yaml
kubectl apply -f k8s/deployment-worker.yaml

# 6. Autoscaling
kubectl apply -f k8s/hpa-api.yaml

# 7. Networking (Service + Ingress)
kubectl apply -f k8s/service-api.yaml
kubectl apply -f k8s/ingress.yaml
```

> **Note**: `k8s/ingress.yaml` contains placeholder values for domain name and TLS issuer.
> Edit `spec.tls.hosts`, `spec.rules[0].host`, and the cert-manager annotation before applying.
>
> All secrets in `k8s/secrets.yaml` must be replaced with real base64-encoded values before deployment.
> See the comments in that file for the exact list of values that need replacement.

## 🔒 Security

- ✅ API key authentication
- ✅ Twilio webhook signature validation
- ✅ `.env` in `.gitignore`
- ✅ Non-root Docker user
- ✅ Kubernetes secrets for credentials

## 📊 Metrics Tracked

- Ticket counts & status distribution
- Resolution times (avg/median)
- Agent success rate & token usage
- Channel usage distribution
- Escalation rate %

All metrics published to Kafka and stored in PostgreSQL for analysis.

## 🆘 Troubleshooting

**Database connection error**:
```bash
docker compose logs postgres
docker compose ps postgres  # Should be "healthy"
```

**Kafka connection error**:
```bash
docker compose exec kafka kafka-broker-api-versions.sh --bootstrap-server localhost:9092
```

**OpenAI API error**:
```bash
# Check API key is set
echo $OPENAI_API_KEY
```

## ✨ Tech Stack

- **Framework**: FastAPI (Python)
- **Agent**: OpenAI Agents SDK (gpt-4o)
- **Database**: PostgreSQL + pgvector (vector similarity)
- **Message Queue**: Kafka (9 topics)
- **Async**: asyncpg, aiokafka, httpx
- **Containerization**: Docker + Docker Compose
- **Orchestration**: Kubernetes (with HPA)
- **Monitoring**: structlog + Kafka metrics

---

**Built for the TechFlow Analytics Customer Success team** 🚀
