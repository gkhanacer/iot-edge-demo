import asyncio

import pytest

from iot_edge_base.asset import AssetState
from src.inverter import SolarInverter


@pytest.fixture
def inverter() -> SolarInverter:
    return SolarInverter(asset_id="solar-test", max_power_kw=100.0, startup_delay_s=0)


class TestStateMachine:
    def test_initial_state_is_idle(self, inverter: SolarInverter) -> None:
        assert inverter.state == AssetState.IDLE

    @pytest.mark.asyncio
    async def test_start_transitions_to_running(self, inverter: SolarInverter) -> None:
        await inverter.start()
        assert inverter.state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_stop_returns_to_idle(self, inverter: SolarInverter) -> None:
        await inverter.start()
        await inverter.stop()
        assert inverter.state == AssetState.IDLE

    @pytest.mark.asyncio
    async def test_start_when_already_running_is_noop(self, inverter: SolarInverter) -> None:
        await inverter.start()
        await inverter.start()  # should not raise
        assert inverter.state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_fault_transitions_to_fault_state(self, inverter: SolarInverter) -> None:
        await inverter.start()
        await inverter.fault("OVERCURRENT")
        assert inverter.state == AssetState.FAULT
        assert inverter.fault_code == "OVERCURRENT"

    @pytest.mark.asyncio
    async def test_reset_from_fault_returns_to_idle(self, inverter: SolarInverter) -> None:
        await inverter.fault("OVERCURRENT")
        await inverter.reset()
        assert inverter.state == AssetState.IDLE
        assert inverter.fault_code is None

    @pytest.mark.asyncio
    async def test_reset_when_not_faulted_is_noop(self, inverter: SolarInverter) -> None:
        await inverter.reset()  # should not raise
        assert inverter.state == AssetState.IDLE


class TestSetOutput:
    @pytest.mark.asyncio
    async def test_set_output_while_running(self, inverter: SolarInverter) -> None:
        await inverter.start()
        await inverter.set_output(50.0)
        telemetry = inverter.get_telemetry(irradiance_w_m2=1000.0)
        assert telemetry.power_output_kw == pytest.approx(50.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_set_output_clamps_to_max(self, inverter: SolarInverter) -> None:
        await inverter.start()
        await inverter.set_output(999.0)
        assert inverter._target_power_kw == 100.0

    @pytest.mark.asyncio
    async def test_set_output_clamps_to_zero(self, inverter: SolarInverter) -> None:
        await inverter.start()
        await inverter.set_output(-10.0)
        assert inverter._target_power_kw == 0.0

    @pytest.mark.asyncio
    async def test_set_output_raises_when_not_running(self, inverter: SolarInverter) -> None:
        with pytest.raises(RuntimeError):
            await inverter.set_output(50.0)


class TestTelemetry:
    @pytest.mark.asyncio
    async def test_power_is_zero_when_idle(self, inverter: SolarInverter) -> None:
        telemetry = inverter.get_telemetry(irradiance_w_m2=1000.0)
        assert telemetry.power_output_kw == 0.0

    @pytest.mark.asyncio
    async def test_power_is_zero_when_irradiance_is_zero(self, inverter: SolarInverter) -> None:
        await inverter.start()
        telemetry = inverter.get_telemetry(irradiance_w_m2=0.0)
        assert telemetry.power_output_kw == 0.0

    @pytest.mark.asyncio
    async def test_full_irradiance_produces_near_max_power(self, inverter: SolarInverter) -> None:
        await inverter.start()
        telemetry = inverter.get_telemetry(irradiance_w_m2=1000.0)
        # At full irradiance and STC, output should be close to max
        assert telemetry.power_output_kw > 90.0

    @pytest.mark.asyncio
    async def test_telemetry_includes_required_fields(self, inverter: SolarInverter) -> None:
        await inverter.start()
        data = inverter.get_telemetry(irradiance_w_m2=500.0).to_dict()
        required_fields = {"asset_id", "asset_type", "state", "power_output_kw", "timestamp"}
        assert required_fields.issubset(data.keys())

    @pytest.mark.asyncio
    async def test_temperature_rises_with_irradiance(self, inverter: SolarInverter) -> None:
        await inverter.start()
        telemetry = inverter.get_telemetry(irradiance_w_m2=1000.0)
        assert telemetry.temperature_c > 25.0

    @pytest.mark.asyncio
    async def test_power_is_zero_after_fault(self, inverter: SolarInverter) -> None:
        await inverter.start()
        await inverter.fault("TEST_FAULT")
        telemetry = inverter.get_telemetry(irradiance_w_m2=1000.0)
        assert telemetry.power_output_kw == 0.0
