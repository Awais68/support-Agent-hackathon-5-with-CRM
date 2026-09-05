# Gemini API Key — Test Report & Fixes

Date: 2026-09-05 · Key: `GEMINI_API_KEY` in `.env` (53 chars, `AQ.` prefix)

## Outcome

The key is valid. It was also dead config — nothing in the codebase read it.
Embeddings now run on Gemini; chat stays on OpenRouter. Eight bugs found, all
fixed and verified end-to-end against a real Postgres + pgvector database.

## How to test

```bash
.venv/bin/python scripts/test_gemini.py   # live Gemini smoke test
.venv/bin/python -m pytest -q             # 137 passed, 12 skipped
```

`scripts/test_gemini.py` gates its exit code on the **embedding** checks only,
because that is all this project routes through Gemini. Its chat checks are
informational and report `SKIP` on HTTP 429 once the free chat quota is spent.

The key authenticates two ways, and they are not interchangeable:

| Endpoint | Auth | Works |
|---|---|---|
| `generativelanguage.googleapis.com/v1beta/...?key=<KEY>` | query param | yes |
| `generativelanguage.googleapis.com/v1beta/openai/` | `Authorization: Bearer` | yes |
| `generativelanguage.googleapis.com/v1beta/models` | `Bearer` header | 401 `API_KEY_SERVICE_BLOCKED` |

The OpenAI-compatible base URL is the one that matters, because the whole
project talks to its providers through `AsyncOpenAI`.

## Bugs found and fixed

### 1. `GEMINI_API_KEY` was dead config
`grep -rniE 'gemini|generativeai'` over the repo returned zero code hits. Every
LLM call went through `OPENROUTER_API_KEY`.

**Fixed** — new `embeddings_provider.py` builds a Gemini-backed `AsyncOpenAI`
client for embeddings and is wired into `api/main.py`, `workers/message_processor.py`,
`agent/customer_success_agent.py` and `database/seed.py`.

### 2. Knowledge-base semantic search was broken (HTTP 402)
`agent/tools.py` embedded through the OpenRouter client. That account has chat
credits but no embedding credits:

```
POST openrouter.ai/api/v1/embeddings → 402
"Insufficient credits. This account never purchased credits."
```

Every `search_knowledge_base` call failed and returned *"Please escalate to
human support"* — the agent silently lost its knowledge base.

**Fixed** — embeddings go to Gemini through the new provider, independent of
the chat provider.

### 3. The lexical fallback existed but the agent never used it
`database/queries.py:search_knowledge_base_text` was written as the pg_trgm
fallback "when the embedding provider is down, out of credits...". Only
`api/main.py` called it; both agent paths escalated instead.

**Fixed** — `agent/tools.py` and `agent/tools_executor.py` now degrade to
lexical search on any embedding failure and return `search_mode` plus a
`degraded` flag.

### 4. Gemini embeddings default to 3072 dims, the schema needs 1536
`database/schema.sql` declares `vector(1536)`; `gemini-embedding-001` returns
3072 unless `dimensions` is passed. A naive provider swap would have failed at
insert with a pgvector dimension mismatch.

**Fixed** — `EmbeddingProvider.embed`/`embed_many` always pass
`dimensions=EMBEDDING_DIM`, so no call site can forget it.

### 5. Every model name in `.env` 404s on Gemini
`MODEL=nvidia/nemotron-3-ultra-550b-a55b:free`, `OPENAI_MODEL=openai/gpt-4o`
and `EMBEDDING_MODEL=openai/text-embedding-3-small` are OpenRouter-namespaced.
`MODEL` was also read by nothing at all.

**Fixed** — dead `MODEL` var removed; `GEMINI_EMBEDDING_MODEL` added and
documented in all four env files. `EMBEDDING_MODEL` now only applies when
`GEMINI_API_KEY` is empty.

### 6. Gemini chat latency is unusable for this agent's timeout
```
gemini-3.6-flash        3.1s / 14.6s / 16.3s / 17.7s / 35.9s / 503
gemini-3.5-flash-lite   23.9s / 32.6s / 40.6s
gemini-3.1-flash-lite   3.4s / 3.7s / 6.4s
gemini-embedding-001    0.6s / 0.7s / 1.1s   (batch of 5: 1.0s)
```
`MESSAGE_PROCESSOR_TIMEOUT_SECONDS=30` — several chat calls exceeded it, and
one request in six returned 503 under a trivial burst.

