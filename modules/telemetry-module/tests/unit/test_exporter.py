"""Unit tests for AzureMonitorExporter callback logic.

AzureMonitorExporter bridges the controller's aggregated grid snapshot to
Azure Monitor via the OpenTelemetry SDK. It registers observable gauge
callbacks that are invoked periodically by the OTel MeterProvider.

Callback contract (OTel observable gauge pattern):
    def _cb_*(self, options) -> list[Observation]:
        Each callback receives an `options` argument (from the OTel SDK) and
        returns a list of Observation objects. Each Observation carries a numeric
        value and an attributes dict (used as Azure Monitor dimensions).

Mocking strategy:
    azure.monitor.opentelemetry.exporter and the opentelemetry SDK packages
    are production-only dependencies not installed in the dev environment.
    They are injected into sys.modules as MagicMocks at module import time
    — before src.exporter is first loaded — so the module-level imports in
    src/exporter.py resolve to our mocks instead of raising ModuleNotFoundError.

    The Observation class is provided as a lightweight @dataclass stub so that
    assertions on .value and .attributes work correctly without the real SDK.

Test data (MOCK_SNAPSHOT):
    Represents one reporting cycle from the controller:
        grid_balance_kw      =  2.5 kW
        total_generation_kw  =  5.0 kW
        total_consumption_kw =  2.5 kW
        alerts               = ["GRID_SURPLUS"]  (1 alert)
        assets               = 2 assets (solar-1, boiler-1)
"""

import sys
from dataclasses import dataclass, field
from unittest.mock import MagicMock


# ── SDK stubs ─────────────────────────────────────────────────────────────────

@dataclass
class _Observation:
    """Minimal stub for opentelemetry.metrics.Observation.

    The real Observation is unavailable without the SDK package.
    This stub provides the same interface (.value, .attributes) that the
    exporter callbacks construct and the tests inspect.
    """
    value: float
    attributes: dict = field(default_factory=dict)


# ── sys.modules injection ─────────────────────────────────────────────────────
# src/exporter.py imports azure and opentelemetry packages at module level.
# Both are production-only dependencies absent in the test environment.
# Injecting mocks here (before any import of src.exporter) makes those
# imports resolve to our MagicMocks instead of raising ModuleNotFoundError.
#
# _otel_metrics_mock.Observation is set to _Observation (a real class) so
# the callbacks produce objects whose .value and .attributes are inspectable.

_otel_metrics_mock = MagicMock()
_otel_metrics_mock.Observation = _Observation

_MODULE_MOCKS = {
    "azure": MagicMock(),
    "azure.monitor": MagicMock(),
    "azure.monitor.opentelemetry": MagicMock(),
    "azure.monitor.opentelemetry.exporter": MagicMock(),
    "opentelemetry": MagicMock(),
    "opentelemetry.metrics": _otel_metrics_mock,
    "opentelemetry.sdk": MagicMock(),
    "opentelemetry.sdk.metrics": MagicMock(),
    "opentelemetry.sdk.metrics.export": MagicMock(),
}

# setdefault: only inject if the real package is absent — a real installation
# takes precedence and no test behaviour changes.
for _name, _mock in _MODULE_MOCKS.items():
    sys.modules.setdefault(_name, _mock)


# ── Test data ─────────────────────────────────────────────────────────────────
# Represents one controller reporting cycle. Callbacks extract individual
# metrics from this dict using .get() with 0 as the default.

