"""Battery storage asset driver.

Models a battery energy storage system (BESS) with charging/discharging
state machine and state-of-charge (SoC) simulation.

LSP note: _state (from BaseAsset) tracks the operational lifecycle:
  IDLE → STARTING → RUNNING → STOPPING → IDLE | FAULT
The battery-specific sub-state (IDLE/CHARGING/DISCHARGING) is exposed via
the `mode` property and does not override the parent `state` property.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import structlog

from iot_edge_base.asset import AssetState, BaseAsset

logger = structlog.get_logger()

# Charge/discharge efficiency (round-trip ~90%)
CHARGE_EFFICIENCY = 0.95
DISCHARGE_EFFICIENCY = 0.95
# Minimum and maximum allowed SoC
SOC_MIN = 0.05
SOC_MAX = 0.95


class BatteryMode(str, Enum):
    """Operating mode within the RUNNING state."""
    IDLE = "IDLE"
    CHARGING = "CHARGING"
    DISCHARGING = "DISCHARGING"


@dataclass
class BatteryTelemetry:
    asset_id: str
    asset_type: str = "battery_storage"
    state: str = AssetState.IDLE       # operational lifecycle state
    mode: str = BatteryMode.IDLE       # battery-specific sub-state
    state_of_charge: float = 0.5       # 0.0 – 1.0
    power_kw: float = 0.0              # positive = charging, negative = discharging
    capacity_kwh: float = 0.0
    energy_stored_kwh: float = 0.0
    temperature_c: float = 25.0
    fault_code: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "asset_type": self.asset_type,
            "state": self.state,
            "mode": self.mode,
            "state_of_charge": self.state_of_charge,
            "power_kw": self.power_kw,
            "capacity_kwh": self.capacity_kwh,
            "energy_stored_kwh": self.energy_stored_kwh,
            "temperature_c": self.temperature_c,
            "fault_code": self.fault_code,
            "timestamp": self.timestamp,
            "message_id": self.message_id,
        }


class BatteryStorage(BaseAsset):
    """Battery energy storage system driver.

    Lifecycle: call start() to transition to RUNNING, then use
    start_charging() / start_discharging() to control the mode.

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
        self._power_kw: float = 0.0     # positive=charging, negative=discharging
        self._temperature_c: float = 25.0
        self._mode: BatteryMode = BatteryMode.IDLE

    @property
    def mode(self) -> BatteryMode:
        """Battery-specific operating mode (IDLE / CHARGING / DISCHARGING)."""
        return self._mode

    async def start_charging(self, power_kw: float) -> None:
        """Begin charging at the specified rate.

        Requires the battery to be in RUNNING state (call start() first).

        Raises:
            RuntimeError: If battery is not RUNNING or is at max SoC.
        """
        if self._state != AssetState.RUNNING:
            raise RuntimeError(f"Cannot charge while in state {self._state} — call start() first")
        if self._soc >= SOC_MAX:
            raise RuntimeError("Battery is at maximum charge")
        self._power_kw = min(power_kw, self.max_power_kw)
        self._mode = BatteryMode.CHARGING
        logger.info("Battery charging started", asset_id=self.asset_id, power_kw=self._power_kw)

    async def start_discharging(self, power_kw: float) -> None:
        """Begin discharging at the specified rate.

        Requires the battery to be in RUNNING state (call start() first).

        Raises:
            RuntimeError: If battery is not RUNNING or is at min SoC.
        """
        if self._state != AssetState.RUNNING:
            raise RuntimeError(f"Cannot discharge while in state {self._state} — call start() first")
        if self._soc <= SOC_MIN:
            raise RuntimeError("Battery is at minimum charge")
        self._power_kw = -min(power_kw, self.max_power_kw)
        self._mode = BatteryMode.DISCHARGING
        logger.info("Battery discharging started", asset_id=self.asset_id, power_kw=abs(self._power_kw))

    async def set_idle(self) -> None:
        """Stop charging/discharging, keep battery in RUNNING state."""
        self._power_kw = 0.0
        self._mode = BatteryMode.IDLE
        logger.info("Battery set to idle mode", asset_id=self.asset_id)

    def tick(self, elapsed_s: float) -> None:
        """Advance the SoC simulation by elapsed_s seconds."""
        if self._mode == BatteryMode.CHARGING:
            # energy stored = power × efficiency × time; /3600 converts seconds to hours
            delta_kwh = (self._power_kw * CHARGE_EFFICIENCY * elapsed_s) / 3600.0
            self._soc = min(SOC_MAX, self._soc + delta_kwh / self.capacity_kwh)
            if self._soc >= SOC_MAX:
                self._power_kw = 0.0
                self._mode = BatteryMode.IDLE
                logger.info("Battery fully charged", asset_id=self.asset_id)

        elif self._mode == BatteryMode.DISCHARGING:
            # energy drawn from battery exceeds energy delivered (inverse efficiency)
            delta_kwh = (abs(self._power_kw) / DISCHARGE_EFFICIENCY * elapsed_s) / 3600.0
            self._soc = max(SOC_MIN, self._soc - delta_kwh / self.capacity_kwh)
            if self._soc <= SOC_MIN:
                self._power_kw = 0.0
                self._mode = BatteryMode.IDLE
                logger.info("Battery depleted", asset_id=self.asset_id)

        # Temperature rises proportionally to absolute power (same model for charge and discharge)
        self._temperature_c = 25.0 + 0.05 * abs(self._power_kw)

    def get_telemetry(self) -> BatteryTelemetry:
        return BatteryTelemetry(
            asset_id=self.asset_id,
            state=self._state,
            mode=self._mode,
            state_of_charge=round(self._soc, 4),
            power_kw=round(self._power_kw, 2),
            capacity_kwh=self.capacity_kwh,
            energy_stored_kwh=round(self._soc * self.capacity_kwh, 2),
            temperature_c=round(self._temperature_c, 1),
            fault_code=self._fault_code,
        )

    # ── BaseAsset hooks (LSP-compliant — do not override start/stop directly) ──

    async def _on_start(self) -> None:
        await asyncio.sleep(self._startup_delay_s)
        self._mode = BatteryMode.IDLE

    async def _on_stop(self) -> None:
        self._power_kw = 0.0
        self._mode = BatteryMode.IDLE

    async def _on_fault(self, code: str) -> None:
        self._power_kw = 0.0
        self._mode = BatteryMode.IDLE
