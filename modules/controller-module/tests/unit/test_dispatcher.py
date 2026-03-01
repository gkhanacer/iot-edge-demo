"""Tests for CommandDispatcher — direct method dispatch with retry logic.

CommandDispatcher.send() calls client.invoke_method() up to (max_retries + 1)
times. On exhaustion it raises RuntimeError("... failed after N attempts ...").

Mock setup:
    mock_client is a MagicMock whose invoke_method attribute is an AsyncMock.
    invoke_method must be AsyncMock (not a regular MagicMock) because send()
    awaits it — a plain Mock is not awaitable and would raise TypeError.

Fixture note: max_retries=1 limits each failure test to 2 total attempts.
The dispatcher sleeps RETRY_DELAY_S (2 s) between attempts; this delay is
NOT patched in these tests because the fixture uses max_retries=1, so the
retry path in test_retries_on_transient_failure incurs one real 2-second sleep.
If test speed becomes a concern, patch "src.dispatcher.asyncio.sleep".

Helper methods (set_solar_output, charge_battery, …) are thin wrappers around
send() that pre-build the method name and payload dict. Their tests verify the
payload mapping only — the underlying dispatch logic is covered by TestDispatcher.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.dispatcher import CommandDispatcher


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_client():
    # MagicMock creates a synchronous mock for the client object itself.
    # invoke_method is replaced with AsyncMock because it is awaited inside send().
    client = MagicMock()
    client.invoke_method = AsyncMock(return_value={"status": "ok"})
    return client


@pytest.fixture
def dispatcher(mock_client) -> CommandDispatcher:
    # max_retries=1 means 2 total attempts: one initial call + one retry.
    return CommandDispatcher(mock_client, max_retries=1)


# ── TestDispatcher ────────────────────────────────────────────────────────────

class TestDispatcher:
    @pytest.mark.asyncio
    async def test_send_invokes_method(self, dispatcher, mock_client) -> None:
        # Confirms that send() forwards all arguments to invoke_method with
        # the correct keyword names expected by the IoT Edge client interface.
        await dispatcher.send("solar-module", "start", {})
        mock_client.invoke_method.assert_called_once_with(
            target_module_id="solar-module",
            method_name="start",
            payload={},
            timeout_s=10,
        )

    @pytest.mark.asyncio
    async def test_set_solar_output_passes_payload(self, dispatcher, mock_client) -> None:
        # set_solar_output() must build {"target_kw": value} — the field name
        # expected by the solar module's SetOutputPayload schema.
        await dispatcher.set_solar_output("solar-module", target_kw=40.0)
        call_kwargs = mock_client.invoke_method.call_args.kwargs
        assert call_kwargs["payload"] == {"target_kw": 40.0}

    @pytest.mark.asyncio
    async def test_charge_battery_passes_payload(self, dispatcher, mock_client) -> None:
        # charge_battery() must build {"power_kw": value} — the field name
        # expected by the battery module's StartChargingPayload schema.
        await dispatcher.charge_battery("battery-module", power_kw=25.0)
        call_kwargs = mock_client.invoke_method.call_args.kwargs
        assert call_kwargs["payload"] == {"power_kw": 25.0}

    @pytest.mark.asyncio
    async def test_retries_on_transient_failure(self, dispatcher, mock_client) -> None:
        # side_effect as a list: first call raises, second returns the success dict.
        # With max_retries=1, the dispatcher must attempt exactly twice and succeed.
        mock_client.invoke_method = AsyncMock(
            side_effect=[Exception("transient"), {"status": "ok"}]
        )
        result = await dispatcher.send("solar-module", "start", {})
        assert mock_client.invoke_method.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self, dispatcher, mock_client) -> None:
        # When every attempt fails, send() must raise RuntimeError with a message
        # that includes "failed after" so callers and log monitoring can identify
        # exhausted retries without parsing the original exception type.
        mock_client.invoke_method = AsyncMock(side_effect=Exception("permanent failure"))
        with pytest.raises(RuntimeError, match="failed after"):
            await dispatcher.send("solar-module", "start", {})
