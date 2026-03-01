"""Tests for Boiler — state machine, temperature control, and thermal simulation.

Boiler extends BaseAsset (IDLE → STARTING → RUNNING → IDLE | FAULT).

The thermal simulation runs in tick(elapsed_s):
- RUNNING + diff > 0.5°C: proportional heating, power = fraction * max_power_kw
- RUNNING + diff ≤ 0.5°C: setpoint maintenance, minimal power (2 % of max)
- Not RUNNING: passive cool-down toward AMBIENT_TEMP_C (20°C) at THERMAL_LOSS_COEFF

Temperature setpoints are validated in [40, 120]°C (safe operating range):
- Out of range raises ValueError (not RuntimeError) — a distinct error type
  lets callers distinguish "wrong state" from "physically unsafe command".

Fixture note: startup_delay_s=0 avoids asyncio.sleep in start().
"""

import pytest

from iot_edge_base.asset import AssetState
from src.boiler import Boiler


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def boiler() -> Boiler:
    # startup_delay_s=0 so start() completes immediately without sleeping.
    return Boiler(
        asset_id="boiler-test",
        max_power_kw=200.0,
        default_target_c=80.0,
        startup_delay_s=0,
    )


# ── TestStateMachine ──────────────────────────────────────────────────────────
# Basic lifecycle transitions — confirms the BaseAsset hooks (_on_start,
# _on_stop) don't break the parent contract for the Boiler subclass.

class TestStateMachine:
    @pytest.mark.asyncio
    async def test_start(self, boiler: Boiler) -> None:
        await boiler.start()
        assert boiler.state == AssetState.RUNNING

    @pytest.mark.asyncio
    async def test_stop(self, boiler: Boiler) -> None:
        # _on_stop() sets _power_kw = 0 and the parent transitions to IDLE.
        await boiler.start()
        await boiler.stop()
        assert boiler.state == AssetState.IDLE


# ── TestTemperatureControl ────────────────────────────────────────────────────
# set_temperature() and tick() together implement the control loop.
# set_temperature() only updates the setpoint; tick() drives the physics.

class TestTemperatureControl:
    @pytest.mark.asyncio
    async def test_set_temperature_while_running(self, boiler: Boiler) -> None:
        # A valid setpoint in [40, 120]°C must be stored so the next tick()
        # heats toward the new target.
        await boiler.start()
        await boiler.set_temperature(90.0)
        assert boiler._target_temp_c == 90.0

    @pytest.mark.asyncio
    async def test_set_temperature_rejects_when_idle(self, boiler: Boiler) -> None:
        # Changing the setpoint while IDLE would have no effect on tick() and
        # could confuse the operator — rejected with RuntimeError.
        with pytest.raises(RuntimeError):
            await boiler.set_temperature(80.0)

    @pytest.mark.asyncio
    async def test_set_temperature_rejects_out_of_range(self, boiler: Boiler) -> None:
        # 200°C exceeds the safe operating range [40, 120]°C. The boiler raises
        # ValueError (not RuntimeError) to distinguish a physically unsafe value
        # from a wrong-state error — callers can handle them separately.
        await boiler.start()
        with pytest.raises(ValueError):
            await boiler.set_temperature(200.0)

    @pytest.mark.asyncio
    async def test_temperature_rises_when_heating(self, boiler: Boiler) -> None:
        # After start(), current_temp (20°C) is below target (80°C).
        # A single tick() with elapsed_s=60 should heat the water noticeably.
        await boiler.start()
        initial_temp = boiler._current_temp_c
        boiler.tick(elapsed_s=60)
        assert boiler._current_temp_c > initial_temp

    @pytest.mark.asyncio
    async def test_temperature_falls_when_stopped(self, boiler: Boiler) -> None:
        # With the boiler in IDLE (not started), tick() runs the cool-down
        # branch: temp converges toward AMBIENT_TEMP_C (20°C) via thermal loss.
        # We manually set the temperature above ambient to observe the decay.
        boiler._current_temp_c = 80.0
        boiler.tick(elapsed_s=60)
        assert boiler._current_temp_c < 80.0

    @pytest.mark.asyncio
    async def test_power_is_zero_when_idle(self, boiler: Boiler) -> None:
        # tick() in the IDLE branch explicitly sets _power_kw = 0 — no heating
        # power is consumed when the boiler is not running.
        boiler.tick(elapsed_s=10)
        assert boiler._power_kw == 0.0


# ── TestTelemetry ─────────────────────────────────────────────────────────────
# get_telemetry() is called each telemetry cycle and must serialise all
# fields needed by the controller and cloud reporting pipeline.

class TestTelemetry:
    def test_telemetry_contains_required_fields(self, boiler: Boiler) -> None:
        # The controller-module and telemetry-module depend on these fields
        # being present in the serialised dict for grid balance calculation.
        data = boiler.get_telemetry().to_dict()
        required = {"asset_id", "state", "current_temperature_c", "target_temperature_c", "power_kw", "timestamp"}
        assert required.issubset(data.keys())

    @pytest.mark.asyncio
    async def test_efficiency_nonzero_when_running_and_heating(self, boiler: Boiler) -> None:
        # Efficiency is a fixed 0.92 whenever the boiler is RUNNING and
        # _power_kw > 0. We set _power_kw directly to isolate the telemetry
        # calculation from the tick() heating model.
        await boiler.start()
        boiler._power_kw = 100.0
        t = boiler.get_telemetry()
        assert t.efficiency > 0.0
