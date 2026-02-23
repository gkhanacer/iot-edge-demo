"""Battery storage asset driver.

Models a battery energy storage system (BESS) with charging/discharging
state machine and state-of-charge (SoC) simulation.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from iot_edge_base.asset import AssetState, BaseAsset

logger = structlog.get_logger()

# Charge/discharge efficiency (round-trip ~90%)
CHARGE_EFFICIENCY = 0.95
DISCHARGE_EFFICIENCY = 0.95
# Minimum and maximum allowed SoC
SOC_MIN = 0.05
SOC_MAX = 0.95


class BatteryState(str):
    """Extended states for battery (on top of AssetState)."""
    CHARGING = "CHARGING"
    DISCHARGING = "DISCHARGING"


@dataclass
class BatteryTelemetry:
    asset_id: str
    asset_type: str = "battery_storage"
    state: str = AssetState.IDLE
    state_of_charge: float = 0.5       # 0.0 – 1.0
    power_kw: float = 0.0              # positive = charging, negative = discharging
    capacity_kwh: float = 0.0
    energy_stored_kwh: float = 0.0
    temperature_c: float = 25.0
    fault_code: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "asset_type": self.asset_type,
            "state": self.state,
            "state_of_charge": self.state_of_charge,
            "power_kw": self.power_kw,
            "capacity_kwh": self.capacity_kwh,
            "energy_stored_kwh": self.energy_stored_kwh,
            "temperature_c": self.temperature_c,
            "fault_code": self.fault_code,
            "timestamp": self.timestamp,
        }


class BatteryStorage(BaseAsset):
    """Battery energy storage system driver.

    Args:
        asset_id: Unique identifier.
        capacity_kwh: Total energy capacity.
        max_power_kw: Maximum charge/discharge rate.
        initial_soc: Initial state of charge (0.0 – 1.0).
        startup_delay_s: Simulated startup duration in seconds.
    """

    def __init__(
        self,
        asset_id: str,
        capacity_kwh: float = 500.0,
        max_power_kw: float = 100.0,
        initial_soc: float = 0.5,
        startup_delay_s: float = 1.0,
    ) -> None:
        super().__init__(asset_id)
        self.capacity_kwh = capacity_kwh
        self.max_power_kw = max_power_kw
        self._startup_delay_s = startup_delay_s
        self._soc = initial_soc
        self._power_kw: float = 0.0            # positive=charging, negative=discharging
        self._temperature_c: float = 25.0
        self._battery_state: str = AssetState.IDLE

    @property
    def state(self) -> str:
        return self._battery_state

    async def start_charging(self, power_kw: float) -> None:
        """Begin charging at the specified rate.

        Raises:
            RuntimeError: If battery cannot accept charging in current state.
        """
        if self._battery_state not in (AssetState.IDLE, AssetState.RUNNING, BatteryState.DISCHARGING):
            raise RuntimeError(f"Cannot start charging in state {self._battery_state}")
        if self._soc >= SOC_MAX:
            raise RuntimeError("Battery is at maximum charge")
        self._power_kw = min(power_kw, self.max_power_kw)
        self._battery_state = BatteryState.CHARGING
        logger.info("Battery charging started", asset_id=self.asset_id, power_kw=self._power_kw)

    async def start_discharging(self, power_kw: float) -> None:
        """Begin discharging at the specified rate.

        Raises:
            RuntimeError: If battery cannot discharge in current state.
        """
        if self._battery_state not in (AssetState.IDLE, AssetState.RUNNING, BatteryState.CHARGING):
            raise RuntimeError(f"Cannot start discharging in state {self._battery_state}")
        if self._soc <= SOC_MIN:
            raise RuntimeError("Battery is at minimum charge")
        self._power_kw = -min(power_kw, self.max_power_kw)
        self._battery_state = BatteryState.DISCHARGING
        logger.info("Battery discharging started", asset_id=self.asset_id, power_kw=abs(self._power_kw))

    async def stop(self) -> None:
        self._power_kw = 0.0
        self._battery_state = AssetState.IDLE
        logger.info("Battery stopped", asset_id=self.asset_id)

    def tick(self, elapsed_s: float) -> None:
        """Advance the SoC simulation by elapsed_s seconds."""
        if self._battery_state == BatteryState.CHARGING:
            delta_kwh = (self._power_kw * CHARGE_EFFICIENCY * elapsed_s) / 3600.0
            self._soc = min(SOC_MAX, self._soc + delta_kwh / self.capacity_kwh)
            if self._soc >= SOC_MAX:
                self._power_kw = 0.0
                self._battery_state = AssetState.IDLE
                logger.info("Battery fully charged", asset_id=self.asset_id)

        elif self._battery_state == BatteryState.DISCHARGING:
            delta_kwh = (abs(self._power_kw) / DISCHARGE_EFFICIENCY * elapsed_s) / 3600.0
            self._soc = max(SOC_MIN, self._soc - delta_kwh / self.capacity_kwh)
            if self._soc <= SOC_MIN:
                self._power_kw = 0.0
                self._battery_state = AssetState.IDLE
                logger.info("Battery depleted", asset_id=self.asset_id)

        # Temperature rises slightly under load
        self._temperature_c = 25.0 + 0.05 * abs(self._power_kw)

    def get_telemetry(self) -> BatteryTelemetry:
        return BatteryTelemetry(
            asset_id=self.asset_id,
            state=self._battery_state,
            state_of_charge=round(self._soc, 4),
            power_kw=round(self._power_kw, 2),
            capacity_kwh=self.capacity_kwh,
            energy_stored_kwh=round(self._soc * self.capacity_kwh, 2),
            temperature_c=round(self._temperature_c, 1),
            fault_code=self._fault_code,
        )

    # ── BaseAsset hooks ──────────────────────────────────────────────────────

    async def _on_start(self) -> None:
        await asyncio.sleep(self._startup_delay_s)
        self._battery_state = AssetState.RUNNING

    async def _on_stop(self) -> None:
        self._power_kw = 0.0
        self._battery_state = AssetState.IDLE

    async def _on_fault(self, code: str) -> None:
        self._power_kw = 0.0
        self._battery_state = AssetState.FAULT
