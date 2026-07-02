# Chaos Testing — TechFlow CRM Digital FTE

## Overview

This document describes chaos engineering experiments to validate system resilience. Each scenario includes setup, execution, and expected recovery behavior.

## Prerequisites

- Docker Compose stack fully healthy (`docker compose ps` — all 7 services green)
- Prometheus scraping at `http://localhost:9090`
- Grafana at `http://localhost:3001`
- A recent ticket submission to seed metrics data

## Experiment 1: API Pod Deletion (Kubernetes)

### Scenario
Delete the API pod and verify the HPA restarts it within the expected window.

### Steps
```bash
# Start continuous health check
while true; do
  curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health
  echo ""
  sleep 1
done

# In another terminal, delete the pod (k8s deployment)
kubectl delete pod -n techflow -l app=techflow-api

# Observe:
# - Health check returns 000 (connection refused) for ~5-10s
# - New pod starts within ReplicaSet reconciliation
# - Health check returns 200 again
```

### Expected Result
- Downtime: 5-15 seconds (pod restart + init)
- No data loss (API is stateless, persistent data in PostgreSQL)
- KEDA/HPA may trigger on missing metrics but stabilizes

### Grafana Observability
- Check `container_cpu_usage_seconds_total` spike on restart
- Check `container_restarts_total` increments
- Prometheus `up` metric briefly drops to 0 then returns to 1

## Experiment 2: Kafka Broker Restart

### Scenario
Restart Kafka while messages are being produced and consumed.

### Steps
```bash
# Terminal 1: continuous webform submissions
while true; do
  curl -s -X POST http://localhost:8000/webhooks/webform \
    -H "Content-Type: application/json" \
    -d '{"name":"Stress","email":"stress@test.com","subject":"Chaos","message":"Kafka restart test","category":"support","priority":"low"}'
  sleep 2
done

# Terminal 2: restart Kafka
docker compose restart kafka

# Terminal 3: watch worker logs
docker compose logs -f worker
```

### Expected Result
- Worker's `aiokafka` consumer detects broker disconnect
- Consumer group rebalances automatically
- During rebalance: some submissions show 201 but tickets may not be processed immediately
- After Kafka reconnects: backlog is consumed
- No messages lost (persisted to Kafka log, auto-created topics)

### Recovery Time
- Kafka restart: ~10-15s
- Consumer rebalance: ~5-10s
- Total window without processing: ~20-30s

## Experiment 3: PostgreSQL Outage

### Scenario
Kill the Postgres container and observe API degradation, then restore.

### Steps
```bash
# Terminal 1: watch health endpoint
while true; do curl -s http://localhost:8000/health | jq .; sleep 1; done

# Terminal 2: kill postgres
docker compose stop postgres

# Observe:
# - /health immediately returns {"status":"degraded","db":"error","kafka":"ok"}
# - All endpoints that query DB return 500
# - Webform submissions produce 500 (no customer lookup possible)
# - Worker logs show "Can't connect to PostgreSQL"

# Restore
docker compose start postgres

# Observe:
# - /health returns {"status":"healthy","db":"ok","kafka":"ok"} once Postgres is ready
# - All endpoints work again
```

### Expected Result
- Degraded mode (API stays up, health check reports DB error)
- Webform submissions fail during outage (no way to create customer/ticket)
- Full recovery after Postgres reconnects (no data loss — Postgres crash recovery)
- Worker resumes processing queued Kafka messages

## Experiment 4: Worker Process Kill

### Scenario
Kill the worker container and verify it restarts and resumes processing.

### Steps
```bash
# Terminal 1: continuous submissions
for i in $(seq 1 10); do
  curl -s -X POST http://localhost:8000/webhooks/webform \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"Test$i\",\"email\":\"test$i@test.com\",\"subject\":\"Batch $i\",\"message\":\"Worker chaos test\",\"category\":\"support\",\"priority\":\"low\"}"
  echo ""
  sleep 1
done

# Terminal 2: kill worker
docker compose kill worker

# Terminal 3: check tickets before and after restart
sleep 5
curl -s http://localhost:8000/tickets -H "X-API-Key: test-key-12345" | jq '.tickets | length'

# Worker restarts via docker compose restart policy
docker compose start worker
sleep 10

# Check tickets again — new ones should be processed
curl -s http://localhost:8000/tickets -H "X-API-Key: test-key-12345" | jq '.tickets | length'
```

