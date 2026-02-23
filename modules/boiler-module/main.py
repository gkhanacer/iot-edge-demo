"""Boiler module entry point."""

import asyncio
import os
import signal

import structlog

from iot_edge_base.asset import AssetState
from iot_edge_base.client import BaseEdgeClient, DirectMethodRequest, DirectMethodResponse, create_client
from src.boiler import Boiler

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()

ASSET_ID = os.environ.get("ASSET_ID", "boiler-01")
MAX_POWER_KW = float(os.environ.get("MAX_POWER_KW", "200.0"))
DEFAULT_TARGET_C = float(os.environ.get("DEFAULT_TARGET_C", "80.0"))
TELEMETRY_INTERVAL_S = int(os.environ.get("TELEMETRY_INTERVAL_S", "10"))


async def register_handlers(client: BaseEdgeClient, boiler: Boiler) -> None:
    async def handle_method(request: DirectMethodRequest) -> DirectMethodResponse:
        logger.info("Direct method received", method=request.name, payload=request.payload)
        try:
            if request.name == "start":
                await boiler.start()
                payload = {"status": "started"}

            elif request.name == "stop":
                await boiler.stop()
                payload = {"status": "stopped"}

            elif request.name == "set_temperature":
                target = float(request.payload["target_celsius"])
                await boiler.set_temperature(target)
                payload = {"status": "ok", "target_celsius": target}

            elif request.name == "reset":
                await boiler.reset()
                payload = {"status": "reset"}

            else:
                return DirectMethodResponse(request.request_id, 404, {"error": f"Unknown method: {request.name}"})

            return DirectMethodResponse(request.request_id, 200, payload)

        except (RuntimeError, ValueError) as exc:
            return DirectMethodResponse(request.request_id, 409, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Method handler error")
            return DirectMethodResponse(request.request_id, 500, {"error": str(exc)})

    async def handle_twin_update(patch: dict) -> None:
        if "default_target_c" in patch and boiler.state == AssetState.RUNNING:
            await boiler.set_temperature(float(patch["default_target_c"]))

    client.on_method(handle_method)
    client.on_twin_update(handle_twin_update)


async def telemetry_loop(client: BaseEdgeClient, boiler: Boiler) -> None:
    while True:
        try:
            boiler.tick(elapsed_s=TELEMETRY_INTERVAL_S)
            telemetry = boiler.get_telemetry()
            await client.send_message_to_output(telemetry.to_dict(), output_name="telemetry")
            await client.update_reported_properties(
                {"state": telemetry.state, "current_temperature_c": telemetry.current_temperature_c}
            )
        except Exception:
            logger.exception("Telemetry loop error")
        await asyncio.sleep(TELEMETRY_INTERVAL_S)


async def main() -> None:
    client = create_client()
    boiler = Boiler(asset_id=ASSET_ID, max_power_kw=MAX_POWER_KW, default_target_c=DEFAULT_TARGET_C)

    await client.connect()
    await register_handlers(client, boiler)
    await client.update_reported_properties(
        {"asset_id": ASSET_ID, "asset_type": "industrial_boiler", "max_power_kw": MAX_POWER_KW}
    )

    logger.info("Boiler module ready", asset_id=ASSET_ID)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    telemetry_task = asyncio.create_task(telemetry_loop(client, boiler))
    await stop_event.wait()
    telemetry_task.cancel()
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
