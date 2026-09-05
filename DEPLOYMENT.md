# Deployment Guide

## Prerequisites

- Docker 24+ with Compose v2
- Kubernetes cluster (kind/minikube/EKS/GKE) for k8s deployment
- `kubectl` v1.28+
- OpenAI API key
- (Optional) Twilio account for WhatsApp
- (Optional) Gmail API credentials for email polling

## Local Deployment (Docker Compose)

### 1. Environment Configuration

```bash
cp .env.example .env
```

Required variables in `.env`:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | Postgres connection string | `postgresql://techflow:techflow@postgres:5432/techflow` |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker address | `kafka:29092` |
| `OPENAI_API_KEY` | OpenAI API key (sk-...) | *(required)* |
| `API_KEY` | API key for X-API-Key header | `test-key-12345` |
| `CORS_ORIGINS` | Allowed CORS origins | `http://localhost:3000,http://localhost:8000` |

Optional variables:

| Variable | Description |
|----------|-------------|
| `TWILIO_ACCOUNT_SID` | Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token |
| `TWILIO_WHATSAPP_NUMBER` | WhatsApp-enabled Twilio number |
| `GMAIL_CREDENTIALS_FILE` | Path to Gmail OAuth JSON |
| `GRAFANA_ADMIN_PASSWORD` | Grafana admin password (default: `admin`) |

> **Security**: `.env` is in `.gitignore`. Never commit real secrets.

### 2. Build and Start

```bash
docker compose up --build -d
```

This builds and starts 7 containers:
- `techflow-postgres` — PostgreSQL 16 with pgvector (port 5433 host / 5432 container)
- `techflow-zookeeper` — Zookeeper for Kafka coordination (port 2181)
- `techflow-kafka` — Kafka broker (port 9092)
- `techflow-api` — FastAPI with 14 endpoints (port 8000)
- `techflow-worker` — Background message processor
- `techflow-prometheus` — Metrics scraper (port 9090)
- `techflow-grafana` — Metrics dashboard (port 3001)

Note: PostgreSQL uses host port 5433 instead of 5432 to avoid conflicts with host PostgreSQL instances.

### 3. Verify Health

```bash
curl http://localhost:8000/health
# {"status":"healthy","db":"ok","kafka":"ok"}
```

Wait for all containers to show `healthy`:

```bash
docker compose ps
```

### 4. Smoke Test

```bash
# Web form submission
curl -s -X POST http://localhost:8000/webhooks/webform \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","email":"test@example.com","subject":"Help","message":"Need assistance","category":"support","priority":"medium"}'

# List tickets (authenticated)
curl -s http://localhost:8000/tickets \
  -H "X-API-Key: test-key-12345"

# Customer history
curl -s http://localhost:8000/customers/test@example.com/history \
  -H "X-API-Key: test-key-12345"

# Metrics
curl -s http://localhost:8000/metrics
```

### 5. View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api
docker compose logs -f worker
```

### 6. Stop

```bash
docker compose down -v
```

The `-v` flag removes volumes (PostgreSQL data, Kafka logs). Omit it to persist data between restarts.

## Kubernetes Deployment

### Apply Order

```bash
# 1. Namespace
kubectl apply -f k8s/namespace.yaml

# 2. Config & Secrets (no runtime dependencies)
kubectl apply -f k8s/secrets.yaml      # Edit base64 values first!
kubectl apply -f k8s/configmap.yaml

# 3. Data layer
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/kafka.yaml

# 4. Database schema initialization (runs after Postgres is ready)
kubectl apply -f k8s/job-db-init.yaml

# 5. Application deployments (depend on DB + Kafka)
kubectl apply -f k8s/deployment-api.yaml
kubectl apply -f k8s/deployment-worker.yaml

# 6. Autoscaling
kubectl apply -f k8s/hpa-api.yaml

# 7. Networking
kubectl apply -f k8s/service-api.yaml
kubectl apply -f k8s/ingress.yaml
```

### Secrets (k8s/secrets.yaml)

Before applying, encode real values in base64:

```bash
echo -n "sk-proj-..." | base64   # OPENAI_API_KEY
echo -n "test-key-12345" | base64 # APP_API_KEY
```

Replace placeholder values in `k8s/secrets.yaml` with the encoded output. Secrets that must be replaced:
- `database-url`
- `kafka-bootstrap-servers`
- `openai-api-key`
- `app-api-key`
- `twilio-account-sid` (optional)
- `twilio-auth-token` (optional)
- `twilio-whatsapp-number` (optional)

### Ingress Configuration

`k8s/ingress.yaml` contains placeholders for domain name and TLS issuer. Edit before applying:

```yaml
spec:
  tls:
    - hosts: [your-domain.com]
      secretName: techflow-tls
  rules:
    - host: your-domain.com
```

Update the cert-manager annotation issuer name if not using Let's Encrypt staging.

## Scaling

### Horizontal Pod Autoscaler (HPA)

The API deployment (`k8s/hpa-api.yaml`) auto-scales between 1-10 replicas based on CPU utilization (target: 70%).

```bash
# View HPA status
kubectl get hpa -n techflow

# Manual scale (override HPA temporarily)
kubectl scale deployment techflow-api -n techflow --replicas=5
```

### Worker Instances

The worker is a single-replica deployment. To increase throughput, increase the `concurrent_messages` setting in the worker config or add a `KAFKA_CONCURRENCY` environment variable. Multiple worker replicas require Kafka consumer group coordination (`group.id` must match across replicas — handled automatically via `aiokafka` consumer groups).

## Verifying Deployment

### Health Check Endpoint

```bash
kubectl port-forward -n techflow service/techflow-api 8000:8000 &
curl http://localhost:8000/health
```

### Prometheus Metrics

```bash
kubectl port-forward -n techflow service/techflow-api 8000:8000 &
curl http://localhost:8000/metrics
```

### Grafana Dashboard

```bash
kubectl port-forward -n techflow service/techflow-grafana 3001:3001 &
# Open http://localhost:3001 in browser (admin / password from secrets)
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| API healthcheck fails (exit code 52) | curl not in container | Rebuild with `docker compose build --no-cache api` |
| PostgreSQL refuses connection | Port 5432 conflict on host | Change `ports: "5433:5432"` in docker-compose.yml |
| Kafka container restarting | `KAFKA_ADVERTISED_LISTENERS` wrong | Check kafka healthcheck: `kafka-broker-api-versions` (no `.sh`) |
| Worker crashes on startup | Gmail credentials missing/invalid | Set `GMAIL_CREDENTIALS_FILE` to empty and restart |
| 500 instead of 401 | API key middleware HTTPException | Verify `verify_api_key` uses `request.headers` not `Header()` |
| Agent returns no response | OpenAI API key missing/misconfigured | Check `OPENAI_API_KEY` in `.env` |
| "Extension vector not found" | Extension name mismatch | Use `CREATE EXTENSION vector` (not `pgvector`) |
| Grafana blank dashboard | No metrics data | Submit a ticket via webform to generate metrics |
