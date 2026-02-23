from unittest.mock import AsyncMock, MagicMock

import pytest

from src.dispatcher import CommandDispatcher


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.invoke_method = AsyncMock(return_value={"status": "ok"})
    return client


@pytest.fixture
def dispatcher(mock_client) -> CommandDispatcher:
    return CommandDispatcher(mock_client, max_retries=1)


class TestDispatcher:
    @pytest.mark.asyncio
    async def test_send_invokes_method(self, dispatcher, mock_client) -> None:
        await dispatcher.send("solar-module", "start", {})
        mock_client.invoke_method.assert_called_once_with(
            target_module_id="solar-module",
            method_name="start",
            payload={},
            timeout_s=10,
        )

    @pytest.mark.asyncio
    async def test_set_solar_output_passes_payload(self, dispatcher, mock_client) -> None:
        await dispatcher.set_solar_output("solar-module", target_kw=40.0)
        call_kwargs = mock_client.invoke_method.call_args.kwargs
        assert call_kwargs["payload"] == {"target_kw": 40.0}

    @pytest.mark.asyncio
    async def test_charge_battery_passes_payload(self, dispatcher, mock_client) -> None:
        await dispatcher.charge_battery("battery-module", power_kw=25.0)
        call_kwargs = mock_client.invoke_method.call_args.kwargs
        assert call_kwargs["payload"] == {"power_kw": 25.0}

    @pytest.mark.asyncio
    async def test_retries_on_transient_failure(self, dispatcher, mock_client) -> None:
        mock_client.invoke_method = AsyncMock(
            side_effect=[Exception("transient"), {"status": "ok"}]
        )
        result = await dispatcher.send("solar-module", "start", {})
        assert mock_client.invoke_method.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self, dispatcher, mock_client) -> None:
        mock_client.invoke_method = AsyncMock(side_effect=Exception("permanent failure"))
        with pytest.raises(RuntimeError, match="failed after"):
            await dispatcher.send("solar-module", "start", {})
