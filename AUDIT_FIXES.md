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
| S1 | Critical | Web-form proxy `/api/tickets/[id]` attaches the master key; `..%2F` reaches any GET | Open | |
| S13 | Low | `get_ticket` exposes `agent_runs` (internal prompts, errors) | Open (fixed with S1's public view) | |
| S2 | High | Fuzzy email identity merge (`alice1@` → Alice) | Open | |
| S3 | High | LLM tools take model-supplied `ticket_id` / `customer_email` | Open | |
| S4 | High | WebSocket `/ws/tickets/{id}` unauthenticated | Open | |
| S5 | High | `.env.development*`, `credentials.json`, `.kilo/` baked into the image | Open | |
| S6 | Medium | DB URL with password logged (worker, seed) | Open | |
| N4 | High | Kafka auto-commit + swallowed exception = message loss | Open | |
| S7 | Medium | `/api/voice` proxy attaches the master key; no size cap / rate limit | Open | |

## P1

| ID | Sev | Finding | Status |
|---|---|---|---|
| N5 | High | Human `/reply` never reaches email/WhatsApp | Open |
| N6 | Medium | `/health` 200 with DB down; no liveness/readiness split | Open |
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
| C2 | Medium | Worker healthcheck `kill -0 1` | Open |
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
| R2-5 | Low | Emotion stems in `sentiment_analyzer.py` end in `\b` | Open |

## Secrets to rotate (you)

Filled in at the end of P0 (S5/S6).
