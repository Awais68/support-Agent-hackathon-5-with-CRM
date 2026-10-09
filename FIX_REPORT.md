# Fix Report — `fix/known-issues`

Date: 2026-10-09. Branch `fix/known-issues`, based on `main` (`0523db0`). Not pushed.

Every status below comes from a command that was actually run on this machine. Where a check was
not possible, it says so and gives the reason.

## Summary

| # | Issue | Fix (files changed) | How verified | Status |
|---|---|---|---|---|
| 1 | The web-form Docker build failed: `"/app/public": not found` | `web-form/public/.gitkeep` (87db9e7). `web-form/package-lock.json` regenerated with npm 10 (deac062), because the npm 11 lockfile broke `npm ci` in `node:20-alpine` | `docker compose build web-form` ✅; `npm ci` inside `node:20-alpine` ✅; full `docker compose up --build` ✅ | **Verified** |
| 2 | Nothing consumed `notifications.outbound`, so email/WhatsApp replies were never delivered | `workers/notification_sender.py` (new consumer group `techflow-notification-sender`). Tenacity retries with exponential backoff (`OUTBOUND_MAX_ATTEMPTS`, default 3), then DLQ. Idempotency through the new table `outbound_deliveries`, keyed `agent_run:<id>` (`database/migrations/011_outbound_deliveries.sql`). Wired into `workers/message_processor.py`. Env vars in `.env.example`. Tests in `tests/test_notification_sender.py` (6ea448b, ab74780, ca8d11b) | (a) 10 tests against **real PostgreSQL + real Kafka** with **mocked Gmail/WhatsApp providers**, all passing. They cover: sent once, redelivery not resent, WhatsApp to the customer phone, transient retry, retries exhausted → `failed` + DLQ, not-configured → 1 attempt, webform skipped, mid-run tool event superseded, missing token → no OAuth hang, and Kafka topic message → `send` called once. (b) Live stack: an `inbound.email` event went through the real worker → agent → `notifications.outbound` → sender → no Gmail token → `failed` after 1 attempt → DLQ offset 6→7 (log below) | **Verified with mock** (providers mocked; no real Gmail/Twilio credentials were used, so no real email or WhatsApp message was sent) |
| 3 | Compose applied only `schema.sql` + `001`, so KB vector search failed | `docker-compose.yml` adds a one-shot `migrate` service (`scripts/render_migrate.sh`: schema + `001–011`, tracked in `_migrations_applied`, safe to re-run) and a `kb-embed` service (`database/seed.py --embed-kb-only`) (2d3ad4d) | `migrate` exited 0 and `kb-embed` exited 0 (10 articles embedded). Re-running `migrate` is a no-op. In Chrome, a technical question produced `search_knowledge_base` with `search_mode=semantic`, 5 results, and the answer came from the "API Documentation" article (screenshot 07/08) | **Verified** |
| 4 | k8s: the worker pod ran uvicorn; KEDA watched a non-existent topic/group; `idleReplicaCount: 0` contradicted `minReplicaCount: 2` | `k8s/deployment-worker.yaml` now has `command: ["python", "-m", "workers.message_processor"]`. `k8s/scaledobject-worker.yaml` gets real `inbound.*` topics, group `techflow-message-processor`, and drops `idleReplicaCount` (446a337) | `kubeconform -summary` passes: 22 valid, 0 invalid, 2 skipped (no schema); the ScaledObject is valid against the KEDA CRD schema. `kubectl apply --dry-run=server` against a throwaway k3s v1.30.4 container passed at 446a337, and `k8s/` is unchanged since then (`git diff 446a337 HEAD -- k8s` is empty). The real AKS cluster in the kubeconfig was not touched (unreachable) | **Verified** (schema + server dry-run; not deployed to a real cluster) |
| 5a | CORS preflight returned 401 | `api/main.py`: OPTIONS skips the API-key check; CORS is the outermost middleware, so 401s also carry CORS headers. Origins come from `CORS_ORIGINS`, and `*` is rejected (2b59848). Tests in `tests/test_security.py` | Unit tests: allowed-origin preflight 2xx, unknown origin gets no ACAO, 401 carries ACAO. In Chrome, the cross-origin POST from `localhost:3000` → `localhost:8000/webhooks/webform` returned 201 with **no console errors** | **Verified** |
| 5b | `npm run lint` opened an interactive prompt | `web-form/.eslintrc.json`, `eslint` + `eslint-config-next` dev deps, 2 lint fixes (9a0198e) | `npm run lint` runs non-interactively (warnings only, exit 0); `npm run build` ✅ | **Verified** |
| 6a | Hardcoded `test-key-12345` fallback | `api/main.py`: no fallback. Startup raises `ConfigurationError` without a key unless `RUN_MODE=test`. Docs/examples/scripts updated (793a905) | Tests: the old key is rejected with 401 when no key is set; startup fails without a key | **Verified** |
| 6b | Voice webhooks unauthenticated | `/webhooks/voice/call` requires a valid `X-Twilio-Signature` (fails closed without `TWILIO_AUTH_TOKEN`). `/webhooks/voice/message` requires the API key. The web form calls it through a new server-side proxy (`web-form/src/app/api/voice/route.ts`), so the key never reaches the browser (3923232) | Tests: missing, forged, and no-token signatures → **403**; a valid signature → 200 TwiML; `/voice/message` without a key → **401** | **Verified** |
| 6c | SSRF via `audio_url` | `utils/safe_fetch.py`: HTTPS only, host allowlist (`AUDIO_URL_ALLOWED_HOSTS`, default Twilio), IP literals and credentials rejected, every resolved address must be public, each redirect hop re-checked, streaming size cap. Used by `channels/voice_handler.py` (eec1ab6) | Tests: metadata IP, loopback, `localhost`, the internal `api` host, `file://`, private DNS answers (v4/v6/mapped), and redirect-to-internal are all **rejected**; an oversized body is rejected; the endpoint returns **400** for `169.254.169.254` | **Verified** |

