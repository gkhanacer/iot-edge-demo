import pytest

from src.aggregator import Aggregator
from src.registry import AssetRegistry


@pytest.fixture
def registry() -> AssetRegistry:
    return AssetRegistry()


@pytest.fixture
def aggregator() -> Aggregator:
    return Aggregator(device_id="test-device", surplus_threshold_kw=10.0)


async def _populate(registry: AssetRegistry) -> None:
    await registry.update({
        "asset_id": "solar-01",
        "asset_type": "solar_inverter",
        "state": "RUNNING",
        "power_output_kw": 100.0,
    })
    await registry.update({
        "asset_id": "battery-01",
        "asset_type": "battery_storage",
        "state": "CHARGING",
        "power_kw": 30.0,  # consuming
    })
    await registry.update({
        "asset_id": "boiler-01",
        "asset_type": "industrial_boiler",
        "state": "RUNNING",
        "power_kw": 40.0,  # consuming
    })


class TestAggregation:
    @pytest.mark.asyncio
    async def test_total_generation(self, registry, aggregator) -> None:
        await _populate(registry)
        result = await aggregator.compute(registry)
        assert result.total_generation_kw == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_total_consumption(self, registry, aggregator) -> None:
        await _populate(registry)
        result = await aggregator.compute(registry)
        assert result.total_consumption_kw == pytest.approx(70.0)  # 30 + 40

    @pytest.mark.asyncio
    async def test_grid_balance(self, registry, aggregator) -> None:
        await _populate(registry)
        result = await aggregator.compute(registry)
        assert result.grid_balance_kw == pytest.approx(30.0)  # 100 - 70

    @pytest.mark.asyncio
    async def test_empty_registry_produces_zero_balance(self, registry, aggregator) -> None:
        result = await aggregator.compute(registry)
        assert result.grid_balance_kw == 0.0
        assert result.asset_count == 0

    @pytest.mark.asyncio
    async def test_assets_dict_in_output(self, registry, aggregator) -> None:
        await _populate(registry)
        result = await aggregator.compute(registry)
        assert "solar-01" in result.assets
        assert "battery-01" in result.assets


class TestAlerts:
    @pytest.mark.asyncio
    async def test_surplus_alert_when_generation_exceeds_threshold(self, registry, aggregator) -> None:
        await registry.update({
            "asset_id": "solar-01",
            "asset_type": "solar_inverter",
            "state": "RUNNING",
            "power_output_kw": 100.0,
        })
        result = await aggregator.compute(registry)
        codes = [a["code"] for a in result.alerts]
        assert "GRID_SURPLUS" in codes

    @pytest.mark.asyncio
    async def test_deficit_alert_when_consumption_exceeds_generation(self, registry, aggregator) -> None:
        await registry.update({
            "asset_id": "boiler-01",
            "asset_type": "industrial_boiler",
            "state": "RUNNING",
            "power_kw": 100.0,
        })
        result = await aggregator.compute(registry)
        codes = [a["code"] for a in result.alerts]
        assert "GRID_DEFICIT" in codes

    @pytest.mark.asyncio
    async def test_fault_alert_for_faulted_asset(self, registry, aggregator) -> None:
        await registry.update({
            "asset_id": "solar-01",
            "asset_type": "solar_inverter",
            "state": "FAULT",
            "power_output_kw": 0.0,
        })
        result = await aggregator.compute(registry)
        codes = [a["code"] for a in result.alerts]
        assert "ASSET_FAULT" in codes

    @pytest.mark.asyncio
    async def test_no_alerts_when_balanced(self, registry, aggregator) -> None:
        await registry.update({
            "asset_id": "solar-01",
            "asset_type": "solar_inverter",
            "state": "RUNNING",
            "power_output_kw": 50.0,
        })
        await registry.update({
            "asset_id": "boiler-01",
            "asset_type": "industrial_boiler",
            "state": "RUNNING",
            "power_kw": 50.0,
        })
        result = await aggregator.compute(registry)
        assert result.alerts == []
