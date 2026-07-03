"""Rate limiting configuration using slowapi with optional Redis backend."""

import os
import structlog
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = structlog.get_logger(__name__)

# --- Configurable limits (evaluated at import time after dotenv loads) ---
RATE_LIMIT_PER_MINUTE = os.getenv("RATE_LIMIT_PER_MINUTE", "100")
STRICT_RATE_LIMIT_PER_MINUTE = os.getenv("STRICT_RATE_LIMIT_PER_MINUTE", "10")

default_limits = [f"{RATE_LIMIT_PER_MINUTE}/minute"]
strict_limit = f"{STRICT_RATE_LIMIT_PER_MINUTE}/minute"


def _get_storage_uri() -> str:
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        logger.info("rate_limiter_redis_enabled")
        return redis_url
    logger.info("rate_limiter_memory_fallback")
    return "memory://"


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=default_limits,
    storage_uri=_get_storage_uri(),
)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
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
        client_ip=get_remote_address(request),
        retry_after=retry_after,
    )
    return response
