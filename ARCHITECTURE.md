# TechFlow CRM Digital FTE — Architecture

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Customer Channels                        │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │   Gmail    │  │   WhatsApp   │  │  Web Form    │             │
│  │  (Polling) │  │  (Webhook)   │  │  (REST API)  │             │
│  └──────┬─────┘  └──────┬───────┘  └──────┬───────┘             │
└─────────┼───────────────┼─────────────────┼──────────────────────┘
          │               │                 │
          ▼               ▼                 ▼
┌──────────────────────────────────────────────────────────────────┐
│                        Kafka (9 Topics)                         │
│  ┌──────────────┐ ┌───────────────┐ ┌──────────────┐            │
│  │ inbound.email│ │inbound.whatsap│ │inbound.webfor│            │
│  └──────┬───────┘ └───────┬───────┘ └──────┬───────┘            │
│  ┌──────────────────┐ ┌─────────────────┐ ┌──────────────┐     │
│  │ agent.processing │ │agent.completed  │ │ escalations  │     │
│  └────────┬─────────┘ └────────┬────────┘ └──────┬───────┘     │
│  ┌──────────────────┐ ┌────────────────┐ ┌──────────────────┐  │
│  │notifications.    │ │ metrics.events │ │      dlq         │  │
│  │ outbound         │ │                │ │                  │  │
│  └──────────────────┘ └────────────────┘ └──────────────────┘  │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Message Processor Worker                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │   CustomerSuccessAgent (OpenAI Agents SDK)               │   │
│  │   ┌─────────────────────────────────────────────────┐   │   │
│  │   │         Pre-Processing Gate                      │   │   │
│  │   │  • Pricing/refund check → Escalate               │   │   │
│  │   │  • Legal detection     → Escalate               │   │   │
│  │   │  • Internal details    → Deflect                │   │   │
│  │   │  • Low sentiment       → Escalate               │   │   │
│  │   └─────────────────────┬───────────────────────────┘   │   │
│  │                         ▼                               │   │
│  │   ┌─────────────────────────────────────────────────┐   │   │
│  │   │           Tool Execution Loop                   │   │   │
│  │   │  • search_knowledge_base  (pgvector similarity) │   │   │
│  │   │  • create_ticket          (TKT-* numbering)     │   │   │
│  │   │  • get_customer_history   (cross-channel)       │   │   │
│  │   │  • escalate_to_human      (high priority queue) │   │   │
│  │   │  • send_response          (channel-specific)    │   │   │
│  │   └─────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                   PostgreSQL + pgvector                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │
│  │ customers│ │ tickets  │ │ messages │ │ customer_identifi│   │
│  ├──────────┤ ├──────────┤ ├──────────┤ ├──────────────────┤   │
│  │ 7 cols   │ │ 10 cols  │ │ 9 cols   │ │ 4 cols           │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                        │
│  │knowledge_│ │agent_runs│ │ metrics  │                        │
│  │ base     │ │          │ │          │                        │
│  └──────────┘ └──────────┘ └──────────┘                        │
└──────────────────────────────────────────────────────────────────┘
```

## Data Flow

### Identity Resolution (WS2)

```
Email arrives from john@example.com
  ↓
get_customer_or_create_by_identifier("email", "john@example.com")
  ↓
customer_identifiers table lookup
  ↓
