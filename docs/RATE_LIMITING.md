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
| Exempt | — | — | `/health`, `/livez`, `/readyz`, `/metrics` |

The WebSocket is not rate limited. Over the limit the API answers
`429 {"error":"RATE_LIMIT_EXCEEDED"}` with `Retry-After`.

Limits apply per key, and the key is the TCP peer address
(`slowapi.util.get_remote_address`). There is no per-account, per-email or
global limit.

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

## Key: behind a proxy every user shares one bucket

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

### What a correct setup needs (not implemented; tracked as G2/S8)

1. Make the proxies append the real client address. The Next.js route
   handlers (`/api/tickets/[id]`, `/api/voice`) build a new request and
   forward no client headers today. Check that the `/webhooks` rewrite sets
   `X-Forwarded-For`, and make the ingress or edge in front add its own.
2. Trust that header only from known proxies. Run uvicorn with
   `--proxy-headers --forwarded-allow-ips=<web-form / ingress CIDRs>`. Never
   use `*` on a port that is reachable from the internet. Uvicorn then sets
   the client address from the right-most untrusted hop, and
   `get_remote_address` returns the real client.
3. Keep the API port unreachable except through those proxies (k8s
   NetworkPolicy, no public port mapping), otherwise a client can bypass the
   proxy and send its own header.
4. Use Redis (above) so the per-client counter is shared across replicas.
5. Rate limiting is not a spend cap. `/webhooks/webform` and
   `/webhooks/voice/message` trigger LLM/STT calls. Distributed clients each
   get their own budget, so a global spend circuit breaker and a CAPTCHA /
   Turnstile on the form are still needed (AUDIT S8).

Until 1–3 are done, treat the limits as a coarse global throttle per proxy.
