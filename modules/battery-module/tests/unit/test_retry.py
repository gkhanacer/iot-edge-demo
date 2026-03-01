"""Tests for the with_retry() backoff utility."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from iot_edge_base.retry import with_retry


class TestWithRetrySuccess:
    @pytest.mark.asyncio
    async def test_returns_on_first_success(self) -> None:
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
        attempts = []

        async def fn():
            attempts.append(1)
            if len(attempts) < 2:
                raise ConnectionError("transient")
            return "ok"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await with_retry(fn, base_delay_s=0.0, label="test")

        assert result == "ok"
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_succeeds_on_last_attempt(self) -> None:
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


class TestWithRetryExhaustion:
    @pytest.mark.asyncio
    async def test_raises_after_all_attempts(self) -> None:
        async def always_fails():
            raise TimeoutError("always down")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(TimeoutError, match="always down"):
                await with_retry(always_fails, max_attempts=3, base_delay_s=0.0, label="test")

    @pytest.mark.asyncio
    async def test_exact_attempt_count(self) -> None:
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


class TestWithRetryBackoff:
    @pytest.mark.asyncio
    async def test_sleep_called_between_attempts(self) -> None:
        async def fn():
            raise RuntimeError("x")

        with patch("iot_edge_base.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(RuntimeError):
                await with_retry(fn, max_attempts=3, base_delay_s=1.0, label="test")

        # Sleep is called between attempts — once fewer than max_attempts
        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    async def test_no_sleep_on_single_attempt(self) -> None:
        async def fn():
            raise RuntimeError("x")

        with patch("iot_edge_base.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(RuntimeError):
                await with_retry(fn, max_attempts=1, base_delay_s=1.0, label="test")

        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_delay_respects_max_delay_cap(self) -> None:
        """Jitter must never exceed max_delay_s."""
        sleep_calls = []

        async def always_fails():
            raise RuntimeError("x")

        async def fake_sleep(delay):
            sleep_calls.append(delay)

        with patch("iot_edge_base.retry.asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(RuntimeError):
                await with_retry(
                    always_fails,
                    max_attempts=5,
                    base_delay_s=100.0,
                    max_delay_s=10.0,
                    label="test",
                )

        assert all(d <= 10.0 for d in sleep_calls)
