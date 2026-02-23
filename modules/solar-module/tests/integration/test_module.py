"""Integration tests for solar-module.

Tests the interaction between the inverter driver and the IoT client
using a mock client — no real IoT Hub or MQTT broker required.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from iot_edge_base.asset import AssetState
from iot_edge_base.client import DirectMethodRequest, DirectMethodResponse
from src.inverter import SolarInverter
from main import register_handlers


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.on_method = MagicMock()
    client.on_twin_update = MagicMock()
    client.send_message_to_output = AsyncMock()
    client.update_reported_properties = AsyncMock()
    return client


@pytest.fixture
def inverter():
    return SolarInverter(asset_id="solar-test", max_power_kw=100.0, startup_delay_s=0)


class TestCommandHandlers:
    @pytest.mark.asyncio
    async def test_start_command(self, mock_client, inverter: SolarInverter) -> None:
        await register_handlers(mock_client, inverter)
        handler = mock_client.on_method.call_args[0][0]

        request = DirectMethodRequest(request_id="r1", name="start", payload={})
        response = await handler(request)

        assert response.status == 200
        assert inverter.state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_stop_command(self, mock_client, inverter: SolarInverter) -> None:
        await inverter.start()
        await register_handlers(mock_client, inverter)
        handler = mock_client.on_method.call_args[0][0]

        request = DirectMethodRequest(request_id="r2", name="stop", payload={})
        response = await handler(request)

        assert response.status == 200
        assert inverter.state == AssetState.IDLE

    @pytest.mark.asyncio
    async def test_set_output_command(self, mock_client, inverter: SolarInverter) -> None:
        await inverter.start()
        await register_handlers(mock_client, inverter)
        handler = mock_client.on_method.call_args[0][0]

        request = DirectMethodRequest(request_id="r3", name="set_output", payload={"target_kw": 40.0})
        response = await handler(request)

        assert response.status == 200
        assert inverter._target_power_kw == 40.0

    @pytest.mark.asyncio
    async def test_unknown_command_returns_404(self, mock_client, inverter: SolarInverter) -> None:
        await register_handlers(mock_client, inverter)
        handler = mock_client.on_method.call_args[0][0]

        request = DirectMethodRequest(request_id="r4", name="fly_to_moon", payload={})
        response = await handler(request)

        assert response.status == 404

    @pytest.mark.asyncio
    async def test_set_output_when_idle_returns_409(self, mock_client, inverter: SolarInverter) -> None:
        await register_handlers(mock_client, inverter)
        handler = mock_client.on_method.call_args[0][0]

        request = DirectMethodRequest(request_id="r5", name="set_output", payload={"target_kw": 50.0})
        response = await handler(request)

        assert response.status == 409

    @pytest.mark.asyncio
    async def test_twin_update_changes_max_power(self, mock_client, inverter: SolarInverter) -> None:
        await register_handlers(mock_client, inverter)
        twin_handler = mock_client.on_twin_update.call_args[0][0]

        await twin_handler({"max_power_kw": 75.0})

        assert inverter.max_power_kw == 75.0
