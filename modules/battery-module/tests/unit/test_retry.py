"""Tests for the with_retry() exponential backoff utility.

with_retry() calls an async function up to max_attempts times.
Between failures it sleeps for a random duration drawn from
[0, min(max_delay_s, base_delay_s * 2^attempt)] — "full jitter" strategy.
On success it returns immediately; on exhaustion it re-raises the last exception.

Patching strategy
-----------------
asyncio.sleep is patched at its *import site* inside the retry module:
    patch("iot_edge_base.retry.asyncio.sleep")
This prevents real delays during tests while still letting us assert
how many times sleep was called and with what arguments.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from iot_edge_base.retry import with_retry


# ── TestWithRetrySuccess ─────────────────────────────────────────────────────
# Covers the happy paths: fn() completes without ever raising.

class TestWithRetrySuccess:
    @pytest.mark.asyncio
    async def test_returns_on_first_success(self) -> None:
        # When fn() succeeds on the very first call, the return value must be
        # propagated to the caller and the function must not be called again.
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await with_retry(fn, label="test")
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_succeeds_on_second_attempt(self) -> None:
        # Simulates a transient failure on attempt 1 followed by a successful
        # attempt 2. with_retry() must return the result and not propagate the
        # first exception.
        attempts = []

        async def fn():
            attempts.append(1)
            if len(attempts) < 2:
                raise ConnectionError("transient")
            return "ok"

        # Patch asyncio.sleep so the test does not wait for the backoff delay.
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await with_retry(fn, base_delay_s=0.0, label="test")

        assert result == "ok"
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_succeeds_on_last_attempt(self) -> None:
        # Verifies that recovery on the final allowed attempt is handled
        # correctly — result returned, no exception raised.
        attempts = []

        async def fn():
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("transient")
            return "done"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await with_retry(fn, max_attempts=3, base_delay_s=0.0, label="test")

        assert result == "done"
        assert len(attempts) == 3


# ── TestWithRetryExhaustion ──────────────────────────────────────────────────
# Covers the failure path: fn() keeps raising until all attempts are used up.

class TestWithRetryExhaustion:
    @pytest.mark.asyncio
    async def test_raises_after_all_attempts(self) -> None:
        # Once every attempt is exhausted, with_retry() must propagate the
        # exception rather than swallowing it silently.
        async def always_fails():
            raise TimeoutError("always down")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(TimeoutError, match="always down"):
                await with_retry(always_fails, max_attempts=3, base_delay_s=0.0, label="test")

    @pytest.mark.asyncio
    async def test_exact_attempt_count(self) -> None:
        # with_retry() must call fn() exactly max_attempts times — no more,
        # no fewer — before giving up.
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("boom")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError):
                await with_retry(fn, max_attempts=4, base_delay_s=0.0, label="test")

        assert call_count == 4

    @pytest.mark.asyncio
    async def test_re_raises_last_exception_type(self) -> None:
        # When different exceptions are raised across attempts, with_retry()
        # must re-raise the *last* one (not the first). This preserves the
        # most recent failure context for the caller's error handling.
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("first")
            raise ValueError("last")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ValueError, match="last"):
                await with_retry(fn, max_attempts=2, base_delay_s=0.0, label="test")


# ── TestWithRetryBackoff ─────────────────────────────────────────────────────
# Covers the sleep / backoff behaviour between attempts.

class TestWithRetryBackoff:
    @pytest.mark.asyncio
    async def test_sleep_called_between_attempts(self) -> None:
        # sleep() must be called exactly (max_attempts - 1) times:
        # once after each failed attempt except the last, because there is
        # nothing to wait for after the final attempt.
        async def fn():
            raise RuntimeError("x")

        # Patch at the retry module's import site so the mock intercepts the
        # actual call inside with_retry(), not a different binding of sleep.
        with patch("iot_edge_base.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(RuntimeError):
                await with_retry(fn, max_attempts=3, base_delay_s=1.0, label="test")

        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    async def test_no_sleep_on_single_attempt(self) -> None:
        # When max_attempts=1 there are no retries, so sleep must never be
        # called — the failure is raised immediately on the first exception.
        async def fn():
            raise RuntimeError("x")

        with patch("iot_edge_base.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(RuntimeError):
                await with_retry(fn, max_attempts=1, base_delay_s=1.0, label="test")

        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_delay_respects_max_delay_cap(self) -> None:
        # Full jitter formula: delay = uniform(0, min(max_delay_s, base * 2^n))
        # With base_delay_s=100 the uncapped window would be 100, 200, 400 …
        # but max_delay_s=10 must clamp every sleep call to at most 10 seconds.
        # Because jitter is random we cannot assert the exact value, only the
        # upper bound.
        sleep_calls = []

        async def always_fails():
            raise RuntimeError("x")

        async def fake_sleep(delay):
            # Collect every delay value instead of discarding it.
            sleep_calls.append(delay)

        with patch("iot_edge_base.retry.asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(RuntimeError):
                await with_retry(
                    always_fails,
                    max_attempts=5,
                    base_delay_s=100.0,   # intentionally large to stress the cap
                    max_delay_s=10.0,
                    label="test",
                )

        assert all(d <= 10.0 for d in sleep_calls)
