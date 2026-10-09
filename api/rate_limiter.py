"""Rate limiting configuration using slowapi with optional Redis backend."""

import ipaddress
import os
from collections.abc import Callable

import structlog
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = structlog.get_logger(__name__)

# --- Configurable limits (evaluated at import time after dotenv loads) ---
RATE_LIMIT_PER_MINUTE = os.getenv("RATE_LIMIT_PER_MINUTE", "100")
STRICT_RATE_LIMIT_PER_MINUTE = os.getenv("STRICT_RATE_LIMIT_PER_MINUTE", "10")

default_limits: list[str | Callable[..., str]] = [f"{RATE_LIMIT_PER_MINUTE}/minute"]
strict_limit = f"{STRICT_RATE_LIMIT_PER_MINUTE}/minute"


def _get_storage_uri() -> str:
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        logger.info("rate_limiter_redis_enabled")
        return redis_url
    logger.info("rate_limiter_memory_fallback")
    return "memory://"


def _trusted_proxies() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    raw = os.getenv("TRUSTED_PROXIES", "")
    return [ipaddress.ip_network(p.strip(), strict=False) for p in raw.split(",") if p.strip()]


def _is_trusted(ip: str, proxies: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in proxies)


def client_ip(request: Request) -> str:
    """The real client address for rate limiting (S8).

    X-Forwarded-For is only believed when the TCP peer is a proxy listed in
    TRUSTED_PROXIES (IPs or CIDRs); a direct caller could otherwise rotate it
    to get a fresh bucket per request. Walking the header from the right,
    the first address that is not a trusted proxy is the client.
    """
    peer = get_remote_address(request)
    proxies = _trusted_proxies()
    if not proxies or not _is_trusted(peer, proxies):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not _is_trusted(hop, proxies):
            return hop
    return hops[0] if hops else peer


def public_llm_budget() -> str:
    """Total public LLM-triggering submissions per hour, across all clients."""
    return f"{os.getenv('PUBLIC_LLM_BUDGET_PER_HOUR', '300')}/hour"


def _global_key(request: Request) -> str:
    return "all-clients"


limiter = Limiter(
    key_func=client_ip,
    default_limits=default_limits,
    storage_uri=_get_storage_uri(),
)

# One shared bucket for every public route that spends LLM calls: a spend
# circuit breaker that rotating IPs cannot get around (S8).
public_llm_limit = limiter.shared_limit(
    public_llm_budget,
    scope="public-llm",
    key_func=_global_key,
    override_defaults=False,
)


async def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    response = JSONResponse(
        status_code=429,
        content={
            "error": "RATE_LIMIT_EXCEEDED",
            "message": "Too many requests. Please slow down and try again.",
        },
    )
    retry_after = getattr(exc, "retry_after", 60)
    response.headers["Retry-After"] = str(int(retry_after))
    response.headers["X-RateLimit-Reset"] = str(int(retry_after))
    logger.warning(
        "rate_limit_exceeded",
        path=request.url.path,
        client_ip=client_ip(request),
        retry_after=retry_after,
    )
    return response
