import pytest

from iot_edge_base.asset import AssetState
from src.boiler import Boiler


@pytest.fixture
def boiler() -> Boiler:
    return Boiler(
        asset_id="boiler-test",
        max_power_kw=200.0,
        default_target_c=80.0,
        startup_delay_s=0,
    )


class TestStateMachine:
    @pytest.mark.asyncio
    async def test_start(self, boiler: Boiler) -> None:
        await boiler.start()
        assert boiler.state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_stop(self, boiler: Boiler) -> None:
        await boiler.start()
        await boiler.stop()
        assert boiler.state == AssetState.IDLE


class TestTemperatureControl:
    @pytest.mark.asyncio
    async def test_set_temperature_while_running(self, boiler: Boiler) -> None:
        await boiler.start()
        await boiler.set_temperature(90.0)
        assert boiler._target_temp_c == 90.0

    @pytest.mark.asyncio
    async def test_set_temperature_rejects_when_idle(self, boiler: Boiler) -> None:
        with pytest.raises(RuntimeError):
            await boiler.set_temperature(80.0)

    @pytest.mark.asyncio
    async def test_set_temperature_rejects_out_of_range(self, boiler: Boiler) -> None:
        await boiler.start()
        with pytest.raises(ValueError):
            await boiler.set_temperature(200.0)

    @pytest.mark.asyncio
    async def test_temperature_rises_when_heating(self, boiler: Boiler) -> None:
        await boiler.start()
        initial_temp = boiler._current_temp_c
        boiler.tick(elapsed_s=60)
        assert boiler._current_temp_c > initial_temp

    @pytest.mark.asyncio
    async def test_temperature_falls_when_stopped(self, boiler: Boiler) -> None:
        boiler._current_temp_c = 80.0
        boiler.tick(elapsed_s=60)
        assert boiler._current_temp_c < 80.0

    @pytest.mark.asyncio
    async def test_power_is_zero_when_idle(self, boiler: Boiler) -> None:
        boiler.tick(elapsed_s=10)
        assert boiler._power_kw == 0.0


class TestTelemetry:
    def test_telemetry_contains_required_fields(self, boiler: Boiler) -> None:
        data = boiler.get_telemetry().to_dict()
        required = {"asset_id", "state", "current_temperature_c", "target_temperature_c", "power_kw", "timestamp"}
        assert required.issubset(data.keys())

    @pytest.mark.asyncio
    async def test_efficiency_nonzero_when_running_and_heating(self, boiler: Boiler) -> None:
        await boiler.start()
        boiler._power_kw = 100.0
        t = boiler.get_telemetry()
        assert t.efficiency > 0.0