**Decision** — chat stays on OpenRouter. This was borne out during testing:
Gemini chat hit its daily quota (HTTP 429) while embeddings kept serving
normally. Had chat been moved over, the agent would be down.

Embeddings also get their own circuit breaker (`CB_EMBEDDINGS_*`) rather than
sharing `CB_OPENAI_*` — otherwise an embedding outage would reject chat calls.

### 7. `.env` was never loaded when `.env.development` existed
`main.py` and `workers/message_processor.py` used `if env_specific.exists(): ...
elif Path(".env").exists(): ...` — an either/or, despite the docstring promising
`.env` as a fallback layer. `.env.development` exists in this repo, so anything
set only in `.env` (including `GEMINI_API_KEY` and `OPENROUTER_API_KEY`) was
invisible to those two entrypoints. `api/main.py` had a third behaviour again:
a bare `load_dotenv()` that read only `.env`.

**Fixed** — new `env_config.load_environment()` loads `.env.<ENVIRONMENT>` then
`.env` (dotenv never overwrites what is already set, so the documented priority
holds). All three entrypoints and `database/seed.py` use it.

Related: `.env.development`, `.env.production` and `.env.example` are tracked in
git, so an empty `GEMINI_API_KEY=` there would shadow the real key in the
gitignored `.env`. Those placeholders are commented out with a note.

### 8. Seeded knowledge-base articles had no embeddings at all
`database/seed.py` inserted articles with plain SQL and no `embedding` column,
leaving it NULL on every row. `ORDER BY embedding <=> $1` is NULL for those
rows, so semantic ranking was meaningless even with a working provider.

**Fixed** — `backfill_knowledge_base_embeddings()` runs as seed step 6, embeds
every row where `embedding IS NULL` in chunks, and casts `$2::vector`
explicitly rather than relying on the pgvector codec being registered on the
connection it happens to acquire. A provider outage logs a warning instead of
failing the seed.

### 9 (bonus). Migration 002 aborted every fresh seed
`002_customer_identifiers.sql` created two indexes that `schema.sql` had already
created, without `IF NOT EXISTS`:

```
Migration failed  error='relation "idx_customer_identifiers_lookup" already exists'
Seed failed
```

Found while verifying the backfill on a clean database — a fresh install could
never be seeded. **Fixed** with `CREATE INDEX IF NOT EXISTS`.

## Verification

Against a throwaway `pgvector/pgvector:pg16` container, seeded from scratch:

```
Seed data inserted        counts={'knowledge_base': 10, ...}
Knowledge base embedded   articles=20
rows=20  null_embeddings=0  dim=1536
```

Agent tool, real Gemini vectors through pgvector:

| query | mode | top article | similarity |
|---|---|---|---|
| "my connector keeps failing to connect" | semantic | Troubleshooting Connection Issues | 0.800 |
| "how much does the enterprise plan cost" | semantic | Enterprise Features | 0.716 |
| "dashboards load slowly" | semantic | Dashboard Customization | 0.638 |

Simulated embedding outage → `search_mode=text`, `degraded=true`, 4 results
(previously: zero results and an escalation).

`GET /knowledge-base` on a live server → `search_mode=vector`, 3 results, top
hit `Troubleshooting Connection Issues` at 0.756.

## Files changed

New: `embeddings_provider.py`, `env_config.py`, `scripts/test_gemini.py`,
`tests/test_embeddings_provider.py` (9 tests), `tests/test_env_config.py` (4 tests).

Modified: `agent/tools.py`, `agent/tools_executor.py`,
`agent/customer_success_agent.py`, `api/main.py`, `workers/message_processor.py`,
`embeddings_service.py`, `database/seed.py`,
`database/migrations/002_customer_identifiers.sql`, `main.py`, and the four
`.env*` files.

## Still open (not caused by these changes)

- `DATABASE_URL` in `.env` fails against the local Postgres:
  `password authentication failed for user "postgres"`. Verification used a
  throwaway container instead.
- Gemini's free-tier chat quota is spent for the day. Embeddings are unaffected
  and have their own quota.

---

## Round 2 — live end-to-end run (2026-09-05)

Ran the whole stack against a real pgvector database, a live API, and the
Next.js form, rather than tests alone. Three further defects surfaced, all of
which the unit tests had passed over.

### Bug 10 — a provider switch silently returns near-random articles

**Severity: high — wrong answers, no error, no flag.**

