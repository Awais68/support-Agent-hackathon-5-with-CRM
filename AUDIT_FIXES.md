# AUDIT fixes — checklist

- **Audit:** `AUDIT.md`, run on `fix/known-issues` @ `5e9d39a`.
- **Fix branch:** `fix/audit`, cut from `fix/known-issues` @ `5ca1c85` (10 commits after the audited
  commit: Round 2 of FIX_REPORT.md plus the lint pass `24467a0`).
- **Rules:** one commit per finding; security fixes ship with a test that reproduced the exploit
  first (the "before" column records it failing); nothing merged.
- **Status values:** `Open`, `Fixed (<commit>)`, `Already fixed (<commit>, re-tested)`,
  `Not fixed — <reason>`, `Needs you — <what>`.

IDs are the audit's own (`S*` security §8, `N*` not-working §4). `G*` = §5 gaps, `M*` = §6
missing, `C*` = issues named only in the §2 category table, `R2-*` = FIX_REPORT Round 2.

## Reconciliation (Step 0)

Commits between the audited `5e9d39a` and `5ca1c85`, re-tested on `fix/audit` before any change:

| Item | Claimed in | Re-test on `fix/audit` @ 5ca1c85 | Result |
|---|---|---|---|
| N1 (lint part): ruff + black red | 24467a0 | `ruff check .` → "All checks passed"; `black --check .` → 70 unchanged | Already fixed. E501 ignored with a written reason in `pyproject.toml` (black owns line length) |
| N1 (mypy part) / N16 | — | `mypy .` → "Found 1 error … errors prevented further checking" | Open |
| R2-A operator-voice reply | fb2358e | `tests/test_customer_reply.py` 17 passed | Already fixed |
| R2-B `general` category | 211ff9f | `tests/test_kb_category.py` (needs PG; re-run on the live stack in P0 gate) | Already fixed |
| R2-C sentiment gate | 7ec89d9 | `python -m agent.gate_eval` → TP=20 FP=1 FN=0 TN=21, P=0.95 R=1.00 | Already fixed |
| N12 legal stems | 7ec89d9 | `\blawsuits?\b`, `\blitigat` present | Partly fixed; billing patterns still broad (R2-4) |
| C-uv.lock untracked | 2263e88 | `git ls-files uv.lock` tracked | Already fixed |
| H3 live-stack tests not in CI | b50f5ad | `ci.yml` job `live-stack` present; PR #1 run green | Already fixed |

All three Round 2 items (operator voice, `general`, sentiment gate) are done, so P1 #11 adds
nothing new; their open follow-ups are R2-1…R2-8 below.

## P0

