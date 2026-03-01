"""Solar module entry point."""

import asyncio
import signal
from typing import Awaitable, Callable

import structlog

from iot_edge_base.asset import AssetState
from iot_edge_base.client import BaseEdgeClient, DirectMethodRequest, DirectMethodResponse, create_client
from pydantic import ValidationError

from iot_edge_base.retry import with_retry
from src.config import SolarConfig
from src.inverter import SolarInverter
from src.schemas import SetOutputPayload
from src.simulator import get_irradiance

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()


def _build_dispatch(inverter: SolarInverter) -> dict[str, Callable[[dict], Awaitable[None]]]:
    """Map method names to async handlers. Add new methods here — no if/elif needed."""
    async def _start(_: dict) -> None:
        await inverter.start()

    async def _stop(_: dict) -> None:
        await inverter.stop()

    async def _reset(_: dict) -> None:
        await inverter.reset()

    async def _set_output(payload: dict) -> None:
        parsed = SetOutputPayload.model_validate(payload)
        await inverter.set_output(parsed.target_kw)

    return {"start": _start, "stop": _stop, "reset": _reset, "set_output": _set_output}


async def register_handlers(client: BaseEdgeClient, inverter: SolarInverter) -> None:
    dispatch = _build_dispatch(inverter)

    async def handle_method(request: DirectMethodRequest) -> DirectMethodResponse:
        logger.info("Direct method received", method=request.name)
        handler = dispatch.get(request.name)
        if handler is None:
            return DirectMethodResponse(request.request_id, 404, {"error": f"Unknown method: {request.name}"})
        try:
            await handler(request.payload)
            return DirectMethodResponse(request.request_id, 200, {"status": "ok"})
        except ValidationError as exc:
            logger.warning("Invalid payload", method=request.name, reason=str(exc))
            return DirectMethodResponse(request.request_id, 400, {"error": exc.errors()})
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

    client.on_method(handle_method)
    client.on_twin_update(handle_twin_update)


async def telemetry_loop(client: BaseEdgeClient, inverter: SolarInverter, interval_s: int) -> None:
    while True:
        try:
            irradiance = get_irradiance()
            telemetry = inverter.get_telemetry(irradiance_w_m2=irradiance)
            await with_retry(
                lambda: client.send_message_to_output(telemetry.to_dict(), output_name="telemetry"),
                label="solar.send_telemetry",
            )
            await with_retry(
                lambda: client.update_reported_properties(
                    {"state": telemetry.state, "power_output_kw": telemetry.power_output_kw}
                ),
                label="solar.update_twin",
            )
            logger.debug("Telemetry published", power_kw=telemetry.power_output_kw, state=telemetry.state)
        except Exception:
            logger.exception("Telemetry loop error")
        await asyncio.sleep(interval_s)


async def main() -> None:
    cfg = SolarConfig()

    client = create_client()
    inverter = SolarInverter(
        asset_id=cfg.asset_id,
        max_power_kw=cfg.max_power_kw,
        startup_delay_s=cfg.startup_delay_s,
    )

    await client.connect()
    await register_handlers(client, inverter)
    await client.update_reported_properties(
        {
            "asset_id": cfg.asset_id,
            "asset_type": "solar_inverter",
            "max_power_kw": cfg.max_power_kw,
            "state": AssetState.IDLE,
        }
    )
    logger.info("Solar module ready", asset_id=cfg.asset_id, max_power_kw=cfg.max_power_kw)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    telemetry_task = asyncio.create_task(telemetry_loop(client, inverter, cfg.telemetry_interval_s))
    await stop_event.wait()
    telemetry_task.cancel()
    await inverter.stop()
    await client.disconnect()
    logger.info("Solar module shut down gracefully")


if __name__ == "__main__":
    asyncio.run(main())
