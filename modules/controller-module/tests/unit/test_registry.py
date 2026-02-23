import pytest

from src.registry import AssetRegistry, AssetSnapshot


@pytest.fixture
def registry() -> AssetRegistry:
    return AssetRegistry()


SOLAR_PAYLOAD = {
    "asset_id": "solar-01",
    "asset_type": "solar_inverter",
    "state": "RUNNING",
    "power_output_kw": 50.0,
    "timestamp": "2024-01-01T12:00:00Z",
}

BATTERY_PAYLOAD = {
    "asset_id": "battery-01",
    "asset_type": "battery_storage",
    "state": "CHARGING",
    "power_kw": 30.0,  # positive = charging = consuming
    "timestamp": "2024-01-01T12:00:00Z",
}

BOILER_PAYLOAD = {
    "asset_id": "boiler-01",
    "asset_type": "industrial_boiler",
    "state": "RUNNING",
    "power_kw": 100.0,
    "timestamp": "2024-01-01T12:00:00Z",
}


class TestRegistryUpdate:
    @pytest.mark.asyncio
    async def test_update_stores_asset(self, registry: AssetRegistry) -> None:
        await registry.update(SOLAR_PAYLOAD)
        assert await registry.count() == 1

    @pytest.mark.asyncio
    async def test_update_ignores_missing_asset_id(self, registry: AssetRegistry) -> None:
        await registry.update({"state": "RUNNING"})
        assert await registry.count() == 0

    @pytest.mark.asyncio
    async def test_update_overwrites_existing(self, registry: AssetRegistry) -> None:
        await registry.update(SOLAR_PAYLOAD)
        updated = {**SOLAR_PAYLOAD, "power_output_kw": 75.0}
        await registry.update(updated)
        snapshot = await registry.get("solar-01")
        assert snapshot.power_kw == 75.0

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown(self, registry: AssetRegistry) -> None:
        result = await registry.get("nonexistent")
        assert result is None


class TestPowerNormalisation:
    @pytest.mark.asyncio
    async def test_solar_power_is_positive(self, registry: AssetRegistry) -> None:
        await registry.update(SOLAR_PAYLOAD)
        s = await registry.get("solar-01")
        assert s.power_kw == 50.0  # generating

    @pytest.mark.asyncio
    async def test_charging_battery_power_is_negative(self, registry: AssetRegistry) -> None:
        """Charging battery consumes power → negative contribution to grid balance."""
        await registry.update(BATTERY_PAYLOAD)
        s = await registry.get("battery-01")
        assert s.power_kw == -30.0

    @pytest.mark.asyncio
    async def test_boiler_power_is_negative(self, registry: AssetRegistry) -> None:
        await registry.update(BOILER_PAYLOAD)
        s = await registry.get("boiler-01")
        assert s.power_kw == -100.0
