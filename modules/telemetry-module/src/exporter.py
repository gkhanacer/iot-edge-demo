"""Azure Monitor metrics exporter.

Receives aggregated telemetry from the controller-module and exports
it as OpenTelemetry metrics to Azure Monitor (Application Insights /
Log Analytics via Azure Monitor OpenTelemetry Exporter).

Metrics exported:
  edge.grid.balance_kw        — net grid balance in kW
  edge.grid.generation_kw     — total generation across all assets
  edge.grid.consumption_kw    — total consumption across all assets
  edge.grid.active_alerts     — number of active alerts
  edge.asset.power_kw         — per-asset power (labelled by asset_id, asset_type)
"""

import os

import structlog
from azure.monitor.opentelemetry.exporter import AzureMonitorMetricExporter
from opentelemetry import metrics
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

logger = structlog.get_logger()

EXPORT_INTERVAL_MS = int(os.environ.get("EXPORT_INTERVAL_MS", "30000"))


class AzureMonitorExporter:
    """Wraps the Azure Monitor OTel exporter with domain-specific metric definitions.

    Uses ObservableGauge for all metrics — each gauge callback reads from
    `_latest`, which is updated on every telemetry message received.
    """

    def __init__(self, connection_string: str) -> None:
        self._latest: dict = {}

        otel_exporter = AzureMonitorMetricExporter(connection_string=connection_string)
        reader = PeriodicExportingMetricReader(
            otel_exporter, export_interval_millis=EXPORT_INTERVAL_MS
        )
        provider = MeterProvider(metric_readers=[reader])
        metrics.set_meter_provider(provider)

        meter = metrics.get_meter("energy-edge-controller", "0.1.0")

        # Grid-level gauges
        meter.create_observable_gauge(
            name="edge.grid.balance_kw",
            callbacks=[self._cb_grid_balance],
            description="Net grid balance (generation - consumption) in kW",
            unit="kW",
        )
        meter.create_observable_gauge(
            name="edge.grid.generation_kw",
            callbacks=[self._cb_generation],
            description="Total energy generation across all assets in kW",
            unit="kW",
        )
        meter.create_observable_gauge(
            name="edge.grid.consumption_kw",
            callbacks=[self._cb_consumption],
            description="Total energy consumption across all assets in kW",
            unit="kW",
        )
        meter.create_observable_gauge(
            name="edge.grid.active_alerts",
            callbacks=[self._cb_alerts],
            description="Number of active grid or asset alerts",
        )
        # Per-asset gauge
        meter.create_observable_gauge(
            name="edge.asset.power_kw",
            callbacks=[self._cb_asset_power],
            description="Net power of an individual asset in kW",
            unit="kW",
        )

        logger.info("Azure Monitor exporter initialised", export_interval_ms=EXPORT_INTERVAL_MS)

    def update(self, data: dict) -> None:
        """Update the latest snapshot. Called on each incoming telemetry message."""
        self._latest = data

    # ── Observable callbacks ─────────────────────────────────────────────────

    def _cb_grid_balance(self, options) -> list[Observation]:
        device_id = self._latest.get("device_id", "unknown")
        return [Observation(self._latest.get("grid_balance_kw", 0.0), {"device_id": device_id})]

    def _cb_generation(self, options) -> list[Observation]:
        return [Observation(self._latest.get("total_generation_kw", 0.0))]

    def _cb_consumption(self, options) -> list[Observation]:
        return [Observation(self._latest.get("total_consumption_kw", 0.0))]

    def _cb_alerts(self, options) -> list[Observation]:
        return [Observation(float(len(self._latest.get("alerts", []))))]

    def _cb_asset_power(self, options) -> list[Observation]:
        observations = []
        for asset_id, asset in self._latest.get("assets", {}).items():
            observations.append(
                Observation(
                    asset.get("power_kw", 0.0),
                    {
                        "asset_id": asset_id,
                        "asset_type": asset.get("asset_type", "unknown"),
                        "state": asset.get("state", "UNKNOWN"),
                    },
                )
            )
        return observations
