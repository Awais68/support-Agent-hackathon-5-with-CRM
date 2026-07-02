# TechFlow CRM Digital FTE - Quick Start Guide

## ⚡ 5-Minute Startup

### 1. Prerequisites

```bash
# Clone/navigate to project
cd specifyplus

# Verify Python 3.10+
python --version

# Verify Docker is running
docker ps
```

### 2. Start the System

```bash
# Build and start all services
docker-compose up --build

# In another terminal, test health check
curl http://localhost:8000/health
# Expected: {"status":"healthy","db":"ok","kafka":"ok"}
```

### 3. Test Web Form Submission

```bash
curl -X POST http://localhost:8000/webhooks/webform \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Jane Smith",
    "email": "jane@example.com",
    "subject": "How do I get started?",
    "message": "I need help setting up my first connector",
    "category": "onboarding",
    "priority": "medium"
  }'

# Expected: Ticket number like TKT-20260312-0001
```

---

## 🔑 Key API Endpoints

### Create a Ticket
```bash
curl -X POST http://localhost:8000/tickets \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-key-12345" \
  -d '{
    "name": "John Doe",
    "email": "john@example.com",
    "subject": "API Integration Issue",
    "category": "technical",
    "priority": "high",
    "message": "Getting 401 errors when calling the API"
  }'
```

### Get Ticket Details
```bash
curl http://localhost:8000/tickets/{ticket_id} \
  -H "X-API-Key: test-key-12345"
```

### Update Ticket Status
```bash
curl -X PATCH http://localhost:8000/tickets/{ticket_id}/status \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-key-12345" \
  -d '{"status": "in_progress"}'

# Valid statuses: open, in_progress, resolved, escalated, closed
```

### Search Knowledge Base
```bash
curl "http://localhost:8000/knowledge-base?q=how+to+setup+connectors&limit=5" \
  -H "X-API-Key: test-key-12345"
```

### Get Customer History
```bash
curl "http://localhost:8000/customers/jane@example.com/history" \
  -H "X-API-Key: test-key-12345"
```

### View Dashboard Metrics
```bash
curl http://localhost:8000/metrics/dashboard \
  -H "X-API-Key: test-key-12345"

# Returns: {
#   "status_counts": {"open": 5, "in_progress": 2, ...},
#   "avg_resolution_hours": 2.5,
#   "escalation_rate_percent": 8.5,
#   "channel_counts": {"email": 10, "whatsapp": 5, ...},
#   "total_tickets": 17
# }
```

---

## 📊 Monitoring Dashboard

### Access Dashboards

Open your browser to:
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (login: admin/admin)
- **API Metrics**: http://localhost:8000/metrics

### Key Metrics to Watch

1. **Response Time**: `agent_response_seconds` (p95 < 2s)
2. **Error Rate**: `errors_total` (< 1%)
3. **Throughput**: `messages_processed_total` (rate/minute)
4. **Escalation Rate**: `escalation_rate_percent` (target: < 10%)

---

## 🧪 Run Tests

### Unit Tests
```bash
# Test database layer
pytest tests/test_agent.py -v

# Test channel handlers
pytest tests/test_channels.py -v

# Test integration
pytest tests/test_e2e.py -v
```

### E2E Tests (Playwright)
```bash
# Make sure API is running on localhost:8000

# Run all E2E tests
pytest tests/test_e2e_playwright.py -v -s

# Run specific test
pytest tests/test_e2e_playwright.py::TestWebFormFlow::test_ticket_lifecycle -v

# Run with UI (watch test execution)
pytest tests/test_e2e_playwright.py --headed
```

### Load Test
```bash
# Simulate 50 concurrent users for 1 minute
locust -f tests/load_test.py --headless -u 50 -r 5 --run-time 60s
```

---

## 🤖 Agent Tool Execution

The agent now has **5 working tools** that it can call:

### 1. Search Knowledge Base
When customer asks "How do I set up a connector?"
```
Agent Decision: search_knowledge_base(
  query="setup connector",
  category="onboarding"
)
Result: Returns relevant KB articles
```

### 2. Create Ticket
When complex issue is identified
```
Agent Decision: create_ticket(
  customer_email="jane@example.com",
  subject="Complex Integration Issue",
  priority="high",
  category="technical"
)
Result: Ticket TKT-20260312-0042 created
```

### 3. Get Customer History
To provide personalized support
```
Agent Decision: get_customer_history(
  customer_email="jane@example.com",
  limit=10
)
Result: Shows past 10 tickets and resolutions
```

### 4. Escalate to Human
When issue requires human judgment
```
Agent Decision: escalate_to_human(
  ticket_id="uuid",
  reason="Requires billing department review",
  priority_level="high"
)
Result: Routed to human queue
```

### 5. Send Response
To provide immediate feedback
```
Agent Decision: send_response(
  ticket_id="uuid",
  message="Thank you for contacting us...",
  channel="email"
)
Result: Email sent to customer
```

