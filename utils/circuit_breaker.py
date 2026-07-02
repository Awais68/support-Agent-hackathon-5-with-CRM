"""Circuit breaker for external API resilience.

Implements a circuit breaker pattern with three states:
  CLOSED  - normal operation, calls pass through
  OPEN    - failures exceed threshold, calls are rejected fast
  HALF_OPEN - one probe call is allowed to test recovery

Usage:
    breaker = CircuitBreaker("openai", failure_threshold=5, recovery_timeout=30)
    async with breaker:
        response = await openai.chat.completions.create(...)

Or via decorator:
    @circuit_breaker("kafka", failure_threshold=3)
    async def send_kafka(...)
"""

import asyncio
import time
import functools
from enum import Enum
from typing import Optional, Callable, Any

import structlog

logger = structlog.get_logger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(Exception):
    pass


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.half_open_calls = 0
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self._check_state()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            await self._record_failure()
        else:
            await self._record_success()
        return False

    async def _check_state(self):
        async with self._lock:
            if self.state == CircuitState.CLOSED:
                return

            if self.state == CircuitState.OPEN:
                if self.last_failure_time and (
                    time.monotonic() - self.last_failure_time >= self.recovery_timeout
                ):
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    logger.info("Circuit half-open", breaker=self.name)
                    return
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is OPEN. "
                    f"Retry after {self.recovery_timeout - (time.monotonic() - self.last_failure_time):.0f}s"
                )

            if self.state == CircuitState.HALF_OPEN:
                self.half_open_calls += 1
                if self.half_open_calls > self.half_open_max_calls:
                    raise CircuitBreakerError(
                        f"Circuit breaker '{self.name}' is HALF_OPEN and at capacity"
                    )
                return

    async def _record_failure(self):
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            logger.warning(
                "Circuit failure recorded",
                breaker=self.name,
                failure_count=self.failure_count,
                threshold=self.failure_threshold,
            )
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.error(
                    "Circuit opened",
                    breaker=self.name,
                    failure_count=self.failure_count,
                )

    async def _record_success(self):
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                logger.info(
                    "Circuit recovered to closed",
                    breaker=self.name,
                )
            elif self.state == CircuitState.CLOSED:
                self.failure_count = 0


def circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
):
    instances: dict[str, CircuitBreaker] = {}

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            if name not in instances:
                instances[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout,
                )
            breaker = instances[name]
            async with breaker:
                return await func(*args, **kwargs)
        return wrapper
    return decorator