### Expected Result
- Submissions accepted during worker downtime (API produces to Kafka)
- Tickets remain in `pending` status until worker recovers
- After worker restarts, it picks up queued messages from Kafka offset
- All submissions eventually processed (Kafka guarantees at-least-once delivery with consumer offsets)

## Experiment 5: Network Partition (Container Isolation)

### Scenario
Isolate the worker from Kafka to simulate network failure.

### Steps
```bash
# Disconnect worker from the techflow network
docker network disconnect techflow_default techflow-worker

# Submit webform requests — they should fail at processing
curl -X POST http://localhost:8000/webhooks/webform \
  -H "Content-Type: application/json" \
  -d '{"name":"NetTest","email":"net@test.com","subject":"Network","message":"Isolation test","category":"support","priority":"low"}'

# Worker logs show Kafka connection errors
docker compose logs worker --tail 20

# Restore connectivity
docker network connect techflow_default techflow-worker

# Worker reconnects, consumer rebalances, processes queued messages
```

### Expected Result
- API accepts submissions (Kafka production succeeds from API side)
- Worker cannot consume from Kafka during isolation
- After reconnection: worker resumes from last committed offset
- No message loss (Kafka retains messages per `log.retention.hours`)

## Experiment 6: Metrics Pipeline Failure

### Scenario
Stop Prometheus and verify the system continues operating without monitoring.

### Steps
```bash
docker compose stop prometheus

# Verify API/worker continue functioning
curl -s http://localhost:8000/health

# Submit a ticket
curl -s -X POST http://localhost:8000/webhooks/webform \
  -H "Content-Type: application/json" \
  -d '{"name":"MonTest","email":"mon@test.com","subject":"Monitoring","message":"No prom test","category":"support","priority":"low"}'

# Check API metrics endpoint still works
curl -s http://localhost:8000/metrics | head -20

# Restore
docker compose start prometheus

# Verify Grafana picks up again
curl -s http://localhost:3001/api/health
```

### Expected Result
- System operates normally without Prometheus
- Internal metrics events continue to be published to Kafka `metrics.events` topic
- Metrics endpoint (`/metrics`) still serves Prometheus-format metrics
- Predictable queries in Grafana may show gaps during outage
- No cascading failures

## Summary Matrix

| Experiment | Component | Failure Mode | Recovery | Data Loss | Max Downtime |
|-----------|-----------|-------------|----------|-----------|-------------|
| 1 | API Pod | Kill process | HPA/ReplicaSet | None (stateless) | 15s |
| 2 | Kafka Broker | Restart | Consumer rebalance | None (disk persistence) | 30s |
| 3 | PostgreSQL | Stop container | Crash recovery | None (WAL + restart) | Until restore |
| 4 | Worker | Kill process | Restart policy | None (Kafka offset) | Until restart |
| 5 | Network | Disconnect | Reconnect | None (Kafka retention) | Until reconnect |
| 6 | Prometheus | Stop container | Restart | Metrics gap | Until restore |

## Recovery Score

| System | Self-Healing | Notes |
|--------|-------------|-------|
| API | ✅ | k8s: ReplicaSet; Docker Compose depends_on ensures restart |
| Worker | ✅ | Resumes from Kafka offset after container restart |
| Postgres | ✅ | WAL crash recovery on restart |
| Kafka | ⚠️ | Requires docker restart behavior; no k8s StatefulSet auto-repair |
| Prometheus | ✅ | Stateless; restart recovers |
| Grafana | ✅ | Stateless; data sources from Prometheus |
| DLQ | ✅ | Dead letters automatically retried via dlq.retry topic |