### Live worker flow (fix 2, real worker, no mocks except the missing credentials)

```
inbound.email            -> Message classified  ticket TKT-20261009-9A4A40
agent                    -> search_knowledge_base, get_customer_history, send_response
notifications.outbound   -> (send_response event)  "superseded by final agent reply"
notifications.outbound   -> (final reply, agent_run_id=45b1855f-…)
notification sender      -> Outbound delivery failed attempts=1 error="Gmail token file 'gmail_token.json' not found"
dlq                      -> message published (offset 6 -> 7)
```

Consumer lag after the run was 0 for both `techflow-message-processor` (inbound.*) and
`techflow-notification-sender` (notifications.outbound).

## Test counts

| | Before (`main`) | After (`fix/known-issues`) |
|---|---|---|
| `pytest` without a live stack | 141 passed, 12 skipped | Not run separately (the stack was up for the whole session). The 10 new outbound tests skip without a DB, and the Kafka one also skips without `OUTBOUND_TEST_KAFKA` |
| CI filter `-m "not slow and not integration and not e2e"` (Playwright deselected) | 141 passed | **178 passed** |
| Full suite with the live stack (`E2E_API_KEY`, `OUTBOUND_TEST_DATABASE_URL`, `OUTBOUND_TEST_KAFKA` set) | not run | **192 passed, 0 skipped** |
| Playwright (`tests/test_e2e_playwright.py`) against the compose stack | 12 skipped | **12 passed** |
| `web-form`: `npm run build` / `npm run lint` | ✅ / ❌ interactive | ✅ / ✅ |

## Chrome E2E (`docker compose -p spfix up -d --build`)

Containers: `api`, `postgres`, `kafka`, and `worker` were **healthy**. `migrate` and `kb-embed`
exited 0. `web-form`, `zookeeper`, `prometheus`, `grafana`, and `postgres-exporter` were running
(they have no healthchecks).

Screenshots are in [`docs/fix-report/`](docs/fix-report/):

