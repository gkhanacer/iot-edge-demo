"""Tests for SolarInverter — state machine and physics-based power output.

SolarInverter extends BaseAsset with the lifecycle:
    IDLE → STARTING → RUNNING → STOPPING → IDLE | FAULT

Power output is calculated from irradiance using a temperature-derated model:

    panel_temp   = 25 + TEMP_RISE_FACTOR * irradiance_w_m2
    efficiency   = REFERENCE_EFFICIENCY * (1 - TEMP_COEFFICIENT * (panel_temp - 25))
    max_possible = (irradiance / 1000) * max_power_kw * (efficiency / REFERENCE_EFFICIENCY)
    actual_power = min(_target_power_kw, max_possible)

At full irradiance (1000 W/m²) output approaches but does not reach max_power_kw
because panel heating above STC (25°C) reduces efficiency via TEMP_COEFFICIENT.

Fixture note: startup_delay_s=0 avoids asyncio.sleep in start(), keeping tests fast.
"""

import asyncio

import pytest

from iot_edge_base.asset import AssetState
from src.inverter import SolarInverter


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def inverter() -> SolarInverter:
    # startup_delay_s=0 so start() completes immediately without real sleeping.
    return SolarInverter(asset_id="solar-test", max_power_kw=100.0, startup_delay_s=0)


# ── TestStateMachine ──────────────────────────────────────────────────────────
# Verifies the IDLE → RUNNING → IDLE | FAULT transitions inherited from BaseAsset.
# SolarInverter provides _on_start / _on_stop / _on_fault hooks; these tests
# confirm the hooks don't corrupt the parent state machine contract.

class TestStateMachine:
    def test_initial_state_is_idle(self, inverter: SolarInverter) -> None:
        # A freshly constructed inverter must start in IDLE — not RUNNING —
        # so callers must explicitly start() before it outputs power.
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
        # BaseAsset silently ignores a second start() in RUNNING state.
        # This prevents accidental double-start from corrupting internal fields.
        await inverter.start()
        await inverter.start()  # should not raise
        assert inverter.state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_fault_transitions_to_fault_state(self, inverter: SolarInverter) -> None:
        # fault() stores a diagnostic code and transitions to FAULT so the
        # controller can detect and report the condition via telemetry.
        await inverter.start()
        await inverter.fault("OVERCURRENT")
        assert inverter.state == AssetState.FAULT
        assert inverter.fault_code == "OVERCURRENT"

    @pytest.mark.asyncio
    async def test_reset_from_fault_returns_to_idle(self, inverter: SolarInverter) -> None:
        # reset() is the only recovery path from FAULT — it clears the fault
        # code and returns to IDLE so start() can be called again.
        await inverter.fault("OVERCURRENT")
        await inverter.reset()
        assert inverter.state == AssetState.IDLE
        assert inverter.fault_code is None

    @pytest.mark.asyncio
    async def test_reset_when_not_faulted_is_noop(self, inverter: SolarInverter) -> None:
        # Calling reset() from IDLE (not FAULT) must be a silent no-op.
        await inverter.reset()  # should not raise
        assert inverter.state == AssetState.IDLE


# ── TestSetOutput ─────────────────────────────────────────────────────────────
# set_output() adjusts the desired power setpoint within [0, max_power_kw].
# The actual power is further bounded by what the irradiance can supply,
# so the setpoint is an upper limit, not a guaranteed output.