MOCK_SNAPSHOT = {
    "device_id": "test-device",
    "grid_balance_kw": 2.5,
    "total_generation_kw": 5.0,
    "total_consumption_kw": 2.5,
    "alerts": ["GRID_SURPLUS"],
    "assets": {
        "solar-1": {"power_kw": 5.0, "asset_type": "solar", "state": "RUNNING"},
        "boiler-1": {"power_kw": -2.5, "asset_type": "boiler", "state": "RUNNING"},
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_exporter():
    """Create AzureMonitorExporter with all OTel/Azure calls resolving to MagicMocks.

    sys.modules already has the mocks in place, so this is a straightforward
    import + construction. All SDK calls in __init__ (MeterProvider, reader,
    meter.create_observable_gauge …) return MagicMocks and have no side effects.
    """
    from src.exporter import AzureMonitorExporter
    return AzureMonitorExporter(connection_string="InstrumentationKey=00000000-0000-0000-0000-000000000000")


# ── TestUpdate ────────────────────────────────────────────────────────────────
# update() is called by the telemetry loop whenever a new grid snapshot arrives.
# It stores the snapshot so the OTel callbacks can read it on the next export cycle.

class TestUpdate:
    def test_update_stores_snapshot(self):
        # The snapshot must be available on _latest immediately after update().
        exporter = _make_exporter()
        exporter.update(MOCK_SNAPSHOT)
        assert exporter._latest == MOCK_SNAPSHOT

    def test_update_replaces_previous(self):
        # Subsequent snapshots overwrite the previous one — only the most
        # recent reading should be exported to Azure Monitor.
        exporter = _make_exporter()
        exporter.update({"grid_balance_kw": 1.0})
        exporter.update({"grid_balance_kw": 2.0})
        assert exporter._latest["grid_balance_kw"] == 2.0


# ── TestCallbacks ─────────────────────────────────────────────────────────────
# Each _cb_*() method is an OTel observable gauge callback. The OTel SDK calls
# them periodically; tests call them directly with a MagicMock options argument.
# setup_method() (pytest equivalent of setUp) pre-loads MOCK_SNAPSHOT so each
# test starts from a consistent state.

class TestCallbacks:
    def setup_method(self):
        self.exporter = _make_exporter()
        self.exporter.update(MOCK_SNAPSHOT)
        # options is passed by the OTel SDK; the callbacks don't use it directly,
        # so a MagicMock is sufficient to satisfy the function signature.
        self.options = MagicMock()

    def test_grid_balance_returns_correct_value(self):
        # Each callback must return exactly one Observation for scalar metrics.
        obs = self.exporter._cb_grid_balance(self.options)
        assert len(obs) == 1
        assert obs[0].value == 2.5

    def test_grid_balance_includes_device_id_attribute(self):
        # device_id is sent as a dimension so Azure Monitor queries can filter
        # by edge device when multiple devices report to the same workspace.
        obs = self.exporter._cb_grid_balance(self.options)
        assert obs[0].attributes["device_id"] == "test-device"

    def test_generation_returns_correct_value(self):
        obs = self.exporter._cb_generation(self.options)
        assert obs[0].value == 5.0

    def test_consumption_returns_correct_value(self):
        obs = self.exporter._cb_consumption(self.options)
        assert obs[0].value == 2.5

    def test_alerts_returns_alert_count(self):
        # Alerts are reported as a count gauge so Azure Monitor alert rules
        # can trigger on "active_alerts > 0".
        obs = self.exporter._cb_alerts(self.options)
        assert obs[0].value == 1.0

    def test_alerts_zero_when_no_alerts(self):
        # Replacing the snapshot with an empty alerts list must produce 0.
        # This confirms the callback uses len() rather than a truthy check.
        self.exporter.update({"alerts": []})
        obs = self.exporter._cb_alerts(self.options)
        assert obs[0].value == 0.0

    def test_asset_power_returns_one_observation_per_asset(self):
        # Unlike scalar callbacks, _cb_asset_power returns one Observation per
        # asset so each asset's power appears as a separate metric series.
        obs = self.exporter._cb_asset_power(self.options)
        assert len(obs) == 2

    def test_asset_power_includes_labels(self):
        # Each Observation must carry asset_id as a dimension attribute so
        # Azure Monitor can distinguish solar from boiler power in the same metric.
        obs = self.exporter._cb_asset_power(self.options)
        asset_ids = {o.attributes["asset_id"] for o in obs}
        assert "solar-1" in asset_ids
        assert "boiler-1" in asset_ids

    def test_asset_power_empty_when_no_assets(self):
        # No assets → no observations. Confirms the callback handles an empty
        # assets dict without raising and doesn't emit phantom data points.
        self.exporter.update({"assets": {}})
        obs = self.exporter._cb_asset_power(self.options)
        assert obs == []

    def test_defaults_when_snapshot_empty(self):
        # If the controller hasn't reported yet (snapshot = {}), all callbacks
        # must return 0 via .get(key, 0) — no KeyError, no data loss.
        self.exporter.update({})
        assert self.exporter._cb_grid_balance(self.options)[0].value == 0.0
        assert self.exporter._cb_generation(self.options)[0].value == 0.0
        assert self.exporter._cb_consumption(self.options)[0].value == 0.0
        assert self.exporter._cb_alerts(self.options)[0].value == 0.0