| Step | Screenshot | Result |
|---|---|---|
| Form loads | `01-form-initial.jpg` | ✅ |
| Empty submit | `02-empty-form-validation.jpg` | ✅ field errors, no request sent |
| Invalid input (bad email, short subject/message) | `03-invalid-input.jpg` | ✅ field errors |
| Valid form | `04-form-filled.jpg` | ✅ |
| Submit (general category) | `05-ticket-submitted-escalated.jpg` | ✅ 201; ticket in DB; agent ran but **escalated** (see finding 1) |
| Submit (frustrated wording) | `06-ticket-sentiment-escalated.jpg` | ✅ 201; sentiment gate escalated (score 0.24) |
| Submit (technical KB question) | `07-ticket-kb-vector-answer.jpg` | ✅ 201; `search_mode=semantic`, 5 results; status `open` with the agent's answer |
| Tracking page | `08-tracking-page.jpg` | ✅ `/api/tickets/{id}` 200, shows the customer and agent messages; no console errors |

For webform tickets, the agent's reply rows appear in `outbound_deliveries` as `skipped`
(an in-app channel, read on the tracking page).

## New findings (not fixed)

1. **The agent passes the form category as a KB filter.** For `category=general` the KB filter
   returns 0 results, the agent falls back to text search, and the ticket escalates.
2. **The sentiment gate escalates neutral technical questions.** The phrase "failing with 429"
   scored 0.24, below the 0.3 threshold, so no tools ran.
3. **The final agent reply is operator-facing.** `output_message` sometimes reads like "I've
   responded to the customer…", and that is what `notifications.outbound` delivers to email/WhatsApp.
   This needs a prompt or format fix before turning on real Gmail sending.
4. **Missing bind-mount sources become directories.** When `gmail_token.json` and
   `gmail_credentials.json` are absent, Docker creates root-owned directories with those names in
   the repo, and Gmail polling then fails on them (the sender now treats this as "not configured").
5. **Two producers publish to `notifications.outbound` with different payloads.** The
   `send_response` tool event has `content` and no `agent_run_id`; the final reply has `message`
   and `agent_run_id`. Only the latter is delivered.
6. **`GmailHandler.authenticate()` falls back to interactive `InstalledAppFlow`,** which would
   hang a container. The sender guards against this; the polling path doesn't.
7. **`WhatsAppHandler.send_message` returns sentinel strings** (`not-configured`, `circuit-open`)
   instead of raising.
8. **`KafkaProducerClient` retries only `ConnectionError`/`TimeoutError`,** so the first publish to a
   cold broker can fail.
9. **The Playwright tests aren't marked `e2e`.** With the stack up but browsers not installed, they
   error instead of skipping. They also need `E2E_API_KEY` to match the server's `API_KEY_SECRET`.
10. **The Twilio signature URL is computed from `request.url`.** Behind a TLS-terminating proxy or
    ingress the scheme/host differ, so valid signatures fail. A `PUBLIC_BASE_URL` override is
    needed.
11. **`safe_fetch` has a DNS-rebinding window.** It resolves then connects separately; this is
    mitigated by the host allowlist.
12. **The k8s worker probes are no-ops** (`sys.exit(0)`), and the worker Deployment lacks the
    `GEMINI_*`/`DEEPSEEK_*` env vars.
13. **k8s/Render web form still bakes an internal `NEXT_PUBLIC_API_URL`** and has no
    `API_INTERNAL_URL`/`API_KEY_SECRET`. Only compose was fixed (0d058f8).
14. **Two migration runners keep different tracking tables** (`schema_migrations` vs
    `_migrations_applied`).
15. **`database/seed.py` logs `db_url` with the password.**
16. **Tracking URLs point at `support.techflow.com`.**
17. **Ruff and black were already red on `main`.** This branch adds no new violations in the files
    it touches.
18. **Repo hygiene:**
    - `uv sync` generates an untracked `uv.lock`.
    - A `.kilo/worktrees` copy of the repo lives in the working directory.
    - The committed `.venv` shebangs point at a moved path; use `uv run python -m pytest`.

## Teardown

All temporary containers, volumes, and images for this run were removed (`docker compose -p spfix
down -v`, the throwaway `sp-fix-pg` and k3s containers, and the scratch images). Unrelated local
containers were not touched.
