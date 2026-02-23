"""Battery module entry point."""

import asyncio
import os
import signal

import structlog

from iot_edge_base.asset import AssetState
from iot_edge_base.client import BaseEdgeClient, DirectMethodRequest, DirectMethodResponse, create_client
from src.battery import BatteryStorage, BatteryState

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()

ASSET_ID = os.environ.get("ASSET_ID", "battery-01")
CAPACITY_KWH = float(os.environ.get("CAPACITY_KWH", "500.0"))
MAX_POWER_KW = float(os.environ.get("MAX_POWER_KW", "100.0"))
TELEMETRY_INTERVAL_S = int(os.environ.get("TELEMETRY_INTERVAL_S", "10"))


async def register_handlers(client: BaseEdgeClient, battery: BatteryStorage) -> None:
    async def handle_method(request: DirectMethodRequest) -> DirectMethodResponse:
        logger.info("Direct method received", method=request.name, payload=request.payload)
        try:
            if request.name == "start_charging":
                power_kw = float(request.payload.get("power_kw", MAX_POWER_KW))
                await battery.start_charging(power_kw)
                payload = {"status": "charging", "power_kw": power_kw}

            elif request.name == "start_discharging":
                power_kw = float(request.payload.get("power_kw", MAX_POWER_KW))
                await battery.start_discharging(power_kw)
                payload = {"status": "discharging", "power_kw": power_kw}

            elif request.name == "stop":
                await battery.stop()
                payload = {"status": "stopped"}

            elif request.name == "reset":
                await battery.reset()
                payload = {"status": "reset"}

            else:
                return DirectMethodResponse(request.request_id, 404, {"error": f"Unknown method: {request.name}"})

            return DirectMethodResponse(request.request_id, 200, payload)

        except RuntimeError as exc:
            return DirectMethodResponse(request.request_id, 409, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Method handler error", method=request.name)
            return DirectMethodResponse(request.request_id, 500, {"error": str(exc)})

    async def handle_twin_update(patch: dict) -> None:
        if "max_power_kw" in patch:
            battery.max_power_kw = float(patch["max_power_kw"])

    client.on_method(handle_method)
    client.on_twin_update(handle_twin_update)


async def telemetry_loop(client: BaseEdgeClient, battery: BatteryStorage) -> None:
    while True:
        try:
            battery.tick(elapsed_s=TELEMETRY_INTERVAL_S)
            telemetry = battery.get_telemetry()
            await client.send_message_to_output(telemetry.to_dict(), output_name="telemetry")
            await client.update_reported_properties(
                {"state": telemetry.state, "state_of_charge": telemetry.state_of_charge}
            )
        except Exception:
            logger.exception("Telemetry loop error")
        await asyncio.sleep(TELEMETRY_INTERVAL_S)


async def main() -> None:
    client = create_client()
    battery = BatteryStorage(asset_id=ASSET_ID, capacity_kwh=CAPACITY_KWH, max_power_kw=MAX_POWER_KW)

    await client.connect()
    await register_handlers(client, battery)
    await client.update_reported_properties(
        {"asset_id": ASSET_ID, "asset_type": "battery_storage", "capacity_kwh": CAPACITY_KWH}
    )

    logger.info("Battery module ready", asset_id=ASSET_ID)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    telemetry_task = asyncio.create_task(telemetry_loop(client, battery))
    await stop_event.wait()
    telemetry_task.cancel()
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
