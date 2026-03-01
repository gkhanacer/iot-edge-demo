import pytest

from iot_edge_base.asset import AssetState
from src.battery import BatteryMode, BatteryStorage


@pytest.fixture
def battery() -> BatteryStorage:
    return BatteryStorage(
        asset_id="battery-test",
        capacity_kwh=100.0,
        max_power_kw=50.0,
        initial_soc=0.5,
        startup_delay_s=0,
    )


@pytest.fixture
async def running_battery(battery: BatteryStorage) -> BatteryStorage:
    """Battery that has completed startup and is in RUNNING state."""
    await battery.start()
    return battery


class TestCharging:
    @pytest.mark.asyncio
    async def test_start_charging(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(30.0)
        assert running_battery.mode == BatteryMode.CHARGING

    @pytest.mark.asyncio
    async def test_charging_increases_soc(self, running_battery: BatteryStorage) -> None:
        initial_soc = running_battery._soc
        await running_battery.start_charging(50.0)
        running_battery.tick(elapsed_s=3600)
        assert running_battery._soc > initial_soc

    @pytest.mark.asyncio
    async def test_charging_clamps_to_max_power(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(999.0)
        assert running_battery._power_kw <= running_battery.max_power_kw

    @pytest.mark.asyncio
    async def test_charging_stops_at_max_soc(self, running_battery: BatteryStorage) -> None:
        running_battery._soc = 0.94
        await running_battery.start_charging(50.0)
        running_battery.tick(elapsed_s=3600)
        assert running_battery.mode == BatteryMode.IDLE

    @pytest.mark.asyncio
    async def test_cannot_charge_when_full(self, running_battery: BatteryStorage) -> None:
        running_battery._soc = 0.95
        with pytest.raises(RuntimeError, match="maximum charge"):
            await running_battery.start_charging(50.0)

    @pytest.mark.asyncio
    async def test_cannot_charge_when_not_running(self, battery: BatteryStorage) -> None:
        with pytest.raises(RuntimeError, match="call start\\(\\) first"):
            await battery.start_charging(50.0)


class TestDischarging:
    @pytest.mark.asyncio
    async def test_start_discharging(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_discharging(30.0)
        assert running_battery.mode == BatteryMode.DISCHARGING

    @pytest.mark.asyncio
    async def test_discharging_decreases_soc(self, running_battery: BatteryStorage) -> None:
        initial_soc = running_battery._soc
        await running_battery.start_discharging(50.0)
        running_battery.tick(elapsed_s=3600)
        assert running_battery._soc < initial_soc

    @pytest.mark.asyncio
    async def test_power_is_negative_when_discharging(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_discharging(30.0)
        assert running_battery._power_kw < 0.0

    @pytest.mark.asyncio
    async def test_cannot_discharge_when_empty(self, running_battery: BatteryStorage) -> None:
        running_battery._soc = 0.05
        with pytest.raises(RuntimeError, match="minimum charge"):
            await running_battery.start_discharging(50.0)

    @pytest.mark.asyncio
    async def test_cannot_discharge_when_not_running(self, battery: BatteryStorage) -> None:
        with pytest.raises(RuntimeError, match="call start\\(\\) first"):
            await battery.start_discharging(50.0)


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_sets_idle(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(50.0)
        await running_battery.stop()
        assert running_battery.state == AssetState.IDLE
        assert running_battery.mode == BatteryMode.IDLE
        assert running_battery._power_kw == 0.0


class TestTelemetry:
    def test_telemetry_contains_required_fields(self, battery: BatteryStorage) -> None:
        data = battery.get_telemetry().to_dict()
        required = {"asset_id", "state", "mode", "state_of_charge", "power_kw", "energy_stored_kwh", "timestamp", "message_id"}
        assert required.issubset(data.keys())

    def test_energy_stored_equals_soc_times_capacity(self, battery: BatteryStorage) -> None:
        t = battery.get_telemetry()
        assert t.energy_stored_kwh == pytest.approx(battery._soc * battery.capacity_kwh, abs=0.1)

    @pytest.mark.asyncio
    async def test_temperature_rises_under_load(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(50.0)
        running_battery.tick(elapsed_s=10)
        t = running_battery.get_telemetry()
        assert t.temperature_c > 25.0
