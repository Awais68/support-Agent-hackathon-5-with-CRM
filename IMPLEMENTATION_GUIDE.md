# TechFlow CRM Digital FTE - Implementation Guide

## 🎯 Overview

This guide walks through implementing the enhanced TechFlow system with:
- ✅ Working agent tools via MCP
- ✅ Real knowledge base search with embeddings
- ✅ Prometheus metrics & Grafana dashboard
- ✅ Playwright E2E tests
- ✅ WebSocket support for real-time updates

---

## 📦 Phase 1: Core Dependencies (Day 1)

### Add to `requirements.txt`

```bash
# Existing dependencies (keep these)
fastapi==0.104.1
uvicorn==0.24.0
asyncpg==0.29.0
pydantic==2.5.0
structlog==23.3.0
openai==1.3.9
aiokafka==0.10.0

# NEW: For embeddings
openai>=1.0.0  # Already there, ensure recent version

# NEW: For MCP
mcp>=0.1.0

# NEW: For metrics
prometheus-client>=0.19.0

# NEW: For WebSocket
websockets>=12.0

# NEW: For testing
pytest>=7.4.0
pytest-asyncio>=0.23.0
playwright>=1.40.0
httpx>=0.25.0

# NEW: For better error handling
python-json-logger>=2.0.0
tenacity>=8.2.3  # Retry library

# NEW: For async utilities
aiofiles>=23.2.0
```

### Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium  # For E2E tests
```

---

## 🔧 Phase 2: Integrate Tool Executor (Day 1-2)

### Step 1: Update Agent to Use Tool Executor

**File**: `agent/customer_success_agent.py`

Replace the tool invocation section (lines 136-143) with:

```python
# Add this import at the top
from agent.tools_executor import ToolExecutor

# In the process_customer_message method, after getting OpenAI response:
if response.choices[0].message.tool_calls:
    tools_executor = ToolExecutor(
        db_pool=self.context.db_pool,
        kafka_producer=self.context.kafka_producer,
        openai_client=self.context.openai_client,
    )

    for tool_call in response.choices[0].message.tool_calls:
        tool_name = tool_call.function.name
        tool_input = json.loads(tool_call.function.arguments)

        # Execute tool
        tool_result = await tools_executor.execute_tool(tool_name, tool_input)

        self.context.logger.info(
            "Tool executed",
            tool_name=tool_name,
            status=tool_result.get("status"),
        )
```

### Step 2: Integrate MCP Server

**File**: `api/main.py`

Add at startup (in lifespan):

```python
from mcp_server import mcp_server, initialize_default_tools
from agent.tools_executor import ToolExecutor

# In lifespan startup:
tools_executor = ToolExecutor(
    app.state.db_pool,
    app.state.kafka_producer,
    app.state.openai_client,
)

await initialize_default_tools(tools_executor)
app.state.mcp_server = mcp_server

# Add MCP tools info endpoint
@app.get("/admin/tools")
async def list_available_tools():
    """List available MCP tools."""
    return mcp_server.get_tool_specs()
```

---

## 📚 Phase 3: Real Knowledge Base with Embeddings (Day 2)

### Step 1: Update Database Queries

**File**: `database/queries.py`

Add this function:

```python
async def search_knowledge_base(
    pool: asyncpg.Pool,
    query_embedding: list,
    category: Optional[str] = None,
    limit: int = 5,
) -> List[dict]:
    """Search KB using pgvector similarity."""
    query = """
    SELECT id, title, content, category, tags,
           1 - (embedding <=> $1::vector) as similarity
    FROM knowledge_base
    """
    params = [query_embedding]

    if category:
        query += " WHERE category = $2"
        params.append(category)

    query += " ORDER BY similarity DESC LIMIT ${}".format(len(params) + 1)
    params.append(limit)

    async with pool.acquire() as conn:
        return await conn.fetch(query, *params)
```

### Step 2: Integrate Embeddings Service

**File**: `api/main.py`

Update knowledge base endpoints:

```python
from embeddings_service import EmbeddingsService

# In lifespan:
app.state.embeddings_service = EmbeddingsService(app.state.openai_client)

