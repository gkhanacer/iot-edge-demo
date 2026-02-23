"""Solar inverter asset driver.

Manages a solar inverter's state machine and simulates physics-based
power output based on irradiance and panel temperature.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from iot_edge_base.asset import AssetState, BaseAsset

logger = structlog.get_logger()

# Temperature coefficient of power (%/°C above 25°C STC)
TEMP_COEFFICIENT = 0.004
# Reference efficiency at STC (Standard Test Conditions)
REFERENCE_EFFICIENCY = 0.18
# Panel temperature rise per unit irradiance (°C per W/m²)
TEMP_RISE_FACTOR = 0.02


@dataclass
class SolarTelemetry:
    asset_id: str
    asset_type: str = "solar_inverter"
    state: str = AssetState.IDLE
    power_output_kw: float = 0.0
    irradiance_w_m2: float = 0.0
    efficiency: float = 0.0
    temperature_c: float = 25.0
    fault_code: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "asset_type": self.asset_type,
            "state": self.state,
            "power_output_kw": self.power_output_kw,
            "irradiance_w_m2": self.irradiance_w_m2,
            "efficiency": self.efficiency,
            "temperature_c": self.temperature_c,
            "fault_code": self.fault_code,
            "timestamp": self.timestamp,
        }


class SolarInverter(BaseAsset):
    """Solar inverter driver with physics-based power simulation.

    Args:
        asset_id: Unique identifier for this inverter.
        max_power_kw: Nameplate capacity in kW.
        startup_delay_s: Simulated startup duration in seconds.
    """

    def __init__(
        self,
        asset_id: str,
        max_power_kw: float = 100.0,
        startup_delay_s: float = 2.0,
    ) -> None:
        super().__init__(asset_id)
        self.max_power_kw = max_power_kw
        self._startup_delay_s = startup_delay_s
        self._target_power_kw: float = max_power_kw
        self._current_power_kw: float = 0.0
        self._temperature_c: float = 25.0

    async def set_output(self, target_kw: float) -> None:
        """Set desired output power. Clamped to [0, max_power_kw].

        Raises:
            RuntimeError: If inverter is not in RUNNING state.
        """
        if self._state != AssetState.RUNNING:
            raise RuntimeError(f"Cannot set output while in state {self._state}")
        self._target_power_kw = max(0.0, min(target_kw, self.max_power_kw))
        logger.info("Output target updated", asset_id=self.asset_id, target_kw=self._target_power_kw)

    def get_telemetry(self, irradiance_w_m2: float = 0.0) -> SolarTelemetry:
        """Calculate current telemetry based on irradiance.

        Args:
            irradiance_w_m2: Current solar irradiance in W/m².

        Returns:
            SolarTelemetry snapshot.
        """
        if self._state == AssetState.RUNNING and irradiance_w_m2 > 0:
            self._temperature_c = 25.0 + TEMP_RISE_FACTOR * irradiance_w_m2
            efficiency = REFERENCE_EFFICIENCY * (1 - TEMP_COEFFICIENT * max(0.0, self._temperature_c - 25.0))
            max_possible_kw = (irradiance_w_m2 / 1000.0) * self.max_power_kw * (efficiency / REFERENCE_EFFICIENCY)
            self._current_power_kw = min(self._target_power_kw, max_possible_kw)
        else:
            self._current_power_kw = 0.0
            if self._state != AssetState.RUNNING:
                self._temperature_c = 25.0

        efficiency = REFERENCE_EFFICIENCY * (1 - TEMP_COEFFICIENT * max(0.0, self._temperature_c - 25.0))

        return SolarTelemetry(
            asset_id=self.asset_id,
            state=self._state,
            power_output_kw=round(self._current_power_kw, 2),
            irradiance_w_m2=round(irradiance_w_m2, 1),
            efficiency=round(efficiency, 4),
            temperature_c=round(self._temperature_c, 1),
            fault_code=self._fault_code,
        )

    # ── BaseAsset hooks ────────────────────────────────────────────────────────

    async def _on_start(self) -> None:
        await asyncio.sleep(self._startup_delay_s)
        self._target_power_kw = self.max_power_kw

    async def _on_stop(self) -> None:
        self._current_power_kw = 0.0
        self._target_power_kw = 0.0

    async def _on_fault(self, code: str) -> None:
        self._current_power_kw = 0.0
        self._target_power_kw = 0.0