Found → return existing customer
Not found → create customer + identifier
```

Cross-channel linking: a user with email `john@example.com` and phone `+1234567890` resolves to the same customer via `link_identifiers()`.

### Kafka Topic Structure

| Topic | Producer | Consumer | Purpose |
|-------|----------|----------|---------|
| `inbound.email` | Gmail poller | Worker | New email from customer |
| `inbound.whatsapp` | Twilio webhook | Worker | WhatsApp message |
| `inbound.webform` | Web form API | Worker | Web form submission |
| `agent.processing` | Worker | Agent | Trigger agent processing for a ticket |
| `agent.completed` | Agent | Worker | Agent finished processing result |
| `notifications.outbound` | Agent | Notification handler | External notifications (email/WhatsApp) |
| `escalations` | Agent | Human queue | High-priority human escalation |
| `metrics.events` | Worker/API | Metrics collector | All metric events |
| `dlq` | Producer | DLQ handler | Failed messages (dead letter queue) |

### Agent Decision Flow

```
1. Customer message arrives on inbound.* topic
2. MessageProcessor routes by topic → _process_email / _whatsapp / _webform
3. Identity resolution (customer lookup/create)
4. Ticket created in PostgreSQL
5. AgentContext built (db_pool, kafka_producer, openai_client)
6. build_agent() constructs CustomerSuccessAgent with 5 tools
7. Pre-processing Gate checks:
   - Contains pricing/refund keywords? → Escalate
   - Contains legal keywords? → Escalate
   - Requests internal details? → Deflect
   - Sentiment below -0.35? → Escalate
   - Sentiment above threshold? → Proceed to agent
8. Agent processes with tool execution loop
9. Response published to agent.completed / notifications.outbound / escalations
```

## Database Schema

### Tables

**customers** — 7 columns: `id (UUID PK)`, `email (UNIQUE)`, `name`, `company`, `tier`, `created_at`, `updated_at`

**tickets** — 10 columns: `id (UUID PK)`, `ticket_number (UNIQUE, TKT-YYYYMMDD-NNNN)`, `customer_id (FK)`, `subject`, `status`, `priority`, `category`, `channel`, `created_at`, `updated_at`

**messages** — 9 columns: `id (UUID PK)`, `ticket_id (FK)`, `sender`, `body`, `embedding (vector(384))`, `sentiment_label`, `sentiment_score`, `created_at`, `metadata`

**customer_identifiers** — 4 columns: `id (UUID PK)`, `customer_id (FK)`, `identifier_type (email/phone/web_session)`, `identifier_value`

**knowledge_base** — 6 columns: `id (UUID PK)`, `title`, `content`, `embedding (vector(384))`, `category`, `created_at`

**agent_runs** — 6 columns: `id (UUID PK)`, `ticket_id (FK)`, `action`, `tool_used`, `input_data`, `output_data`, `created_at`

**metrics** — 6 columns: `id (UUID PK)`, `metric_name`, `metric_value`, `metric_type (counter/gauge/histogram)`, `timestamp`, `metadata`

## Container Architecture

```
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│  Zookeeper   │◄───│    Kafka     │    │  PostgreSQL      │
│  (port 2181) │    │ (port 9092)  │    │ (port 5432 int)  │
└──────────────┘    └──────┬───────┘    │ (port 5433 ext)  │
                          │            └────────┬─────────┘
                          │                     │
                    ┌─────┴─────┐         ┌─────┴──────┐
                    │   API     │         │   Worker   │
                    │ (port 8000)│         │ (internal) │
                    └─────┬─────┘         └─────┬──────┘
                          │                     │
                    ┌─────┴─────┐         ┌─────┴──────┐
                    │ Prometheus│         │  Grafana   │
                    │ (port 9090)│         │ (port 3001) │
                    └───────────┘         └─────────────┘
```

All containers communicate via the `techflow` bridge network. Internal service discovery uses container names (e.g., `postgres:5432`, `kafka:29092`).

## Key Design Decisions

1. **Single worker process** for all 3 channels (not one per channel) — keeps coordination simple within a single async event loop
2. **Soft-fail channel initialization** — Gmail auth failure logs a warning but does not crash the worker; WhatsApp/Twilio webhook returns 200 even on errors
3. **DLQ routing** — failed Kafka messages are automatically routed to the `dlq` topic via `KafkaProducerClient.send_message()` catch block
4. **pgvector for semantic search** — KB articles are embedded with `text-embedding-3-small` (384 dimensions)
5. **TKT-YYYYMMDD-NNNN numbering** — daily resetting counter derived from PostgreSQL sequence
6. **5 retries on healthcheck** — prevents flapping during cold starts (Kafka, PostgreSQL)
