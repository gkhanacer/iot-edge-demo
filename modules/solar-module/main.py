"""Solar module entry point.

Connects to IoT Edge Hub, registers command handlers, and runs the
telemetry publish loop. Controlled via environment variables.
"""

import asyncio
import os
import signal

import structlog

from iot_edge_base.asset import AssetState
from iot_edge_base.client import BaseEdgeClient, DirectMethodRequest, DirectMethodResponse, create_client
from src.inverter import SolarInverter
from src.simulator import get_irradiance

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()

ASSET_ID = os.environ.get("ASSET_ID", "solar-01")
MAX_POWER_KW = float(os.environ.get("MAX_POWER_KW", "100.0"))
TELEMETRY_INTERVAL_S = int(os.environ.get("TELEMETRY_INTERVAL_S", "10"))


async def register_handlers(client: BaseEdgeClient, inverter: SolarInverter) -> None:
    async def handle_method(request: DirectMethodRequest) -> DirectMethodResponse:
        logger.info("Direct method received", method=request.name, payload=request.payload)
        try:
            if request.name == "start":
                await inverter.start()
                payload = {"status": "started"}

            elif request.name == "stop":
                await inverter.stop()
                payload = {"status": "stopped"}

            elif request.name == "set_output":
                target_kw = float(request.payload["target_kw"])
                await inverter.set_output(target_kw)
                payload = {"status": "ok", "target_kw": target_kw}

            elif request.name == "reset":
                await inverter.reset()
                payload = {"status": "reset"}

            else:
                return DirectMethodResponse(request.request_id, 404, {"error": f"Unknown method: {request.name}"})

            return DirectMethodResponse(request.request_id, 200, payload)

        except RuntimeError as exc:
            logger.warning("Method rejected", method=request.name, reason=str(exc))
            return DirectMethodResponse(request.request_id, 409, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Method handler error", method=request.name)
            return DirectMethodResponse(request.request_id, 500, {"error": str(exc)})

    async def handle_twin_update(patch: dict) -> None:
        logger.info("Twin desired properties updated", patch=patch)
        if "max_power_kw" in patch:
            inverter.max_power_kw = float(patch["max_power_kw"])
            logger.info("max_power_kw updated", value=inverter.max_power_kw)

    client.on_method(handle_method)
    client.on_twin_update(handle_twin_update)


async def telemetry_loop(client: BaseEdgeClient, inverter: SolarInverter) -> None:
    while True:
        try:
            irradiance = get_irradiance()
            telemetry = inverter.get_telemetry(irradiance_w_m2=irradiance)
            await client.send_message_to_output(telemetry.to_dict(), output_name="telemetry")

            await client.update_reported_properties(
                {"state": telemetry.state, "power_output_kw": telemetry.power_output_kw}
            )
            logger.debug("Telemetry published", power_kw=telemetry.power_output_kw, state=telemetry.state)

        except Exception:
            logger.exception("Telemetry loop error")

        await asyncio.sleep(TELEMETRY_INTERVAL_S)


async def main() -> None:
    client = create_client()
    inverter = SolarInverter(asset_id=ASSET_ID, max_power_kw=MAX_POWER_KW)

    await client.connect()
    await register_handlers(client, inverter)

    await client.update_reported_properties(
        {
            "asset_id": ASSET_ID,
            "asset_type": "solar_inverter",
            "max_power_kw": MAX_POWER_KW,
            "state": AssetState.IDLE,
        }
    )

    logger.info("Solar module ready", asset_id=ASSET_ID, max_power_kw=MAX_POWER_KW)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    telemetry_task = asyncio.create_task(telemetry_loop(client, inverter))

    await stop_event.wait()
    telemetry_task.cancel()
    await inverter.stop()
    await client.disconnect()
    logger.info("Solar module shut down gracefully")


if __name__ == "__main__":
    asyncio.run(main())