| ID | Sev | Finding | Status | Exploit test (before → after) |
|---|---|---|---|---|
| S1 | Critical | Web-form proxy `/api/tickets/[id]` attaches the master key; `..%2F` reaches any GET | Fixed (S1 commit) | `tests/test_proxy_exploits_live.py`: 10 failed (all audit URLs 200 with ticket/customer/metrics data) → 11 passed (400; own ticket via token/email 200, wrong token 404) |
| S13 | Low | `get_ticket` exposes `agent_runs` (internal prompts, errors) | Fixed (S1 commit): customers only see `/public/tickets/*`, which drops agent_runs, customer identity, assignment and message metadata; `/tickets/{id}` stays key-only | `tests/test_ticket_tracking.py::test_valid_token_returns_redacted_view` |
| S2 | High | Fuzzy email identity merge (`alice1@` → Alice) | Fixed (S2 commit): exact normalized email only in `get_customer_or_create_by_identifier`, `create_ticket`, `find_customer_by_name_email`, `get_customer_history`; look-alikes become a new customer with a review hint. Existing links: see "Data clean-up" below | `tests/test_identity_exact_match.py` (real PG): 6 failed → 7 passed |
| S3 | High | LLM tools take model-supplied `ticket_id` / `customer_email` | Fixed (S3 commit): the agent's `ToolContext` is bound to the run's ticket/customer; the model is offered no ID/target parameters and no `create_ticket`, any IDs it sends are replaced, history is read by customer id, `send_response` checks ticket ownership, history `limit` capped at 50. Not yet scoped (no route reaches them): `agent/tools_executor.py`, `mcp_server.py` — see P2 | `tests/test_tool_scope.py` (real PG, scripted prompt injection): failed (Alice's ticket returned to Mallory's run) → 3 passed |
| S4 | High | WebSocket `/ws/tickets/{id}` unauthenticated | Fixed (S4 commit): handshake needs the ticket's tracking token (`?token=`) or the master key in `X-API-Key`; otherwise closed with 1008 before accept (HTTP 403 on the wire). The tracking page passes its token and stops reconnecting on 1008 | `tests/test_websocket_auth.py`: 4 failed (sockets accepted with no/wrong credential) → 6 passed; live: no key → 403, key → accepted |
| S5 | High | `.env.development*`, `credentials.json`, `.kilo/` baked into the image | Fixed (S5 commit): both `.dockerignore` files exclude `**/.env*` (except `*.example`), `**/*.bak`, `credentials.json`, `client_secret*.json`, `gmail_*.json/.pickle`, keys/certs, root `*.sql` dumps, `.claude/`, `.kilo/`, scratch dirs. Patterns are scoped so `channels/gmail_handler.py` and `database/migrations/*.sql` stay in. Any image built before this (local, registry, Render) still holds the old secrets: rotate them (see Secrets to rotate) | `tests/test_docker_context.py` (Docker evaluates the real ignore file against decoys): 2 failed (13 / 18 decoys copied) → 2 passed; rebuilt api/worker/kb-embed/web-form images: 0 secret files |
| S6 | Medium | DB URL with password logged (worker, seed) | Fixed (S6 commit): new `utils/redact.redact_dsn` masks the password (keeps user/host/db); used by the worker startup log and the seed/kb-embed log. No other log call carries a credential-bearing URL (grepped). Old log lines in any aggregator still hold the password: rotate it | `tests/test_log_redaction.py`: 2 failed (password in captured startup logs) → 8 passed; live worker log now `postgresql://techflow:***@postgres:5432/techflow` |
| N4 | High | Kafka auto-commit + swallowed exception = message loss | Fixed (N4 commit): auto-commit off; the offset is committed only after the handler succeeds or the message is written to the `dlq` topic (retries: `KAFKA_HANDLER_MAX_ATTEMPTS`, default 3, exponential backoff); if the DLQ write fails nothing is committed. Handler errors now propagate. Idempotency: new `inbound_processing` ledger (migration 012) keyed on the Kafka message id skips finished messages, reuses the ticket a dead delivery created and gives up after `INBOUND_MAX_DELIVERIES` (10); the agent publishes the reply before marking its run completed, a redelivery skips the agent if a successful run exists since the claim, failed runs are now stored as `failed`, and the sender dedups on `inbound:<message id>`. Note: the agent also emits its own `dlq` event per failed run, so one message can produce one agent DLQ event per attempt plus the consumer's final one | `tests/test_inbound_redelivery.py` (real Kafka + PG, worker SIGKILLed): kill mid-agent failed (message lost) → passes, exactly one ticket, one completed run, one reply; kill after agent passes; failed first attempt is retried and answered. `tests/test_consumer_commit.py` 8 passed; `test_notification_sender.py::test_rerun_for_same_inbound_message_is_not_sent_twice` failed → passes. Live (LLM unreachable): 3 attempts, agent re-run each time |
| S7 | Medium | `/api/voice` proxy attaches the master key; no size cap / rate limit | Fixed (S7 commit): the web form no longer holds the master key (removed from compose and `.env.local.example`); the proxy adds no credentials and refuses bodies over the cap (Content-Length and streamed count, 413). `/webhooks/voice/message` is public but capped: body > base64(`VOICE_MAX_AUDIO_BYTES`, default 5 MB)+4 KB → 413 before parsing, no Content-Length → 411, decoded audio re-checked; `audio_url` (server-side fetch) needs the API key (403). Existing `strict_limit` (10/min/IP) stays. Not done here: per-session tokens and a global LLM/STT spend cap (shared with S8, see P1/P2); behind the proxy every browser shares the web-form IP for the limiter (see rate-limit note). Local `web-form/.env.local` still has `API_KEY_SECRET`: delete that line | `tests/test_voice_public_limits.py`: 3 failed (12 MB-class body processed, no key path) → 5 passed; live `test_proxy_exploits_live.py`: 2 failed (12 MB via proxy → 200; web-form container env had `API_KEY_SECRET`) → 14 passed. `test_security.py::test_requires_api_key` replaced by `test_audio_url_requires_api_key` (endpoint is public by design now) |

