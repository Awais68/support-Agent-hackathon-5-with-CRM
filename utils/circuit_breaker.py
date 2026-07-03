"""Circuit breaker for external API resilience with env-based config and fallback support.

Implements a circuit breaker pattern with three states:
  CLOSED    - normal operation, calls pass through
  OPEN      - failures exceed threshold, calls are rejected fast with CircuitBreakerError
  HALF_OPEN - one probe call is allowed to test recovery

Configurable per-service via env vars:
  CB_{NAME}_FAILURE_THRESHOLD   default: 5
  CB_{NAME}_RECOVERY_TIMEOUT    default: 30 (seconds)
  CB_{NAME}_HALF_OPEN_MAX_CALLS default: 1

Usage:
    async with circuit_breaker("openai"):
        response = await openai.chat.completions.create(...)

    # With fallback when circuit is open:
    try:
        async with circuit_breaker("openai"):
            return await api_call()
    except CircuitBreakerError:
        return fallback_value

    # Or use the .call() helper:
    result = await circuit_breaker("openai").call(api_call, fallback=fallback_value)
"""

import asyncio
import os
import time
import functools
from enum import Enum
from typing import Optional, Callable, Any, Awaitable

import structlog

logger = structlog.get_logger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(Exception):
    """Raised when the circuit is OPEN and the request is rejected without calling the upstream service."""


class CircuitBreaker:
    """Async circuit breaker with env-based defaults, structured logging, and fallback support."""

    def __init__(
        self,
        name: str,
        failure_threshold: Optional[int] = None,
        recovery_timeout: Optional[float] = None,
        half_open_max_calls: Optional[int] = None,
    ):
        self.name = name

        env_prefix = f"CB_{name.upper().replace('-', '_')}"

        self.failure_threshold = (
            failure_threshold
            if failure_threshold is not None
            else int(os.getenv(f"{env_prefix}_FAILURE_THRESHOLD", "5"))
        )
        self.recovery_timeout = (
            recovery_timeout
            if recovery_timeout is not None
            else float(os.getenv(f"{env_prefix}_RECOVERY_TIMEOUT", "30.0"))
        )
        self.half_open_max_calls = (
            half_open_max_calls
            if half_open_max_calls is not None
            else int(os.getenv(f"{env_prefix}_HALF_OPEN_MAX_CALLS", "1"))
        )

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.half_open_calls = 0
        self._lock = asyncio.Lock()

        logger.info(
            "Circuit breaker initialized",
            breaker=self.name,
            state=self.state.value,
            failure_threshold=self.failure_threshold,
            recovery_timeout=self.recovery_timeout,
            half_open_max_calls=self.half_open_max_calls,
        )

    # ── async context manager ──────────────────────────────────────────

    async def __aenter__(self) -> "CircuitBreaker":
        await self._check_state()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> bool:
        if exc_type is CircuitBreakerError:
            return False
        if exc_type is not None:
            await self._record_failure()
        else:
            await self._record_success()
        return False

    # ── state checks ───────────────────────────────────────────────────

    async def _check_state(self) -> None:
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return

            if self.state == CircuitState.OPEN:
                elapsed = time.monotonic() - (self.last_failure_time or time.monotonic())
                if elapsed >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    logger.info(
                        "Circuit state → half-open",
                        breaker=self.name,
                        down_duration_seconds=round(elapsed, 1),
                        recovery_timeout=self.recovery_timeout,
                    )
                    return
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is OPEN. "
                    f"Retry in {self.recovery_timeout - elapsed:.0f}s"
                )

            if self.state == CircuitState.HALF_OPEN:
                self.half_open_calls += 1
                if self.half_open_calls > self.half_open_max_calls:
                    raise CircuitBreakerError(
                        f"Circuit breaker '{self.name}' is HALF_OPEN and at capacity "
                        f"({self.half_open_calls}/{self.half_open_max_calls})"
                    )

    async def _record_failure(self) -> None:
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            prev_state = self.state
            logger.warning(
                "Circuit failure recorded",
                breaker=self.name,
                state=prev_state.value,
                failure_count=self.failure_count,
                threshold=self.failure_threshold,
            )
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.error(
                    "Circuit state → open",
                    breaker=self.name,
                    failure_count=self.failure_count,
                    threshold=self.failure_threshold,
                    previous_state=prev_state.value,
                )

    async def _record_success(self) -> None:
        async with self._lock:
            prev_state = self.state
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                logger.info(
                    "Circuit state → closed (recovered)",
                    breaker=self.name,
                    previous_state=prev_state.value,
                )
            elif self.state == CircuitState.CLOSED:
                self.failure_count = 0

    # ── call helper with fallback ──────────────────────────────────────

    async def call(
        self,
        func: Callable[..., Awaitable[Any]],
        *args: Any,
        fallback: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Execute *func* under circuit protection.

        If the circuit is OPEN the call is rejected and *fallback* is
        returned instead (unless *fallback* is a callable, in which case
        it is invoked to produce the value).
        """
        try:
            async with self:
                return await func(*args, **kwargs)
        except CircuitBreakerError:
            if fallback is not None:
                if callable(fallback):
                    if asyncio.iscoroutinefunction(fallback):
                        return await fallback(*args, **kwargs)
                    return fallback(*args, **kwargs)
                return fallback
            raise


# ── shared registry ──────────────────────────────────────────────────────

_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    failure_threshold: Optional[int] = None,
    recovery_timeout: Optional[float] = None,
    half_open_max_calls: Optional[int] = None,
) -> CircuitBreaker:
    """Return (or create) the named singleton :class:`CircuitBreaker`.

    Thresholds are resolved from env vars ``CB_{NAME}_*`` when not
    given explicitly.  The first call wins – later calls with different
    explicit thresholds are ignored.
    """
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            half_open_max_calls=half_open_max_calls,
        )
    return _breakers[name]


# ── decorator ────────────────────────────────────────────────────────────

def circuit_breaker(
    name: str,
    failure_threshold: Optional[int] = None,
    recovery_timeout: Optional[float] = None,
    half_open_max_calls: Optional[int] = None,
) -> Callable:
    """Decorator: wrap an async function with a named circuit breaker.

    The underlying :class:`CircuitBreaker` is a singleton per *name*
    (see :func:`get_circuit_breaker`).
    """
    breaker = get_circuit_breaker(
        name=name,
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
        half_open_max_calls=half_open_max_calls,
    )

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with breaker:
                return await func(*args, **kwargs)
        return wrapper
    return decorator
