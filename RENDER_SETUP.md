# Render Pe Deploy (Free) — Step-by-Step

Ye guide aapke project (TechFlow CRM) ko **Render free tier** pe deploy karta hai.
Maine code me ye changes kar diye hain taake demo **Kafka ke bina bhi** chale:

1. `ENABLE_KAFKA=false` pe API bina broker ke start hoti hai (pehle crash karti thi).
2. Kafka off hone pe **webform AI agent ko synchronously chala ke reply store** karta hai.
3. Worker bina Kafka ke **dashboard metrics** collect karta hai.
4. `Dockerfile` ab `$PORT` env var pe listen karta hai (Render ki requirement).

## Architecture (free me)

| Service | Render Type | Kya karta hai |
|---|---|---|
| `techflow-db` | Postgres (free) | Data + pgvector |
| `techflow-api` | Web Service (Docker, free) | FastAPI, saare endpoints |
| `techflow-worker` | Worker (Docker, free) | Metrics collector |
| `techflow-webform` | Web Service (Docker, free) | Next.js support form |

## Steps

### 1. GitHub pe push karo
Repo already git hai. Push karo:

```bash
git add -A
git commit -m "Render deployment prep: optional Kafka, render.yaml, migrations"
git push origin main
```

### 2. Render Blueprint se deploy karo
1. [dashboard.render.com](https://dashboard.render.com) pe login karo.
2. **New → Blueprint** chuno.
3. Apna repo select karo (Render se GitHub connect karna hoga).
4. Render `render.yaml` ko read karke 4 cheezein banayega. **Deploy** dabao.

### 3. Secrets set karo
Blueprint ke baad Render dashboard me 2 cheezein manually fill karni hain
(`sync: false` wale):

- **techflow-api** → Environment → `OPENROUTER_API_KEY`:
  - Free key [openrouter.ai/keys](https://openrouter.ai/keys) se banao
  - Model free chahiye to `OPENAI_MODEL` = `meta-llama/llama-3.1-8b-instruct` kar do (gpt-4o charge karta hai)
- **techflow-worker** → same `OPENROUTER_API_KEY`

`API_KEY` (X-API-Key header) auto-generate ho jayega — use karna hoga `/tickets` jaise protected endpoints pe.

### 4. Verify karo

- API health: `https://techflow-api.onrender.com/health` → `{"status":"healthy","db":"ok","kafka":"ok"}`
- Webform submit karo: `https://techflow-webform.onrender.com/` pe form bharo
  - Ticket bane gi + **AI reply** usi response me aayega (Kafka ke bina)
- Tickets: `curl -H "X-API-Key: <API_KEY>" https://techflow-api.onrender.com/tickets`
- Dashboard metrics: `curl -H "X-API-Key: <API_KEY>" https://techflow-api.onrender.com/metrics/dashboard`

## Free tier ke limits (jaan lo)

- Free web services **15 min idle pe sleep** ho jate hain — pehli request ke baad ~1 min lagta hai wake hone me.
- Free Postgres **30 din baad expire** hota hai (hackathon ke liye kaafi hai).
- **750 free instance-hours/month**: 3 services chalein to ~10 din tak free. Demo ke baad services **pause** kar dena (dashboard me pause button).
- Worker me `ENABLE_KAFKA=false` hai — wo sirf metrics collect karega. Full Kafka pipeline chahiye to neeche dekho.

## Full pipeline (optional — free Kafka ke saath)

Agar agent.whatsapp / agent.email channels bhi chahiye (worker ke through):

1. **Aiven free Kafka** banao: [aiven.io](https://aiven.io) → free plan (5 topics, 2 partitions each).
   - Project me Kafka service create karo, `SASL_SSL` credentials copy karo.
2. API + worker dono pe env vars:
   - `ENABLE_KAFKA=true`
   - `KAFKA_BOOTSTRAP_SERVERS=<aiven-host>:<port>`
   - `KAFKA_SECURITY_PROTOCOL=SASL_SSL`
   - `KAFKA_SASL_MECHANISM=PLAIN`
   - `KAFKA_SASL_USERNAME` / `KAFKA_SASL_PASSWORD`
3. Topics create karo (Aiven console): `inbound.webform`, `inbound.whatsapp`, `inbound.email`, `agent.processing`, `agent.completed`, `escalations`, `notifications.outbound`, `metrics.events`, `dlq` (total 9 — free limit 5 hai, is liye kam topics chahiye to `web_form_handler`/agent me kuch topics merge karne padenge).

> Note: free Kafka limit (5 topics) project ke 9-10 topics se kam hai. Full pipeline ke liye ya to Aiven ka paid Dev tier, ya topics reduce karne padenge. Demo ke liye `ENABLE_KAFKA=false` wala path sufficient hai.

## Troubleshooting

| Problem | Fix |
|---|---|
| API crash-loop (status: crash) | Logs dekho. Sabse common: `OPENROUTER_API_KEY` nahi set / `DATABASE_URL` galat. |
| `psql: could not connect` in pre-deploy | Database deploy hone ka wait karo, phir API ko `Deploy` se retry karo. |
| Migration fail | Logs me exact SQL error dekho. `_migrations_applied` table idempotent hai — dobara deploy safe hai. |
| Webform pe form submit → 404 | `NEXT_PUBLIC_API_URL` check karo (techflow-webform env me). API ka URL (`https://techflow-api.onrender.com`) hona chahiye. |
| Health me `db:error` | `DATABASE_SSL=require` hona chahiye (blueprint me set hai). |