# Update search endpoint
@app.get("/knowledge-base")
async def search_knowledge_base(
    q: str,
    category: Optional[str] = None,
    limit: int = 5,
    pool: asyncpg.Pool = Depends(get_db),
    embeddings: EmbeddingsService = Depends(lambda: app.state.embeddings_service),
):
    """Search knowledge base with real embeddings."""
    try:
        # Generate embedding for query
        query_embedding = await embeddings.embed_text(q)

        # Search with pgvector
        results = await db.search_knowledge_base(
            pool,
            query_embedding=query_embedding,
            category=category,
            limit=limit,
        )

        return {
            "query": q,
            "results": [dict(r) for r in results],
            "count": len(results),
        }
    except Exception as e:
        logger.error("KB search failed", error=str(e))
        raise HTTPException(status_code=500, detail="Search failed")
```

### Step 3: Ingest Articles with Embeddings

```python
@app.post("/knowledge-base/ingest", status_code=201)
async def ingest_knowledge_base(
    request: KBArticleRequest,
    pool: asyncpg.Pool = Depends(get_db),
    embeddings: EmbeddingsService = Depends(lambda: app.state.embeddings_service),
):
    """Ingest KB article with embeddings."""
    try:
        # Generate embedding
        title_emb, content_emb, combined_emb = await embeddings.embed_knowledge_base_article(
            title=request.title,
            content=request.content,
        )

        # Store with embedding
        article = await db.add_knowledge_base_article(
            pool,
            title=request.title,
            content=request.content,
            embedding=combined_emb,  # Use combined embedding
            category=request.category,
            tags=request.tags,
        )

        return {
            "id": str(article["id"]),
            "title": article["title"],
            "embedded": True,
            "similarity_ready": True,
        }
    except Exception as e:
        logger.error("Ingest failed", error=str(e))
        raise HTTPException(status_code=400, detail="Ingest failed")
```

---

## 📊 Phase 4: Observability with Prometheus & Grafana (Day 2-3)

### Step 1: Add Metrics Collection

**File**: `api/main.py`

```python
from metrics import (
    message_processing_time,
    agent_response_time,
    tickets_created,
    errors_total,
    api_request_duration,
)
from prometheus_client import make_asgi_app, REGISTRY

# Add Prometheus endpoint
metrics_app = make_asgi_app(registry=REGISTRY)
app.mount("/metrics", metrics_app)

# Wrap endpoints with metrics
@app.post("/tickets")
async def create_ticket(...):
    """Create ticket with metrics."""
    with MetricsContext(message_processing_time) as ctx:
        try:
            # ... create ticket logic ...
            tickets_created.labels(channel="api", priority=request.priority).inc()
            return result
        except Exception as e:
            errors_total.labels(error_type="ticket_creation", component="api").inc()
            raise
```

### Step 2: Update docker-compose.yml

Add Prometheus and Grafana services:

```yaml
prometheus:
  image: prom/prometheus:latest
  ports:
    - "9090:9090"
  volumes:
    - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
    - prometheus_data:/prometheus
  command:
    - "--config.file=/etc/prometheus/prometheus.yml"

grafana:
  image: grafana/grafana:latest
  ports:
    - "3000:3000"
  environment:
    - GF_SECURITY_ADMIN_PASSWORD=admin
  volumes:
    - grafana_data:/var/lib/grafana
    - ./monitoring/grafana_dashboard.json:/etc/grafana/provisioning/dashboards/dashboard.json

volumes:
  prometheus_data:
  grafana_data:
```

### Step 3: Create Prometheus Config

**File**: `monitoring/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'techflow-api'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
```

### Step 4: Create Grafana Dashboard

**File**: `monitoring/grafana_dashboard.json`

```json
{
  "dashboard": {
    "title": "TechFlow CRM Digital FTE",
    "panels": [
      {
        "title": "Messages Processed Per Minute",
        "targets": [
          {"expr": "rate(messages_processed_total[1m])"}
        ]
      },
      {
        "title": "Agent Response Time (p95)",
        "targets": [
          {"expr": "histogram_quantile(0.95, agent_response_seconds)"}
        ]
      },
      {
        "title": "Ticket Creation Rate",
        "targets": [
          {"expr": "rate(tickets_created_total[5m])"}
        ]
      },
      {
        "title": "Error Rate",
        "targets": [
          {"expr": "rate(errors_total[5m])"}
        ]
      }
    ]
  }
}
```

---

## 🧪 Phase 5: E2E Testing with Playwright (Day 3)

### Step 1: Install Playwright

```bash
pip install pytest-playwright
playwright install chromium
```

### Step 2: Run Existing Tests

```bash
# Start API server
python -m specifyplus.main &

