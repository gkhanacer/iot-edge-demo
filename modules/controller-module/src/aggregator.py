"""Telemetry aggregator and grid balance logic.

Aggregates asset snapshots into a single cloud-bound payload and
determines grid balance / control actions needed.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .registry import AssetRegistry, AssetSnapshot


@dataclass
class GridAlert:
    severity: str       # "warning" | "critical"
    code: str
    message: str
    asset_id: str | None = None


@dataclass
class AggregatedTelemetry:
    device_id: str
    timestamp: str
    total_generation_kw: float          # solar + discharging battery
    total_consumption_kw: float         # boiler + charging battery
    grid_balance_kw: float              # generation - consumption (positive = surplus)
    asset_count: int
    assets: dict[str, dict]
    alerts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "timestamp": self.timestamp,
            "total_generation_kw": self.total_generation_kw,
            "total_consumption_kw": self.total_consumption_kw,
            "grid_balance_kw": self.grid_balance_kw,
            "asset_count": self.asset_count,
            "assets": self.assets,
            "alerts": self.alerts,
        }


class Aggregator:
    """Aggregates registry snapshots and computes derived grid metrics."""

    FAULT_SEVERITY_MAP = {
        "FAULT": "critical",
    }

    def __init__(self, device_id: str, surplus_threshold_kw: float = 10.0) -> None:
        self.device_id = device_id
        self.surplus_threshold_kw = surplus_threshold_kw

    async def compute(self, registry: AssetRegistry) -> AggregatedTelemetry:
        snapshots = await registry.get_all()

        generation = sum(s.power_kw for s in snapshots if s.power_kw > 0)
        consumption = sum(abs(s.power_kw) for s in snapshots if s.power_kw < 0)
        balance = generation - consumption

        alerts = self._check_alerts(snapshots, balance)

        return AggregatedTelemetry(
            device_id=self.device_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_generation_kw=round(generation, 2),
            total_consumption_kw=round(consumption, 2),
            grid_balance_kw=round(balance, 2),
            asset_count=len(snapshots),
            assets={s.asset_id: {"state": s.state, "power_kw": s.power_kw, "asset_type": s.asset_type} for s in snapshots},
            alerts=[{"severity": a.severity, "code": a.code, "message": a.message, "asset_id": a.asset_id} for a in alerts],
        )

    def _check_alerts(self, snapshots: list[AssetSnapshot], balance: float) -> list[GridAlert]:
        alerts: list[GridAlert] = []

        for s in snapshots:
            if s.state == "FAULT":
                alerts.append(GridAlert(
                    severity="critical",
                    code="ASSET_FAULT",
                    message=f"Asset {s.asset_id} ({s.asset_type}) is in FAULT state",
                    asset_id=s.asset_id,
                ))

        if balance > self.surplus_threshold_kw:
            alerts.append(GridAlert(
                severity="warning",
                code="GRID_SURPLUS",
                message=f"Grid surplus of {balance:.1f} kW — consider increasing battery charging or curtailing solar",
            ))

        if balance < -self.surplus_threshold_kw:
            alerts.append(GridAlert(
                severity="warning",
                code="GRID_DEFICIT",
                message=f"Grid deficit of {abs(balance):.1f} kW — consider discharging battery",
            ))

        return alerts
