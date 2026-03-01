"""Controller module entry point.

Receives telemetry from all asset modules via edgeHub message routing,
aggregates grid metrics, applies basic balancing logic, and forwards
a summary payload to Azure IoT Hub ($upstream).
"""

import asyncio
import signal

import structlog

from iot_edge_base.client import create_client
from iot_edge_base.retry import with_retry
from src.aggregator import Aggregator
from src.config import ControllerConfig
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


async def balancing_loop(
    registry: AssetRegistry,
    aggregator: Aggregator,
    dispatcher: CommandDispatcher,
    battery_module_id: str,
    interval_s: int,
) -> None:
    """Periodically aggregate telemetry, run balancing logic, report to cloud."""
    while True:
        try:
            telemetry = await aggregator.compute(registry)
            await _apply_balancing(telemetry, dispatcher, battery_module_id)
            logger.info(
                "Grid report",
                generation_kw=telemetry.total_generation_kw,
                consumption_kw=telemetry.total_consumption_kw,
                balance_kw=telemetry.grid_balance_kw,
                alerts=len(telemetry.alerts),
            )
        except Exception:
            logger.exception("Balancing loop error")

        await asyncio.sleep(interval_s)


async def _apply_balancing(
    telemetry,
    dispatcher: CommandDispatcher,
    battery_module_id: str,
) -> None:
    """Simple rule-based grid balancing.

    Rules:
      - Surplus > threshold: increase battery charging rate
      - Deficit > threshold: request battery discharge
      - Asset in FAULT: log critical alert (escalation handled externally)
    """
    balance = telemetry.grid_balance_kw

    for alert in telemetry.alerts:
        if alert["code"] == "GRID_SURPLUS":
            logger.info("Balancing: surplus detected, increasing battery charge", balance_kw=balance)
            try:
                await dispatcher.charge_battery(battery_module_id, power_kw=min(abs(balance), 50.0))
            except RuntimeError:
                logger.warning("Could not command battery to charge")

        elif alert["code"] == "GRID_DEFICIT":
            logger.info("Balancing: deficit detected, requesting battery discharge", balance_kw=balance)
            try:
                await dispatcher.discharge_battery(battery_module_id, power_kw=min(abs(balance), 50.0))
            except RuntimeError:
                logger.warning("Could not command battery to discharge")

        elif alert["code"] == "ASSET_FAULT":
            logger.error(
                "CRITICAL ALERT: asset fault",
                asset_id=alert["asset_id"],
                severity=alert["severity"],
            )


async def main() -> None:
    cfg = ControllerConfig()

    client = create_client()
    await client.connect()

    registry = AssetRegistry()
    aggregator = Aggregator(device_id=cfg.device_id, surplus_threshold_kw=cfg.surplus_threshold_kw)
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
                await with_retry(
                    lambda: client.send_message_to_output(telemetry.to_dict(), output_name="cloud"),
                    label="controller.send_cloud",
                )
                await with_retry(
                    lambda: client.update_reported_properties(
                        {
                            "asset_count": telemetry.asset_count,
                            "grid_balance_kw": telemetry.grid_balance_kw,
                            "active_alerts": len(telemetry.alerts),
                        }
                    ),
                    label="controller.update_twin",
                )
            except Exception:
                logger.exception("Cloud reporting error")
            await asyncio.sleep(cfg.reporting_interval_s)

    client.on_message(handle_asset_telemetry)

    await client.update_reported_properties({"device_id": cfg.device_id, "role": "controller"})
    logger.info("Controller module ready", device_id=cfg.device_id)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    tasks = [
        asyncio.create_task(cloud_reporting_loop()),
        asyncio.create_task(
            balancing_loop(registry, aggregator, dispatcher, cfg.battery_module_id, cfg.reporting_interval_s)
        ),
    ]

    await stop_event.wait()
    for task in tasks:
        task.cancel()
    await client.disconnect()
    logger.info("Controller module shut down gracefully")


if __name__ == "__main__":
    asyncio.run(main())
