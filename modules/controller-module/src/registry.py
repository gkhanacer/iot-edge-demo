"""Asset state registry.

Maintains the last-known state of all connected asset modules,
keyed by asset_id. Thread-safe via asyncio lock.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator


@dataclass
class AssetSnapshot:
    asset_id: str
    asset_type: str
    state: str
    power_kw: float = 0.0          # net power: positive = generating/charging, negative = consuming
    last_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw: dict = field(default_factory=dict)


def _extract_power_kw(data: dict) -> float:
    """Normalise power field name across asset types."""
    # solar: power_output_kw (positive = generating)
    # battery: power_kw (positive = charging = consuming, negative = discharging = generating)
    # boiler: power_kw (positive = consuming)
    asset_type = data.get("asset_type", "")
    if asset_type == "solar_inverter":
        return float(data.get("power_output_kw", 0.0))
    if asset_type == "battery_storage":
        # discharging (negative power_kw) = net generation for the grid
        return -float(data.get("power_kw", 0.0))
    if asset_type == "industrial_boiler":
        return -float(data.get("power_kw", 0.0))
    return float(data.get("power_kw", data.get("power_output_kw", 0.0)))


class AssetRegistry:
    """Thread-safe registry of asset telemetry snapshots."""

    def __init__(self) -> None:
        self._assets: dict[str, AssetSnapshot] = {}
        self._lock = asyncio.Lock()

    async def update(self, data: dict) -> None:
        """Upsert an asset snapshot from a raw telemetry dict."""
        asset_id = data.get("asset_id")
        if not asset_id:
            return
        snapshot = AssetSnapshot(
            asset_id=asset_id,
            asset_type=data.get("asset_type", "unknown"),
            state=data.get("state", "UNKNOWN"),
            power_kw=_extract_power_kw(data),
            last_seen=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            raw=data,
        )
        async with self._lock:
            self._assets[asset_id] = snapshot

    async def get_all(self) -> list[AssetSnapshot]:
        async with self._lock:
            return list(self._assets.values())

    async def get(self, asset_id: str) -> AssetSnapshot | None:
        async with self._lock:
            return self._assets.get(asset_id)

    async def count(self) -> int:
        async with self._lock:
            return len(self._assets)