# Run E2E tests
pytest specifyplus/tests/test_e2e_playwright.py -v -s
```

### Step 3: Test Web Form Flow

```bash
# Test specific flow
pytest specifyplus/tests/test_e2e_playwright.py::TestWebFormFlow::test_web_form_submission_creates_ticket -v
```

---

## 🔌 Phase 6: WebSocket Support (Day 4)

### Step 1: Create WebSocket Manager

**File**: `websocket_manager.py`

```python
from typing import Set
from fastapi import WebSocket, WebSocketDisconnect
import json

class WebSocketManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    async def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass
```

### Step 2: Add WebSocket Endpoint

**File**: `api/main.py`

```python
from websocket_manager import WebSocketManager

manager = WebSocketManager()

@app.websocket("/ws/tickets/{ticket_id}")
async def websocket_endpoint(websocket: WebSocket, ticket_id: str):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle incoming messages
    except WebSocketDisconnect:
        manager.disconnect(websocket)
```

---

## ✅ Verification Checklist

After implementation, verify:

- [ ] Agent tools execute (no more mock results)
- [ ] KB search returns real results
- [ ] Prometheus collecting metrics (`http://localhost:9090`)
- [ ] Grafana dashboard visible (`http://localhost:3000`)
- [ ] E2E tests pass
- [ ] WebSocket connections work
- [ ] All 5 agent tools registered in MCP server
- [ ] Zero errors in tool execution
- [ ] Load test sustains 50+ concurrent requests

---

## 🚀 Deployment

### Local Testing

```bash
# 1. Start services
docker-compose up --build

# 2. Run migrations
docker-compose exec api python -m database.init

# 3. Ingest sample KB articles
curl -X POST http://localhost:8000/knowledge-base/ingest \
  -H "X-API-Key: test-key-12345" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Getting Started",
    "content": "Welcome to TechFlow...",
    "category": "onboarding"
  }'

# 4. Run tests
pytest specifyplus/tests/ -v

# 5. Monitor
# Prometheus: http://localhost:9090
# Grafana: http://localhost:3000
# API Metrics: http://localhost:8000/metrics
```

### Production Deployment

1. Build Docker image: `docker build -t techflow:latest .`
2. Deploy to Kubernetes: `kubectl apply -f k8s/`
3. Configure autoscaling with HPA
4. Set up external Prometheus (e.g., AWS CloudWatch)
5. Configure alerting for critical metrics

---

## 🐛 Troubleshooting

**Tools not executing?**
- Check MCP server initialization in startup
- Verify ToolExecutor is properly instantiated
- Look for errors in structlog output

**Knowledge base searches return empty?**
- Ensure pgvector extension is installed in PostgreSQL
- Verify embeddings are being generated
- Check similarity threshold isn't too high

**Metrics not appearing in Prometheus?**
- Verify `/metrics` endpoint is accessible
- Check Prometheus scrape config
- Look for metric export errors in logs

**WebSocket tests failing?**
- Ensure WebSocket server is running
- Check browser console for connection errors
- Verify firewall allows WebSocket connections

---

## 📚 Additional Resources

- [OpenAI API Docs](https://platform.openai.com/docs)
- [FastAPI WebSocket Guide](https://fastapi.tiangolo.com/advanced/websockets/)
- [pgvector Documentation](https://github.com/pgvector/pgvector)
- [Prometheus Metrics Best Practices](https://prometheus.io/docs/practices/naming/)
- [Playwright Testing Guide](https://playwright.dev/python/)

---

**Last Updated**: 2026-03-12
**Status**: Ready for Implementation