### P0 gate (2026-10-09, `fix/audit` @ 7692662)

- **Exploits re-run:** every P0 exploit test passes on the fixed code: S1/S7 live proxy (14), S13, S2 (7, real PG),
  S3 (3, real PG), S4 (6), S5 (2, real Docker), S6 (8), N4 (3 real Kafka + PG redelivery, 8 commit,
  1 sender dedup), S7 API (5).
- **Full suite** against the live compose stack (providers mocked: every LLM/STT/Twilio/Gmail URL points at a
  closed port): 311 passed, 0 failed, 0 skipped (`-m` all, incl. e2e/Playwright; the Docker-context test needs
  the buildx plugin, run outside the tool sandbox).
- **Chrome:** form submit → ticket number → "Track Your Ticket" opens `/ticket/<n>?t=<token>`, shows the
  redacted ticket, WebSocket accepted with the token; worker processed the message once (ledger `done`, attempt 1).
- **CI:** not recorded yet: the push of `fix/audit` was not permitted from this session (see PR checklist).

## P1

| ID | Sev | Finding | Status |
|---|---|---|---|
| N5 | High | Human `/reply` never reaches email/WhatsApp | Fixed (N5 commit): `/tickets/{id}/reply` publishes `{reply_message_id, source: human, customer_reply, channel, customer_email}` to `notifications.outbound`; a publish failure returns 503 naming the saved message instead of a silent 201. The sender delivers these under `reply:<message id>` (deduped), skips the agent operator-voice guard for them, and still skips in-app channels. Tests: `tests/test_human_reply.py` 2 failed → passed; 3 new sender tests failed → passed. Live: reply → `outbound_deliveries` row `reply:<id>` (failed only because Gmail is mocked) |
| N6 | Medium | `/health` 200 with DB down; no liveness/readiness split | Fixed: `/livez` (process only) and `/readyz` (DB + requested Kafka, 503 when down; DB ping bounded by `READINESS_DB_TIMEOUT_SECONDS`, default 2s, because a paused DB made it hang). `/health` = `/readyz`. k8s api liveness→`/livez`, readiness→`/readyz`; Render and compose api use `/readyz`. Tests: `tests/test_health_probes.py`. Live: paused postgres → `/readyz` 503 in 2.0s, `/livez` 200, recovers on unpause. |
| S9 | High | next@14.2.35 critical/high advisories | Open |
| G2 / S8 (rate limit) | Medium | In-memory per-replica limiter, keyed on proxy IP | Open |
| N1 | High | CI never green (lint part already fixed) | Open (mypy) |
| N16 | Medium | mypy aborts on duplicate module | Open |
| N2 | High | CD 0/7 (uppercase GHCR name, not gated on CI, no migrations) | Open |
| N3 | High | chaos.yml invalid YAML (0/7) | Open |
| R2-A/B/C | — | Round 2 items | Already fixed (see reconciliation) |

## P2