`knowledge_base.embedding` recorded no model. Vectors from two embedding models
occupy unrelated coordinate spaces, so querying a Gemini-indexed knowledge base
with an OpenAI-generated vector produced this:

| index model | query model | top similarity | reported mode |
|---|---|---|---|
| `gemini-embedding-001` | `gemini-embedding-001` | **0.6326** | `vector` |
| `gemini-embedding-001` | `openai/text-embedding-3-small` | **0.0432** | `vector` (no `degraded` flag) |

0.04 cosine is noise. The agent served whichever article happened to sort first
and reported it as a healthy semantic hit. This is worse than the original 402
bug, which at least failed loudly.

**Fix:** migration `009_kb_embedding_model.sql` adds `knowledge_base.embedding_model`;
`search_knowledge_base()` takes `embedding_model=` and matches only rows indexed
by the same model; all three call sites (`/knowledge-base`, `agent/tools.py`,
`agent/tools_executor.py`) treat an empty vector result as *degrade to lexical*,
and set `degraded: True` **even when lexical also returns nothing** — an
unqualified empty `vector` result reads as "no such article", a different and
wrong answer. `database/seed.py` re-embeds any row whose `embedding_model`
differs from the active provider.

### Bug 11 — the knowledge base seed was not idempotent

`seed.py` ended its article INSERT with `ON CONFLICT DO NOTHING`, but no unique
constraint existed for it to conflict against, so the clause never fired. Three
seed runs left 30 rows for 10 articles, and duplicates crowded out distinct
articles in top-N results — a 5-result query returned the same two articles twice.

**Fix:** migration `010_kb_unique_title.sql` collapses existing duplicates
(keeping the oldest row per title) and adds a unique index on `title`; the seed
now says `ON CONFLICT (title) DO NOTHING`. Verified: a second seed run reports
`kb_embedded=0` and the table holds 10 rows / 10 distinct titles.

### Bug 12 — the lexical fallback matched whole phrases only

The safety net added in round 1 passed the entire user message to
`ILIKE '%…%'`. No article contains the phrase *"my dashboard is loading very
slowly"*, so the fallback returned nothing exactly when it was needed.

**Fix:** `search_knowledge_base_text()` now matches per word, using
`plainto_tsquery` rewritten from AND to OR semantics (keeping stemming and
stopword removal), with trigram and ILIKE retained for short queries and
misspellings. Measured before/after on the mismatch path:

| query | before | after |
|---|---|---|
| `my dashboard is loading very slowly` | *(no results)* | Dashboard Customization |
| `I cannot connect my database` | *(no results)* | Troubleshooting Connection Issues |
| `how do I export my data` | *(no results)* | Exporting Data |
| `billing charges are wrong` | *(no results)* | Account Setup and Billing |

### Live verification

| step | result |
|---|---|
| pgvector container + migrations 001–010 | applied clean on a fresh database |
| seed with Gemini backfill | 10 articles, all tagged `gemini-embedding-001` |
| seed re-run (idempotency) | `kb_embedded=0`, no duplicates |
| `GET /health` | `{"status":"healthy","db":"ok"}` |
| `GET /knowledge-base` (healthy) | `search_mode=vector`, top hit 0.6326, ~1.0 s |
| `GET /knowledge-base` (model mismatch) | `search_mode=text`, `degraded=true`, correct article |
| `POST /webhooks/webform` | 201, ticket `TKT-…` created |
| Next.js form (`web-form`) | compiles and serves |
| test suite | **141 passed, 12 skipped** |
| `ruff check --select F,E9` on changed files | clean |

### Open blockers — not code defects

1. **Both chat providers are currently unusable.** OpenRouter returns
   `402 … can only afford 44 tokens`, so `POST /webhooks/webform` creates the
   ticket but logs `Synchronous agent reply failed` and the customer gets no
   reply. The degradation is graceful — the ticket is never lost — but the agent
   cannot answer until credits are added.
2. **Gemini is not a drop-in chat replacement.** `gemini-3.6-flash` → 429 quota
   exhausted; `gemini-flash-latest` → 503 high demand; `gemini-flash-lite-latest`
   → works but **32 s**, above the 30 s worker timeout. Embeddings are unaffected
   (~1 s), which is why the split provider design stands. Add OpenRouter credits
   or move to a paid Gemini tier before relying on agent replies.
3. **Browser automation was unavailable.** `list_connected_browsers` returned
   `[]` (Chrome extension not connected), so the UI was exercised over HTTP
   rather than driven visually.
