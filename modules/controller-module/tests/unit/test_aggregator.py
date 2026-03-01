"""Tests for Aggregator — grid balance computation and alert logic.

Aggregator.compute(registry) reads all AssetSnapshots and produces:

    generation  = sum of positive power_kw values (solar, discharging battery)
    consumption = sum of abs(negative power_kw) values (boiler, charging battery)
    balance     = generation - consumption

Alerts are raised when:
    balance >  surplus_threshold_kw  → GRID_SURPLUS
    balance < -surplus_threshold_kw  → GRID_DEFICIT
    any asset state == "FAULT"       → ASSET_FAULT (one alert per faulted asset)

The _populate() helper registers three assets with known power values:
    solar-01:   +100 kW (generation)
    battery-01: -30 kW  (charging = consumption; registry normalises sign)
    boiler-01:  -40 kW  (heating load = consumption; registry normalises sign)
    ─────────────────────────────────────────────────────────────
    total_generation  = 100 kW
    total_consumption =  70 kW  (30 + 40)
    grid_balance      =  30 kW  (surplus → above threshold=10 → GRID_SURPLUS alert)

The aggregator fixture uses surplus_threshold_kw=10.0.
"""

import pytest

from src.aggregator import Aggregator
from src.registry import AssetRegistry


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def registry() -> AssetRegistry:
    return AssetRegistry()


@pytest.fixture
def aggregator() -> Aggregator:
    # surplus_threshold_kw=10.0 means any imbalance beyond ±10 kW triggers
    # an alert. Tests that check "no alerts" keep balance within this window.
    return Aggregator(device_id="test-device", surplus_threshold_kw=10.0)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _populate(registry: AssetRegistry) -> None:
    """Load a balanced three-asset scenario into the registry.

    Raw power values (before registry normalisation):
        solar-01:   power_output_kw=100  → snapshot.power_kw = +100
        battery-01: power_kw=30 charging → snapshot.power_kw = -30
        boiler-01:  power_kw=40          → snapshot.power_kw = -40
    """
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


# ── TestAggregation ───────────────────────────────────────────────────────────
# Verifies the arithmetic of the grid balance calculation.

class TestAggregation:
    @pytest.mark.asyncio
    async def test_total_generation(self, registry, aggregator) -> None:
        # Only solar (100 kW, positive snapshot) contributes to generation.
        await _populate(registry)
        result = await aggregator.compute(registry)
        assert result.total_generation_kw == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_total_consumption(self, registry, aggregator) -> None:
        # Battery charging (30 kW) + boiler (40 kW) = 70 kW total consumption.
        await _populate(registry)
        result = await aggregator.compute(registry)
        assert result.total_consumption_kw == pytest.approx(70.0)  # 30 + 40

    @pytest.mark.asyncio
    async def test_grid_balance(self, registry, aggregator) -> None:
        # balance = generation - consumption = 100 - 70 = 30 kW (surplus).
        await _populate(registry)
        result = await aggregator.compute(registry)
        assert result.grid_balance_kw == pytest.approx(30.0)  # 100 - 70

    @pytest.mark.asyncio
    async def test_empty_registry_produces_zero_balance(self, registry, aggregator) -> None:
        # With no assets reporting, all metrics must be zero — the system
        # should not alert or report phantom power.
        result = await aggregator.compute(registry)
        assert result.grid_balance_kw == 0.0
        assert result.asset_count == 0

    @pytest.mark.asyncio
    async def test_assets_dict_in_output(self, registry, aggregator) -> None:
        # The assets dict in the output snapshot is used by the telemetry-module
        # to emit per-asset metrics to Azure Monitor.
        await _populate(registry)
        result = await aggregator.compute(registry)
        assert "solar-01" in result.assets
        assert "battery-01" in result.assets


# ── TestAlerts ────────────────────────────────────────────────────────────────
# Verifies the three alert conditions: GRID_SURPLUS, GRID_DEFICIT, ASSET_FAULT.

class TestAlerts:
    @pytest.mark.asyncio
    async def test_surplus_alert_when_generation_exceeds_threshold(self, registry, aggregator) -> None:
        # 100 kW solar with no consumption → balance = 100 kW >> threshold 10 kW.
        # GRID_SURPLUS should prompt the controller to charge the battery or
        # curtail solar output.
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
        # 100 kW boiler with no generation → balance = -100 kW << -threshold.
        # GRID_DEFICIT should prompt the controller to discharge the battery.
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
        # Any asset in FAULT state must produce an ASSET_FAULT alert.
        # This is independent of the power balance — it fires even when the
        # faulted asset reports 0 kW.
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
        # 50 kW solar = 50 kW boiler → balance = 0, well within ±10 kW threshold.
        # No power alerts should be generated.
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
