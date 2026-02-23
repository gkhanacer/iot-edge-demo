"""Controller module entry point.

Receives telemetry from all asset modules via edgeHub message routing,
aggregates grid metrics, applies basic balancing logic, and forwards
a summary payload to Azure IoT Hub ($upstream).
"""

import asyncio
import os
import signal

import structlog

from iot_edge_base.client import create_client
from src.aggregator import Aggregator
from src.dispatcher import CommandDispatcher
from src.registry import AssetRegistry

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()

DEVICE_ID = os.environ.get("IOTEDGE_DEVICEID", "edge-device-01")
REPORTING_INTERVAL_S = int(os.environ.get("REPORTING_INTERVAL_S", "30"))
SURPLUS_THRESHOLD_KW = float(os.environ.get("SURPLUS_THRESHOLD_KW", "10.0"))

# Module IDs of connected asset modules (must match deployment manifest)
SOLAR_MODULE_ID = os.environ.get("SOLAR_MODULE_ID", "solar-module")
BATTERY_MODULE_ID = os.environ.get("BATTERY_MODULE_ID", "battery-module")
BOILER_MODULE_ID = os.environ.get("BOILER_MODULE_ID", "boiler-module")


async def balancing_loop(
    registry: AssetRegistry,
    aggregator: Aggregator,
    dispatcher: CommandDispatcher,
) -> None:
    """Periodically aggregate telemetry, run balancing logic, report to cloud."""
    while True:
        try:
            telemetry = await aggregator.compute(registry)
            await _apply_balancing(telemetry, registry, dispatcher)

            # Forward to IoT Hub via $upstream
            from iot_edge_base.client import BaseEdgeClient  # avoid circular at module level

            logger.info(
                "Grid report",
                generation_kw=telemetry.total_generation_kw,
                consumption_kw=telemetry.total_consumption_kw,
                balance_kw=telemetry.grid_balance_kw,
                alerts=len(telemetry.alerts),
            )

        except Exception:
            logger.exception("Balancing loop error")

        await asyncio.sleep(REPORTING_INTERVAL_S)


async def _apply_balancing(telemetry, registry, dispatcher: CommandDispatcher) -> None:
    """Simple rule-based grid balancing.

    Rules:
      - Surplus > threshold: increase battery charging rate
      - Deficit > threshold: request battery discharge
      - Asset in FAULT: log critical alert (escalation handled externally)
    """
    balance = telemetry.grid_balance_kw
    battery = await registry.get(BATTERY_MODULE_ID.replace("-module", "-01"))  # asset_id convention

    for alert in telemetry.alerts:
        if alert["code"] == "GRID_SURPLUS":
            logger.info("Balancing: surplus detected, increasing battery charge", balance_kw=balance)
            try:
                await dispatcher.charge_battery(BATTERY_MODULE_ID, power_kw=min(abs(balance), 50.0))
            except RuntimeError:
                logger.warning("Could not command battery to charge")

        elif alert["code"] == "GRID_DEFICIT":
            logger.info("Balancing: deficit detected, requesting battery discharge", balance_kw=balance)
            try:
                await dispatcher.discharge_battery(BATTERY_MODULE_ID, power_kw=min(abs(balance), 50.0))
            except RuntimeError:
                logger.warning("Could not command battery to discharge")

        elif alert["code"] == "ASSET_FAULT":
            logger.error(
                "CRITICAL ALERT: asset fault",
                asset_id=alert["asset_id"],
                severity=alert["severity"],
            )


async def main() -> None:
    client = create_client()
    await client.connect()

    registry = AssetRegistry()
    aggregator = Aggregator(device_id=DEVICE_ID, surplus_threshold_kw=SURPLUS_THRESHOLD_KW)
    dispatcher = CommandDispatcher(client)

    # Register input message handler — receives telemetry from all asset modules
    async def handle_asset_telemetry(data: dict, input_name: str) -> None:
        await registry.update(data)
        logger.debug(
            "Telemetry received",
            asset_id=data.get("asset_id"),
            state=data.get("state"),
            input=input_name,
        )

    # Register cloud-reporting as a separate output loop
    async def cloud_reporting_loop() -> None:
        while True:
            try:
                telemetry = await aggregator.compute(registry)
                await client.send_message_to_output(telemetry.to_dict(), output_name="cloud")
                await client.update_reported_properties(
                    {
                        "asset_count": telemetry.asset_count,
                        "grid_balance_kw": telemetry.grid_balance_kw,
                        "active_alerts": len(telemetry.alerts),
                    }
                )
            except Exception:
                logger.exception("Cloud reporting error")
            await asyncio.sleep(REPORTING_INTERVAL_S)

    client.on_message(handle_asset_telemetry)

    await client.update_reported_properties({"device_id": DEVICE_ID, "role": "controller"})
    logger.info("Controller module ready", device_id=DEVICE_ID)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    tasks = [
        asyncio.create_task(cloud_reporting_loop()),
        asyncio.create_task(balancing_loop(registry, aggregator, dispatcher)),
    ]

    await stop_event.wait()
    for task in tasks:
        task.cancel()
    await client.disconnect()
    logger.info("Controller module shut down gracefully")


if __name__ == "__main__":
    asyncio.run(main())