| ID | Sev | Finding | Status |
|---|---|---|---|
| S8 | Medium | No body size cap; 2 MB echoed in 422; no spend cap | Open |
| S10 | Medium | WhatsApp signature fails open; no URL override | Open |
| S11 | Medium | `/metrics` unauthenticated | Open |
| S12 | Low | Default creds, ports on 0.0.0.0 | Open |
| S14 | Info | Public repo; keep secrets out; pin gitleaks | Open |
| N7 | Medium | Web-form `/webhooks/*` rewrite → container localhost | Open |
| N8 | Medium | Gmail mounts create root-owned dirs | Open |
| N9 | Medium | No consumer for `escalations` etc. | Open |
| N10 | Low | 17/22 Prometheus metrics dead, no worker metrics | Open |
| N11 | Low | `agent_runs.duration_ms` in seconds, `model` wrong, tokens overwritten | Open |
| N12 | Low | Billing patterns escalate every billing question | Open |
| N13 | Low | SSRF IP-literal branch dead | Open |
| N14 | Medium | render.yaml undeployable as written | Open |
| N15 | Medium | k8s manifests won't deploy | Open |
| G1 | — | Single shared key, no users/roles | Open |
| G3 | Medium | Unbounded `audio_base64`, `hours`, `limit` | Open |
| G4 | Medium | No consumer DLQ; DLQ payloads carry PII | Open (consumer DLQ in N4) |
| G5 | Low | Blocking I/O in async (Gmail, gTTS) | Open |
| G6 | Low | Hardcoded `support.techflow.com`; LLM invents URLs | Open |
| G7 | Low | gitleaks unpinned; pre-push hook scans HEAD only | Open |
| G8 | Info | Python ≥3.13 declared, tests pass on 3.12 | Open |
| C1 | Low | Misleading comment `message_processor.py:90` | Open (removed with N4) |
| C2 | Medium | Worker healthcheck `kill -0 1` | Fixed with N6: each consumer refreshes `$WORKER_HEARTBEAT_DIR/<group>` (also when idle) unless one handler call exceeds `WORKER_STALL_SECONDS` (600); `python -m utils.worker_healthcheck --max-age N` fails on any stale file. k8s worker liveness 120s / readiness 30s, compose 60s. Tests: `tests/test_worker_heartbeat.py`. Live: compose worker `healthy`, 3 heartbeat files. |
| C3 | Low | Tests accept HTTP 500 (`tests/test_e2e.py:20,37,55`) | Open |
| C4 | Medium | Migrations 001/004 seed demo data; two runners; no downs; 010 unbounded DELETE; no retention | Open |
| C5 | Low | Docs: inconsistent test counts; DEPLOYMENT.md stale | Open |
| C6 | Low | Front end: channel `web` ≠ `webform`; voice `audio/webm` only; react-hooks warnings | Open |
| C7 | Low | Compose is a dev config (`--reload`, `.:/app`, Grafana admin/admin) | Open |
| C8 | Medium | Worker processes one message at a time | Open |
| M* | — | §6 "Missing" (tenant model, RBAC, backups, tracing, Sentry, SBOM, …) | Open |
| R2-1 | Low | Reply guard is a heuristic | Open |
| R2-2 | Low | Replies open with "Great question" | Open |
| R2-3 | Low | `send_response` publishes a second payload shape on `notifications.outbound` | Open |
| R2-4 | Low | = N12 billing patterns | Open |
| X1 | Low | (found in P0 gate) uvicorn access log prints the WebSocket URL including `?token=` (tracking token) | Open |
| X2 | Low | (found in P0 gate) when the agent falls back without `send_response` (circuit open, model answered in text) on webform/voice, the reply is published but never stored, so the tracking page shows nothing | Open |
| R2-5 | Low | Emotion stems in `sentiment_analyzer.py` end in `\b` | Open |

## Data clean-up (you)

S2 stops new fuzzy links but does not touch existing rows. List email identifiers that
differ from their customer's own email, review each, and move the wrong ones with
`POST /customers/{id}/merge` or delete the identifier row:

```sql
SELECT c.id, c.email AS customer_email, ci.identifier_value AS linked_email, ci.created_at
FROM customer_identifiers ci JOIN customers c ON c.id = ci.customer_id
WHERE ci.identifier_type = 'email' AND lower(ci.identifier_value) <> lower(c.email)
ORDER BY ci.created_at;
```

## Secrets to rotate (you)

Anything that was in `.env*`, `credentials.json` or `gmail_*` at the time an image was built before
d7a647c (S5) is in that image's layers; the DB password was also in worker/seed logs before b403af6 (S6).
The web form held `API_KEY_SECRET` until 7692662 (S7). Rotate, then rebuild and redeploy:

- `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY` (and `GROQ_API_KEY` / `OPENAI_API_KEY` if set)
- Gmail OAuth client secret (`GMAIL_CLIENT_SECRET` / `credentials.json`) and revoke the refresh token in `gmail_token.json`
- `API_KEY` / `API_KEY_SECRET` (also changes tracking tokens unless `TRACKING_TOKEN_SECRET` is set separately)
- Database password (`POSTGRES_PASSWORD` / `DATABASE_URL`) for every environment that ran the old worker
- Twilio `TWILIO_AUTH_TOKEN` (and the account SID's API keys if any)
- Delete pushed images built before d7a647c from GHCR / Render
- Locally: remove the `API_KEY_SECRET` line from `web-form/.env.local`
