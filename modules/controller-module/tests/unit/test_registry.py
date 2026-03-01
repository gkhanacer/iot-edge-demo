"""Tests for AssetRegistry — telemetry upsert and power normalisation.

AssetRegistry maintains the last-known state of every connected asset,
keyed by asset_id. It is the single source of truth consumed by Aggregator.

Power sign convention (normalised by _extract_power_kw):
    solar_inverter    → +ve  (generating power for the grid)
    battery_storage   → -ve when charging (consuming grid power),
                        +ve when discharging (supplying grid power)
    industrial_boiler → -ve (always consuming)

Raw payloads use different field names per asset type:
    solar_inverter    → power_output_kw (always positive)
    battery_storage   → power_kw        (positive = charging, negative = discharging)
    industrial_boiler → power_kw        (positive = heating load)

The registry normalises these on update() so the Aggregator sees a uniform
signed power_kw on every AssetSnapshot.
"""

import pytest

from src.registry import AssetRegistry, AssetSnapshot


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def registry() -> AssetRegistry:
    return AssetRegistry()


# ── Test payloads ─────────────────────────────────────────────────────────────
# Representative telemetry dicts as received from each asset module over MQTT.

SOLAR_PAYLOAD = {
    "asset_id": "solar-01",
    "asset_type": "solar_inverter",
    "state": "RUNNING",
    "power_output_kw": 50.0,        # field name differs from battery/boiler
    "timestamp": "2024-01-01T12:00:00Z",
}

BATTERY_PAYLOAD = {
    "asset_id": "battery-01",
    "asset_type": "battery_storage",
    "state": "CHARGING",
    "power_kw": 30.0,               # positive = charging = consuming from grid
    "timestamp": "2024-01-01T12:00:00Z",
}

BOILER_PAYLOAD = {
    "asset_id": "boiler-01",
    "asset_type": "industrial_boiler",
    "state": "RUNNING",
    "power_kw": 100.0,              # positive = heating load = consuming from grid
    "timestamp": "2024-01-01T12:00:00Z",
}


# ── TestRegistryUpdate ────────────────────────────────────────────────────────
# Tests the upsert behaviour: insert on first update, overwrite on subsequent.

class TestRegistryUpdate:
    @pytest.mark.asyncio
    async def test_update_stores_asset(self, registry: AssetRegistry) -> None:
        # A valid payload with asset_id must create one entry in the registry.
        await registry.update(SOLAR_PAYLOAD)
        assert await registry.count() == 1

    @pytest.mark.asyncio
    async def test_update_ignores_missing_asset_id(self, registry: AssetRegistry) -> None:
        # Malformed telemetry without asset_id is silently dropped — the registry
        # stays empty rather than creating an entry under an unknown key.
        await registry.update({"state": "RUNNING"})
        assert await registry.count() == 0

    @pytest.mark.asyncio
    async def test_update_overwrites_existing(self, registry: AssetRegistry) -> None:
        # The second update for the same asset_id replaces the snapshot.
        # This test confirms the registry reflects the most recent reading.
        await registry.update(SOLAR_PAYLOAD)
        updated = {**SOLAR_PAYLOAD, "power_output_kw": 75.0}
        await registry.update(updated)
        snapshot = await registry.get("solar-01")
        assert snapshot.power_kw == 75.0

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown(self, registry: AssetRegistry) -> None:
        # get() on an asset that has never sent telemetry returns None rather
        # than raising — callers must handle the absent case.
        result = await registry.get("nonexistent")
        assert result is None


# ── TestPowerNormalisation ────────────────────────────────────────────────────
# _extract_power_kw() maps the heterogeneous raw payloads to a unified signed
# power_kw so the Aggregator can sum generation and consumption uniformly.

class TestPowerNormalisation:
    @pytest.mark.asyncio
    async def test_solar_power_is_positive(self, registry: AssetRegistry) -> None:
        # Solar generates power — stored as-is (positive = contributing to grid).
        await registry.update(SOLAR_PAYLOAD)
        s = await registry.get("solar-01")
        assert s.power_kw == 50.0  # generating

    @pytest.mark.asyncio
    async def test_charging_battery_power_is_negative(self, registry: AssetRegistry) -> None:
        # A charging battery consumes grid power. The raw payload sends
        # power_kw=+30 (charging convention), but the registry flips the sign
        # to -30 so it appears as consumption in the grid balance formula.
        """Charging battery consumes power → negative contribution to grid balance."""
        await registry.update(BATTERY_PAYLOAD)
        s = await registry.get("battery-01")
        assert s.power_kw == -30.0

    @pytest.mark.asyncio
    async def test_boiler_power_is_negative(self, registry: AssetRegistry) -> None:
        # A boiler always consumes power. The raw payload sends power_kw=+100
        # (heating load convention), registry flips to -100 (consumption).
        await registry.update(BOILER_PAYLOAD)
        s = await registry.get("boiler-01")
        assert s.power_kw == -100.0