class TestSetOutput:
    @pytest.mark.asyncio
    async def test_set_output_while_running(self, inverter: SolarInverter) -> None:
        # At full irradiance the physics model can supply up to ~max_power_kw,
        # so a 50 kW setpoint should be reflected in the next telemetry call.
        await inverter.start()
        await inverter.set_output(50.0)
        telemetry = inverter.get_telemetry(irradiance_w_m2=1000.0)
        assert telemetry.power_output_kw == pytest.approx(50.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_set_output_clamps_to_max(self, inverter: SolarInverter) -> None:
        # Values above nameplate capacity are silently clamped to max_power_kw
        # to prevent over-requesting from the physical inverter.
        await inverter.start()
        await inverter.set_output(999.0)
        assert inverter._target_power_kw == 100.0

    @pytest.mark.asyncio
    async def test_set_output_clamps_to_zero(self, inverter: SolarInverter) -> None:
        # Negative output makes no physical sense — clamped to 0 (curtailment).
        await inverter.start()
        await inverter.set_output(-10.0)
        assert inverter._target_power_kw == 0.0

    @pytest.mark.asyncio
    async def test_set_output_raises_when_not_running(self, inverter: SolarInverter) -> None:
        # Commands sent to an IDLE inverter are rejected so the caller cannot
        # accidentally set a setpoint before the startup sequence completes.
        with pytest.raises(RuntimeError):
            await inverter.set_output(50.0)


# ── TestTelemetry ─────────────────────────────────────────────────────────────
# get_telemetry(irradiance_w_m2) is called every telemetry_interval_s.
# It is a pure calculation — calling it does not change the inverter's state.

class TestTelemetry:
    @pytest.mark.asyncio
    async def test_power_is_zero_when_idle(self, inverter: SolarInverter) -> None:
        # An inverter in IDLE is not connected to the grid, so it must report
        # 0 kW even under direct sunlight.
        telemetry = inverter.get_telemetry(irradiance_w_m2=1000.0)
        assert telemetry.power_output_kw == 0.0

    @pytest.mark.asyncio
    async def test_power_is_zero_when_irradiance_is_zero(self, inverter: SolarInverter) -> None:
        # No sunlight → no power, even when the inverter is RUNNING (e.g. night).
        await inverter.start()
        telemetry = inverter.get_telemetry(irradiance_w_m2=0.0)
        assert telemetry.power_output_kw == 0.0

    @pytest.mark.asyncio
    async def test_full_irradiance_produces_near_max_power(self, inverter: SolarInverter) -> None:
        # At STC irradiance (1000 W/m²) with default target = max_power_kw,
        # actual output should approach but be slightly below 100 kW because
        # panel temperature derating reduces efficiency above 25°C.
        await inverter.start()
        telemetry = inverter.get_telemetry(irradiance_w_m2=1000.0)
        # At full irradiance and STC, output should be close to max
        assert telemetry.power_output_kw > 90.0

    @pytest.mark.asyncio
    async def test_telemetry_includes_required_fields(self, inverter: SolarInverter) -> None:
        # Downstream consumers (controller-module, telemetry-module) depend on
        # these fields being present in the serialised dict.
        await inverter.start()
        data = inverter.get_telemetry(irradiance_w_m2=500.0).to_dict()
        required_fields = {"asset_id", "asset_type", "state", "power_output_kw", "timestamp"}
        assert required_fields.issubset(data.keys())

    @pytest.mark.asyncio
    async def test_temperature_rises_with_irradiance(self, inverter: SolarInverter) -> None:
        # Panel temperature model: temp_c = 25 + TEMP_RISE_FACTOR * irradiance.
        # At 1000 W/m² the panel warms above STC (25°C), which in turn
        # reduces efficiency via TEMP_COEFFICIENT.
        await inverter.start()
        telemetry = inverter.get_telemetry(irradiance_w_m2=1000.0)
        assert telemetry.temperature_c > 25.0

    @pytest.mark.asyncio
    async def test_power_is_zero_after_fault(self, inverter: SolarInverter) -> None:
        # _on_fault() clears both _current_power_kw and _target_power_kw so
        # the inverter stops exporting immediately on any fault condition.
        await inverter.start()
        await inverter.fault("TEST_FAULT")
        telemetry = inverter.get_telemetry(irradiance_w_m2=1000.0)
        assert telemetry.power_output_kw == 0.0