---

## 🔄 Multi-Channel Support

### Email
```bash
# Email handler automatically polls Gmail
# Messages flow: Gmail → Kafka → Agent → Response
```

### WhatsApp
```bash
# Via Twilio integration
curl -X POST http://localhost:8000/webhooks/whatsapp \
  -d 'From=+1234567890&Body=Help+please&MessageSid=SM...'
```

### Web Form
```bash
# Direct submission endpoint
curl -X POST http://localhost:8000/webhooks/webform \
  -d '{...}'
```

---

## 📈 Workflow Example: Full Ticket Lifecycle

### Step 1: Customer Submits Web Form
```json
{
  "name": "Alex Johnson",
  "email": "alex@startup.com",
  "subject": "Can't connect my Salesforce data",
  "message": "Getting authentication errors",
  "category": "technical",
  "priority": "high"
}
```

### Step 2: System Creates Ticket
- Ticket ID: `uuid-123`
- Ticket Number: `TKT-20260312-0001`
- Status: `open`

### Step 3: Agent Processing
1. **Classify** message as "technical"
2. **Search KB** for "Salesforce authentication"
3. **Get customer history** - new customer
4. **Generate response** with helpful KB articles
5. **Send response** via web form

### Step 4: If KB Doesn't Help
1. **Create secondary ticket** for Salesforce team
2. **Escalate to human** agent with reason
3. **Send notification** to human team

### Step 5: Human Agent Takes Over
- Access full customer context
- View KB search results already provided
- Add personal touch and resolve
- Close ticket when resolved

### Step 6: Monitor Metrics
- Resolution time: 45 minutes
- Channel: web form
- Escalation: yes
- Agent satisfaction: captured in survey

---

## 🛠️ Common Operations

### Add a Knowledge Base Article

```bash
curl -X POST http://localhost:8000/knowledge-base/ingest \
  -H "X-API-Key: test-key-12345" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Salesforce OAuth Setup Guide",
    "content": "To set up OAuth authentication with Salesforce...",
    "category": "technical",
    "tags": ["salesforce", "oauth", "authentication"]
  }'
```

### Batch Import KB Articles

```bash
for article in articles/*.json; do
  curl -X POST http://localhost:8000/knowledge-base/ingest \
    -H "X-API-Key: test-key-12345" \
    -H "Content-Type: application/json" \
    -d @"$article"
done
```

### Export Customer Data

```bash
curl "http://localhost:8000/customers/alex@startup.com/history?limit=100" \
  -H "X-API-Key: test-key-12345" | jq . > customer_export.json
```

### Check System Health

```bash
# Full health check
curl http://localhost:8000/health | jq .

# View available tools
curl http://localhost:8000/admin/tools \
  -H "X-API-Key: test-key-12345" | jq .
```

---

## 🚨 Troubleshooting

### Tickets not being created?
```bash
# Check database connection
curl http://localhost:8000/health

# Check logs
docker-compose logs api | tail -50

# Verify DB is running
docker-compose ps postgres
```

### Agent tools not executing?
```bash
# Check MCP server
curl http://localhost:8000/admin/tools

# Review logs for tool execution
docker-compose logs api | grep "Tool executed"

# Test tool directly with curl
curl -X POST http://localhost:8000/agent/test-tool \
  -H "X-API-Key: test-key-12345"
```

### Knowledge base search returns empty?
```bash
# Ingest sample articles first
curl -X POST http://localhost:8000/knowledge-base/ingest \
  -H "X-API-Key: test-key-12345" \
  -d @sample_article.json

# Check if articles exist
curl "http://localhost:8000/knowledge-base?q=test&limit=20" \
  -H "X-API-Key: test-key-12345"
```

### Prometheus not collecting metrics?
```bash
# Check if API is exporting metrics
curl http://localhost:8000/metrics | head -20

# Verify Prometheus can scrape
curl http://localhost:9090/api/v1/query?query=up
```

---

## 📚 Learn More

- **API Specification**: See `README.md` for full API documentation
- **Implementation Details**: Read `IMPLEMENTATION_GUIDE.md`
- **Architecture**: Check `PROJECT_ANALYSIS.md`
- **Agent Prompts**: Explore `agent/prompts.py`
- **Database Schema**: View `database/queries.py`

---

## 🔐 Security Notes

- API Key required for all endpoints except `/health` and webhooks
- Default key: `test-key-12345` (change in production!)
- Set `API_KEY` environment variable
- Webhook signatures validated for Twilio
- CORS configured for trusted origins
- All secrets should be in `.env` (not git)

---

## 📞 Support

If you encounter issues:

1. Check logs: `docker-compose logs -f api`
2. Review this guide's troubleshooting section
3. Check `PROJECT_ANALYSIS.md` for known issues
4. Run tests to isolate problem
5. Review implementation code in `specifyplus/`

---

**Version**: 1.0.0
**Last Updated**: 2026-03-12
**Status**: ✅ Production Ready
