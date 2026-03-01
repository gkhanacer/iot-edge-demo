import uuid

import pytest
from pydantic import ValidationError

from iot_edge_base.asset import AssetState
from src.battery import (
    CHARGE_EFFICIENCY,
    DISCHARGE_EFFICIENCY,
    SOC_MAX,
    SOC_MIN,
    BatteryMode,
    BatteryStorage,
)
from src.schemas import StartChargingPayload, StartDischargingPayload


# ── Fixtures ────────────────────────────────────────────────────────────────

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


# ── TestLifecycle ────────────────────────────────────────────────────────────

class TestLifecycle:
    """State machine: IDLE → STARTING → RUNNING → STOPPING → IDLE | FAULT."""

    def test_initial_state_is_idle(self, battery: BatteryStorage) -> None:
        assert battery.state == AssetState.IDLE
        assert battery.mode == BatteryMode.IDLE

    @pytest.mark.asyncio
    async def test_start_transitions_to_running(self, battery: BatteryStorage) -> None:
        await battery.start()
        assert battery.state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, running_battery: BatteryStorage) -> None:
        """Calling start() when already RUNNING must be a no-op, not raise."""
        await running_battery.start()
        assert running_battery.state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_stop_transitions_to_idle(self, running_battery: BatteryStorage) -> None:
        await running_battery.stop()
        assert running_battery.state == AssetState.IDLE

    @pytest.mark.asyncio
    async def test_stop_from_idle_is_noop(self, battery: BatteryStorage) -> None:
        """stop() when already IDLE must not raise."""
        await battery.stop()
        assert battery.state == AssetState.IDLE

    @pytest.mark.asyncio
    async def test_fault_sets_fault_state(self, running_battery: BatteryStorage) -> None:
        await running_battery.fault("OVERCURRENT")
        assert running_battery.state == AssetState.FAULT

    @pytest.mark.asyncio
    async def test_fault_stores_fault_code(self, running_battery: BatteryStorage) -> None:
        await running_battery.fault("OVERCURRENT")
        assert running_battery.fault_code == "OVERCURRENT"

    @pytest.mark.asyncio
    async def test_fault_clears_power_and_mode(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(50.0)
        await running_battery.fault("OVERTEMP")
        assert running_battery._power_kw == 0.0
        assert running_battery.mode == BatteryMode.IDLE

    @pytest.mark.asyncio
    async def test_reset_from_fault_returns_to_idle(self, running_battery: BatteryStorage) -> None:
        await running_battery.fault("OVERCURRENT")
        await running_battery.reset()
        assert running_battery.state == AssetState.IDLE
        assert running_battery.fault_code is None

    @pytest.mark.asyncio
    async def test_reset_when_not_faulted_is_noop(self, running_battery: BatteryStorage) -> None:
        """reset() while RUNNING must not change state."""
        await running_battery.reset()
        assert running_battery.state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_cannot_charge_in_fault_state(self, running_battery: BatteryStorage) -> None:
        await running_battery.fault("OVERCURRENT")
        with pytest.raises(RuntimeError, match="call start\\(\\) first"):
            await running_battery.start_charging(50.0)

    @pytest.mark.asyncio
    async def test_cannot_discharge_in_fault_state(self, running_battery: BatteryStorage) -> None:
        await running_battery.fault("OVERCURRENT")
        with pytest.raises(RuntimeError, match="call start\\(\\) first"):
            await running_battery.start_discharging(50.0)


# ── TestCharging ─────────────────────────────────────────────────────────────

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
    async def test_charging_power_is_zero_after_auto_stop(self, running_battery: BatteryStorage) -> None:
        running_battery._soc = 0.94
        await running_battery.start_charging(50.0)
        running_battery.tick(elapsed_s=3600)
        assert running_battery._power_kw == 0.0

    @pytest.mark.asyncio
    async def test_cannot_charge_when_full(self, running_battery: BatteryStorage) -> None:
        running_battery._soc = SOC_MAX
        with pytest.raises(RuntimeError, match="maximum charge"):
            await running_battery.start_charging(50.0)

    @pytest.mark.asyncio
    async def test_cannot_charge_when_not_running(self, battery: BatteryStorage) -> None:
        with pytest.raises(RuntimeError, match="call start\\(\\) first"):
            await battery.start_charging(50.0)


# ── TestDischarging ──────────────────────────────────────────────────────────

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
    async def test_discharging_clamps_to_max_power(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_discharging(999.0)
        assert abs(running_battery._power_kw) <= running_battery.max_power_kw

    @pytest.mark.asyncio
    async def test_discharging_auto_stops_at_soc_min(self, running_battery: BatteryStorage) -> None:
        running_battery._soc = 0.06
        await running_battery.start_discharging(50.0)
        running_battery.tick(elapsed_s=3600)
        assert running_battery.mode == BatteryMode.IDLE
        assert running_battery._power_kw == 0.0

    @pytest.mark.asyncio
    async def test_cannot_discharge_when_empty(self, running_battery: BatteryStorage) -> None:
        running_battery._soc = SOC_MIN
        with pytest.raises(RuntimeError, match="minimum charge"):
            await running_battery.start_discharging(50.0)

    @pytest.mark.asyncio
    async def test_cannot_discharge_when_not_running(self, battery: BatteryStorage) -> None:
        with pytest.raises(RuntimeError, match="call start\\(\\) first"):
            await battery.start_discharging(50.0)


# ── TestSetIdle ──────────────────────────────────────────────────────────────

class TestSetIdle:
    @pytest.mark.asyncio
    async def test_set_idle_from_charging(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(50.0)
        await running_battery.set_idle()
        assert running_battery.mode == BatteryMode.IDLE
        assert running_battery._power_kw == 0.0

    @pytest.mark.asyncio
    async def test_set_idle_from_discharging(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_discharging(50.0)
        await running_battery.set_idle()
        assert running_battery.mode == BatteryMode.IDLE
        assert running_battery._power_kw == 0.0

    @pytest.mark.asyncio
    async def test_set_idle_preserves_running_state(self, running_battery: BatteryStorage) -> None:
        """set_idle() must not affect the operational lifecycle state."""
        await running_battery.start_charging(50.0)
        await running_battery.set_idle()
        assert running_battery.state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_can_charge_again_after_set_idle(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_discharging(50.0)
        await running_battery.set_idle()
        await running_battery.start_charging(30.0)
        assert running_battery.mode == BatteryMode.CHARGING


# ── TestStop ─────────────────────────────────────────────────────────────────

class TestStop:
    @pytest.mark.asyncio
    async def test_stop_sets_idle(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(50.0)
        await running_battery.stop()
        assert running_battery.state == AssetState.IDLE
        assert running_battery.mode == BatteryMode.IDLE
        assert running_battery._power_kw == 0.0

    @pytest.mark.asyncio
    async def test_stop_while_discharging_clears_power(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_discharging(30.0)
        await running_battery.stop()
        assert running_battery._power_kw == 0.0
        assert running_battery.mode == BatteryMode.IDLE

    @pytest.mark.asyncio
    async def test_can_restart_after_stop(self, running_battery: BatteryStorage) -> None:
        await running_battery.stop()
        await running_battery.start()
        assert running_battery.state == AssetState.RUNNING


# ── TestSoCSimulation ────────────────────────────────────────────────────────

class TestSoCSimulation:
    """Verify SoC physics formulae match the implementation."""

    @pytest.mark.asyncio
    async def test_charging_soc_formula_accuracy(self, running_battery: BatteryStorage) -> None:
        # 10 kW charging for 30 minutes on a 100 kWh battery
        power_kw = 10.0
        elapsed_s = 1800
        initial_soc = running_battery._soc  # 0.5
        await running_battery.start_charging(power_kw)
        running_battery.tick(elapsed_s=elapsed_s)

        delta_kwh = power_kw * CHARGE_EFFICIENCY * elapsed_s / 3600.0
        expected_soc = initial_soc + delta_kwh / running_battery.capacity_kwh
        assert running_battery._soc == pytest.approx(expected_soc, abs=1e-6)

    @pytest.mark.asyncio
    async def test_discharging_soc_formula_accuracy(self, running_battery: BatteryStorage) -> None:
        # 10 kW discharging for 30 minutes on a 100 kWh battery
        power_kw = 10.0
        elapsed_s = 1800
        initial_soc = running_battery._soc  # 0.5
        await running_battery.start_discharging(power_kw)
        running_battery.tick(elapsed_s=elapsed_s)

        delta_kwh = power_kw / DISCHARGE_EFFICIENCY * elapsed_s / 3600.0
        expected_soc = initial_soc - delta_kwh / running_battery.capacity_kwh
        assert running_battery._soc == pytest.approx(expected_soc, abs=1e-6)

    @pytest.mark.asyncio
    async def test_soc_clamped_at_soc_max_during_charging(self, running_battery: BatteryStorage) -> None:
        running_battery._soc = 0.90
        await running_battery.start_charging(50.0)
        running_battery.tick(elapsed_s=7200)  # 2 hours — enough to overflow
        assert running_battery._soc <= SOC_MAX

    @pytest.mark.asyncio
    async def test_soc_clamped_at_soc_min_during_discharging(self, running_battery: BatteryStorage) -> None:
        running_battery._soc = 0.10
        await running_battery.start_discharging(50.0)
        running_battery.tick(elapsed_s=7200)  # 2 hours — enough to drain
        assert running_battery._soc >= SOC_MIN

    @pytest.mark.asyncio
    async def test_soc_unchanged_when_idle(self, running_battery: BatteryStorage) -> None:
        initial_soc = running_battery._soc
        running_battery.tick(elapsed_s=3600)
        assert running_battery._soc == initial_soc


# ── TestTemperature ──────────────────────────────────────────────────────────

class TestTemperature:
    @pytest.mark.asyncio
    async def test_temperature_at_idle_stays_at_25c(self, running_battery: BatteryStorage) -> None:
        running_battery.tick(elapsed_s=600)
        assert running_battery._temperature_c == pytest.approx(25.0)

    @pytest.mark.asyncio
    async def test_temperature_model_at_charging_load(self, running_battery: BatteryStorage) -> None:
        power_kw = 40.0
        await running_battery.start_charging(power_kw)
        running_battery.tick(elapsed_s=1)
        expected_temp = 25.0 + 0.05 * power_kw
        assert running_battery._temperature_c == pytest.approx(expected_temp)

    @pytest.mark.asyncio
    async def test_temperature_drops_after_set_idle(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(50.0)
        running_battery.tick(elapsed_s=1)
        hot_temp = running_battery._temperature_c
        await running_battery.set_idle()
        running_battery.tick(elapsed_s=1)
        assert running_battery._temperature_c < hot_temp

    @pytest.mark.asyncio
    async def test_temperature_rises_under_load(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(50.0)
        running_battery.tick(elapsed_s=10)
        t = running_battery.get_telemetry()
        assert t.temperature_c > 25.0


# ── TestTelemetry ────────────────────────────────────────────────────────────

class TestTelemetry:
    def test_telemetry_contains_required_fields(self, battery: BatteryStorage) -> None:
        data = battery.get_telemetry().to_dict()
        required = {"asset_id", "state", "mode", "state_of_charge", "power_kw", "energy_stored_kwh", "timestamp", "message_id"}
        assert required.issubset(data.keys())

    def test_energy_stored_equals_soc_times_capacity(self, battery: BatteryStorage) -> None:
        t = battery.get_telemetry()
        assert t.energy_stored_kwh == pytest.approx(battery._soc * battery.capacity_kwh, abs=0.1)

    def test_message_id_is_valid_uuid(self, battery: BatteryStorage) -> None:
        t = battery.get_telemetry()
        parsed = uuid.UUID(t.message_id, version=4)
        assert str(parsed) == t.message_id

    def test_message_id_is_unique_per_call(self, battery: BatteryStorage) -> None:
        ids = {battery.get_telemetry().message_id for _ in range(5)}
        assert len(ids) == 5

    def test_telemetry_asset_id_matches(self, battery: BatteryStorage) -> None:
        assert battery.get_telemetry().asset_id == "battery-test"

    def test_telemetry_capacity_matches_config(self, battery: BatteryStorage) -> None:
        assert battery.get_telemetry().capacity_kwh == battery.capacity_kwh

    @pytest.mark.asyncio
    async def test_telemetry_state_reflects_running(self, running_battery: BatteryStorage) -> None:
        assert running_battery.get_telemetry().state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_telemetry_mode_reflects_charging(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(30.0)
        assert running_battery.get_telemetry().mode == BatteryMode.CHARGING

    @pytest.mark.asyncio
    async def test_telemetry_mode_reflects_discharging(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_discharging(30.0)
        assert running_battery.get_telemetry().mode == BatteryMode.DISCHARGING

    @pytest.mark.asyncio
    async def test_telemetry_power_is_positive_when_charging(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(30.0)
        assert running_battery.get_telemetry().power_kw > 0.0

    @pytest.mark.asyncio
    async def test_telemetry_power_is_negative_when_discharging(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_discharging(30.0)
        assert running_battery.get_telemetry().power_kw < 0.0

    @pytest.mark.asyncio
    async def test_telemetry_power_is_zero_when_idle(self, running_battery: BatteryStorage) -> None:
        assert running_battery.get_telemetry().power_kw == 0.0

    @pytest.mark.asyncio
    async def test_temperature_rises_under_load(self, running_battery: BatteryStorage) -> None:
        await running_battery.start_charging(50.0)
        running_battery.tick(elapsed_s=10)
        t = running_battery.get_telemetry()
        assert t.temperature_c > 25.0


# ── TestSchemas ──────────────────────────────────────────────────────────────

class TestSchemas:
    """Boundary validation — Pydantic rejects bad payloads before the asset driver runs."""

    # Good weather — StartChargingPayload
    def test_charging_payload_with_power_kw(self) -> None:
        p = StartChargingPayload.model_validate({"power_kw": 50.0})
        assert p.power_kw == 50.0

    def test_charging_payload_without_power_kw_defaults_to_none(self) -> None:
        p = StartChargingPayload.model_validate({})
        assert p.power_kw is None

    def test_charging_payload_with_none_explicit(self) -> None:
        p = StartChargingPayload.model_validate({"power_kw": None})
        assert p.power_kw is None

    # Good weather — StartDischargingPayload
    def test_discharging_payload_with_power_kw(self) -> None:
        p = StartDischargingPayload.model_validate({"power_kw": 25.0})
        assert p.power_kw == 25.0

    def test_discharging_payload_without_power_kw_defaults_to_none(self) -> None:
        p = StartDischargingPayload.model_validate({})
        assert p.power_kw is None

    # Bad weather — StartChargingPayload
    def test_negative_power_kw_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StartChargingPayload.model_validate({"power_kw": -10.0})

    def test_zero_power_kw_rejected(self) -> None:
        """power_kw must be strictly > 0 (gt=0.0)."""
        with pytest.raises(ValidationError):
            StartChargingPayload.model_validate({"power_kw": 0.0})

    def test_string_power_kw_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StartChargingPayload.model_validate({"power_kw": "fast"})

    # Bad weather — StartDischargingPayload
    def test_discharging_negative_power_kw_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StartDischargingPayload.model_validate({"power_kw": -5.0})

    def test_discharging_zero_power_kw_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StartDischargingPayload.model_validate({"power_kw": 0.0})
