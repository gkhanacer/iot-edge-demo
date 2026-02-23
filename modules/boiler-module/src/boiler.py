"""Industrial boiler asset driver.

Models a gas/electric industrial boiler with temperature control and
PID-like convergence simulation.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from iot_edge_base.asset import AssetState, BaseAsset

logger = structlog.get_logger()

# Thermal loss coefficient (°C/s at 20°C ambient)
THERMAL_LOSS_COEFF = 0.001
AMBIENT_TEMP_C = 20.0
# Max heating rate (°C/s at full power)
HEATING_RATE_C_PER_KW_S = 0.002


@dataclass
class BoilerTelemetry:
    asset_id: str
    asset_type: str = "industrial_boiler"
    state: str = AssetState.IDLE
    current_temperature_c: float = 20.0
    target_temperature_c: float = 0.0
    power_kw: float = 0.0
    efficiency: float = 0.0
    pressure_bar: float = 1.0
    fault_code: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "asset_type": self.asset_type,
            "state": self.state,
            "current_temperature_c": self.current_temperature_c,
            "target_temperature_c": self.target_temperature_c,
            "power_kw": self.power_kw,
            "efficiency": self.efficiency,
            "pressure_bar": self.pressure_bar,
            "fault_code": self.fault_code,
            "timestamp": self.timestamp,
        }


class Boiler(BaseAsset):
    """Industrial boiler driver with temperature setpoint control.

    Args:
        asset_id: Unique identifier.
        max_power_kw: Maximum heating power in kW.
        default_target_c: Default target temperature in Celsius.
        startup_delay_s: Simulated startup duration in seconds.
    """

    def __init__(
        self,
        asset_id: str,
        max_power_kw: float = 200.0,
        default_target_c: float = 80.0,
        startup_delay_s: float = 3.0,
    ) -> None:
        super().__init__(asset_id)
        self.max_power_kw = max_power_kw
        self._startup_delay_s = startup_delay_s
        self._target_temp_c: float = default_target_c
        self._current_temp_c: float = AMBIENT_TEMP_C
        self._power_kw: float = 0.0
        self._pressure_bar: float = 1.0

    async def set_temperature(self, target_celsius: float) -> None:
        """Set the target temperature setpoint.

        Raises:
            RuntimeError: If boiler is not in RUNNING state.
            ValueError: If temperature out of safe operating range.
        """
        if self._state != AssetState.RUNNING:
            raise RuntimeError(f"Cannot set temperature in state {self._state}")
        if not (40.0 <= target_celsius <= 120.0):
            raise ValueError(f"Temperature {target_celsius}°C out of safe range [40, 120]")
        self._target_temp_c = target_celsius
        logger.info("Temperature setpoint updated", asset_id=self.asset_id, target_c=target_celsius)

    def tick(self, elapsed_s: float) -> None:
        """Advance the thermal simulation by elapsed_s seconds."""
        if self._state != AssetState.RUNNING:
            # Cool down toward ambient
            diff = self._current_temp_c - AMBIENT_TEMP_C
            self._current_temp_c -= THERMAL_LOSS_COEFF * diff * elapsed_s
            self._power_kw = 0.0
            return

        diff = self._target_temp_c - self._current_temp_c
        if diff > 0.5:
            # Proportional heating — full power when diff > 20°C
            fraction = min(1.0, diff / 20.0)
            self._power_kw = fraction * self.max_power_kw
            self._current_temp_c += HEATING_RATE_C_PER_KW_S * self._power_kw * elapsed_s
        else:
            # Maintain setpoint — minimal power
            self._power_kw = 0.02 * self.max_power_kw
            self._current_temp_c = max(
                AMBIENT_TEMP_C,
                self._current_temp_c - THERMAL_LOSS_COEFF * (self._current_temp_c - AMBIENT_TEMP_C) * elapsed_s,
            )

        # Pressure follows temperature (ideal gas approximation)
        self._pressure_bar = 1.0 + (self._current_temp_c - AMBIENT_TEMP_C) * 0.05

        # Safety: fault on over-temperature or over-pressure
        if self._current_temp_c > 125.0:
            asyncio.ensure_future(self.fault("OVER_TEMPERATURE"))
        elif self._pressure_bar > 7.0:
            asyncio.ensure_future(self.fault("OVER_PRESSURE"))

    def get_telemetry(self) -> BoilerTelemetry:
        efficiency = 0.92 if self._state == AssetState.RUNNING and self._power_kw > 0 else 0.0
        return BoilerTelemetry(
            asset_id=self.asset_id,
            state=self._state,
            current_temperature_c=round(self._current_temp_c, 1),
            target_temperature_c=round(self._target_temp_c, 1),
            power_kw=round(self._power_kw, 2),
            efficiency=round(efficiency, 3),
            pressure_bar=round(self._pressure_bar, 2),
            fault_code=self._fault_code,
        )

    # ── BaseAsset hooks ──────────────────────────────────────────────────────

    async def _on_start(self) -> None:
        await asyncio.sleep(self._startup_delay_s)

    async def _on_stop(self) -> None:
        self._power_kw = 0.0

    async def _on_fault(self, code: str) -> None:
        self._power_kw = 0.0
