"""Async retry with exponential backoff + full jitter.

Full jitter strategy (AWS recommendation):
    delay = uniform(0, min(cap, base * 2^attempt))

Why full jitter?
- Prevents thundering herd: if N clients all fail at t=0, their retries
  are spread uniformly over [0, cap] instead of hitting simultaneously.
- Prevents synchronized reconnect storms after a brief outage.
- Retries remain idempotent from the caller's perspective.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog

logger = structlog.get_logger()

T = TypeVar("T")


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    label: str = "operation",
) -> T:
    """Call fn() up to max_attempts times, backing off between failures.

    Args:
        fn: Zero-argument callable that returns an awaitable.  Use a lambda
            or ``functools.partial`` to bind arguments:
            ``with_retry(lambda: client.send(...))``
        max_attempts: Total number of attempts (first call + retries).
        base_delay_s: Minimum backoff window (doubles each attempt).
        max_delay_s: Upper cap on the backoff window.
        label: Name logged on retry/failure for observability.

    Returns:
        The return value of fn() on the first successful attempt.

    Raises:
        The last exception once all attempts are exhausted.
    """
    last_exc: Exception = RuntimeError("unreachable")
    for attempt in range(max_attempts):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                break
            cap = min(max_delay_s, base_delay_s * (2**attempt))
            delay = random.uniform(0, cap)
            logger.warning(
                "Transient failure, retrying",
                label=label,
                attempt=attempt + 1,
                max_attempts=max_attempts,
                retry_after_s=round(delay, 3),
                error=str(exc),
            )
            await asyncio.sleep(delay)

    logger.error(
        "All retry attempts exhausted",
        label=label,
        max_attempts=max_attempts,
        error=str(last_exc),
    )
    raise last_exc
