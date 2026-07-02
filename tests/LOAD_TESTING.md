# Load Testing — TechFlow CRM Digital FTE

## Prerequisites

- Python 3.11+ with `locust` installed (`pip install locust`)
- The stack must be running (see docker-compose.yml in the project root)

## Running Against Local Docker-Compose Stack

```bash
# Web UI mode (open http://localhost:8089)
locust -f tests/locustfile.py --host=http://localhost:8000

# Headless mode — 20 users, 5 hatch-rate/s, 1 minute run
locust -f tests/locustfile.py --headless -u 20 -r 5 -t 1m --host=http://localhost:8000
```

## Running Against a Deployed K8s Ingress URL

```bash
export INGRESS_URL=https://techflow.example.com

# Headless — simulate grading load (200+ req across all channels)
locust -f tests/locustfile.py --headless -u 20 -r 5 -t 1m --host=$INGRESS_URL
```

## Test Design

| Channel       | Task Weight | Target Requests | Endpoint               |
|---------------|-------------|-----------------|------------------------|
| Web Form      | 2           | 100+            | `POST /webhooks/webform` |
| Gmail         | 1           | 50+             | `POST /tickets`          |
| WhatsApp      | 1           | 50+             | `POST /webhooks/whatsapp` (form-encoded) |

## Metrics Tracked

- **P95 Latency** — flagged per-request when > 3s
- **Escalation Rate** — extracted from ticket status in responses (< 25% target)
- **Per-channel breakdown** — printed on test stop

## Dependency

A full pass requires a live stack (Postgres + Kafka + API worker). This
depends on Work Stream 5 (infrastructure/secret provisioning). Until real
API keys (Twilio, Gmail OAuth, OpenAI) are in place, some endpoints will
return degraded responses. The load test framework itself is independent
and will parse without import/config errors.
