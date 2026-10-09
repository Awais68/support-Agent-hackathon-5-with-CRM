# Rate limiting

How the API limits requests today, what that does and does not protect
against, and what to change before running more than one replica or putting
it behind a proxy. Code: `api/rate_limiter.py` (slowapi), decorators in
`api/main.py`.

## Limits

| Scope | Limit | Env | Routes |
|---|---|---|---|
| Default | 100/min | `RATE_LIMIT_PER_MINUTE` | every route without its own decorator |
| Strict | 10/min | `STRICT_RATE_LIMIT_PER_MINUTE` | `POST /tickets/{id}/reply`, `/webhooks/whatsapp`, `/webhooks/webform`, `/webhooks/voice/message`, `/webhooks/voice/call`, `/voice/transcribe`, `/voice/translate`, `POST /customers/{id}/merge`, `/knowledge-base/ingest` |
| Fixed | 20/min | — | `POST /tickets` |
| Fixed | 30/min | — | `GET /public/tickets/{number}`, `/customers/{email}/history`, `/customers/review-queue`, `POST /customers/{id}/review/dismiss` |
| Global budget | 300/hour, all clients together | `PUBLIC_LLM_BUDGET_PER_HOUR` | `/webhooks/webform`, `/webhooks/voice/message` (one shared bucket) |
| Exempt | — | — | `/health`, `/livez`, `/readyz` (metrics are on the internal port 9100, outside the app) |

The WebSocket is not rate limited. Over the limit the API answers
`429 {"error":"RATE_LIMIT_EXCEEDED"}` with `Retry-After`.

Limits apply per key. The key is `client_ip()` in `api/rate_limiter.py`: the
TCP peer address, or, when the peer is listed in `TRUSTED_PROXIES`, the
right-most `X-Forwarded-For` hop that is not itself a trusted proxy. The
global budget uses one key for everyone, so it is a spend circuit breaker:
rotating IPs does not get around it. When it is used up, the form answers
429 for every visitor until the hour window moves on. Size it to the LLM
spend you accept per hour. There is no per-account or per-email limit.

## Body size and error echo

Requests without a valid API key may not send a body over
`PUBLIC_MAX_BODY_BYTES` (default 64 KB): 413 from the key middleware before
the body is read, 411 for a chunked body without `Content-Length`. The voice
message route keeps its own larger cap (`VOICE_MAX_AUDIO_BYTES`). Validation
errors (422) list the field and the reason but no longer echo the submitted
`input`.

## Storage: one bucket per process unless `REDIS_URL` is set

Without `REDIS_URL` the counters are in process memory (`memory://`):

- Each API process counts on its own. With N replicas (k8s runs 2,
  `k8s/deployment-api.yaml`) or N uvicorn workers, a client gets about
  N × the limit, more if the load balancer spreads its requests.
- A restart or deploy resets every counter.

**Multi-replica rule: set `REDIS_URL` on every API replica, pointing at the
same Redis.** Then all replicas share one counter per key. `render.yaml`
already declares `REDIS_URL` (`sync: false`, value set in the dashboard);
compose and the k8s manifests do not set it, so they run in memory. The
startup log says which one is in use: `rate_limiter_redis_enabled` or
`rate_limiter_memory_fallback`.

If Redis is unreachable, slowapi raises on the request rather than silently
skipping the limit. Treat Redis as a hard dependency of the API once it is
configured.

## Key: behind a proxy every user shares one bucket (unless trusted)

The key is whoever opened the TCP connection to uvicorn. That is the end user
only when nothing sits in front of the API. In this repo something always
does:

- **Web form proxy.** The browser calls Next.js (`/webhooks/*` rewrite in
  `web-form/next.config.js`, `/api/tickets/[id]`, `/api/voice`), and Next.js
  calls the API. Every customer gets the web-form container's address.
- **Ingress / Render.** The peer is the ingress controller or Render's edge.

Measured on the compose stack (2026-10-09): 34 requests to
`/api/tickets/<n>` through the web form, each with a different
`X-Forwarded-For`, gave `404×18, 429×16`. The API logged
`client_ip=172.20.0.10` (the web-form container) for all of them, so they all
counted against one 30/min bucket. Two effects:

1. **Availability.** One busy client (or attacker) exhausts the bucket for
   every customer behind the same proxy. Ten form submissions a minute in
   total, across all users, then everyone gets 429.
2. **No real per-client limit.** The limiter cannot tell clients apart, so it
   is a global cap per proxy, not abuse protection per user.

Do not "fix" this by keying on `X-Forwarded-For` as sent. A client can set
that header to any value and get a fresh bucket on every request.

### Trusted proxies (`TRUSTED_PROXIES`)

`TRUSTED_PROXIES` is a comma-separated list of IPs or CIDRs, empty by
default. Only from those peers is `X-Forwarded-For` read; from anyone else it
is ignored, so a direct caller cannot rotate it for a fresh bucket
(`tests/test_public_abuse_limits.py`).

1. List a proxy only if it **appends** the address it saw to
   `X-Forwarded-For` (nginx ingress, Render's edge, a load balancer). The
   web-form container does not qualify: Next.js only sets
   `X-Forwarded-For` when the header is missing (`??=` in `base-server.js`)
   and passes a client-sent value through unchanged, and the route handlers
   (`/api/tickets/[id]`, `/api/voice`) forward no client headers at all.
   Trusting it would let any browser pick its own bucket. So in compose
   `TRUSTED_PROXIES` stays empty and customers behind the web form still
   share one per-IP bucket; the global budget is what caps spend there.
2. The value depends on the deployment (ingress pod CIDR, Render's edge
   ranges); compose gives the web-form container a dynamic address, which is
   another reason not to set it there.
3. Keep the API port unreachable except through those proxies (k8s
   NetworkPolicy, no public port mapping), otherwise a client can bypass the
   proxy and send its own header.
4. Use Redis (above) so the per-client counter is shared across replicas.
5. Rate limiting is not a spend cap. `/webhooks/webform` and
   `/webhooks/voice/message` trigger LLM/STT calls. Distributed clients each
   get their own budget, so a global spend circuit breaker and a CAPTCHA /
   Turnstile on the form are still needed (AUDIT S8).

CAPTCHA / Turnstile on the public form is still not done (AUDIT S8).
