"""Unit tests for AzureMonitorExporter callback logic.

The OTel SDK and Azure Monitor exporter are mocked so tests run without
a real Application Insights connection string.
"""

from unittest.mock import MagicMock, patch


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


def _make_exporter():
    """Create AzureMonitorExporter with all OTel I/O mocked out."""
    with patch("src.exporter.AzureMonitorMetricExporter"), \
         patch("src.exporter.PeriodicExportingMetricReader"), \
         patch("src.exporter.MeterProvider"), \
         patch("src.exporter.metrics"):
        from src.exporter import AzureMonitorExporter
        exporter = AzureMonitorExporter(connection_string="InstrumentationKey=00000000-0000-0000-0000-000000000000")
    return exporter


class TestUpdate:
    def test_update_stores_snapshot(self):
        exporter = _make_exporter()
        exporter.update(MOCK_SNAPSHOT)
        assert exporter._latest == MOCK_SNAPSHOT

    def test_update_replaces_previous(self):
        exporter = _make_exporter()
        exporter.update({"grid_balance_kw": 1.0})
        exporter.update({"grid_balance_kw": 2.0})
        assert exporter._latest["grid_balance_kw"] == 2.0


class TestCallbacks:
    def setup_method(self):
        self.exporter = _make_exporter()
        self.exporter.update(MOCK_SNAPSHOT)
        self.options = MagicMock()

    def test_grid_balance_returns_correct_value(self):
        obs = self.exporter._cb_grid_balance(self.options)
        assert len(obs) == 1
        assert obs[0].value == 2.5

    def test_grid_balance_includes_device_id_attribute(self):
        obs = self.exporter._cb_grid_balance(self.options)
        assert obs[0].attributes["device_id"] == "test-device"

    def test_generation_returns_correct_value(self):
        obs = self.exporter._cb_generation(self.options)
        assert obs[0].value == 5.0

    def test_consumption_returns_correct_value(self):
        obs = self.exporter._cb_consumption(self.options)
        assert obs[0].value == 2.5

    def test_alerts_returns_alert_count(self):
        obs = self.exporter._cb_alerts(self.options)
        assert obs[0].value == 1.0

    def test_alerts_zero_when_no_alerts(self):
        self.exporter.update({"alerts": []})
        obs = self.exporter._cb_alerts(self.options)
        assert obs[0].value == 0.0

    def test_asset_power_returns_one_observation_per_asset(self):
        obs = self.exporter._cb_asset_power(self.options)
        assert len(obs) == 2

    def test_asset_power_includes_labels(self):
        obs = self.exporter._cb_asset_power(self.options)
        asset_ids = {o.attributes["asset_id"] for o in obs}
        assert "solar-1" in asset_ids
        assert "boiler-1" in asset_ids

    def test_asset_power_empty_when_no_assets(self):
        self.exporter.update({"assets": {}})
        obs = self.exporter._cb_asset_power(self.options)
        assert obs == []

    def test_defaults_when_snapshot_empty(self):
        self.exporter.update({})
        assert self.exporter._cb_grid_balance(self.options)[0].value == 0.0
        assert self.exporter._cb_generation(self.options)[0].value == 0.0
        assert self.exporter._cb_consumption(self.options)[0].value == 0.0
        assert self.exporter._cb_alerts(self.options)[0].value == 0.0
