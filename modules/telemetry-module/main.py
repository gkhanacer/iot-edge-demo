"""Telemetry module entry point.

Listens for aggregated telemetry from the controller-module and
exports it as OpenTelemetry metrics to Azure Monitor.

Required environment variable:
  AZURE_MONITOR_CONNECTION_STRING — Application Insights connection string
"""

import asyncio
import os
import signal

import structlog

from iot_edge_base.client import create_client
from src.exporter import AzureMonitorExporter

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()


async def main() -> None:
    connection_string = os.environ.get("AZURE_MONITOR_CONNECTION_STRING", "")

    client = create_client()
    await client.connect()

    exporter: AzureMonitorExporter | None = None
    if connection_string:
        exporter = AzureMonitorExporter(connection_string=connection_string)
    else:
        logger.warning("AZURE_MONITOR_CONNECTION_STRING not set — metrics export disabled")

    async def handle_controller_telemetry(data: dict, input_name: str) -> None:
        if exporter:
            exporter.update(data)
        logger.debug(
            "Metrics forwarded to Azure Monitor",
            grid_balance_kw=data.get("grid_balance_kw"),
            asset_count=data.get("asset_count"),
            alerts=len(data.get("alerts", [])),
        )

    client.on_message(handle_controller_telemetry)

    logger.info("Telemetry module ready")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    await stop_event.wait()
    await client.disconnect()
    logger.info("Telemetry module shut down gracefully")


if __name__ == "__main__":
    asyncio.run(main())
