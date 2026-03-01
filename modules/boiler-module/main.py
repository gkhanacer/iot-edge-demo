"""Boiler module entry point."""

import asyncio
import signal
from typing import Awaitable, Callable

import structlog

from iot_edge_base.asset import AssetState
from iot_edge_base.client import BaseEdgeClient, DirectMethodRequest, DirectMethodResponse, create_client
from pydantic import ValidationError

from iot_edge_base.retry import with_retry
from src.boiler import Boiler
from src.config import BoilerConfig
from src.schemas import SetTemperaturePayload

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()


def _build_dispatch(boiler: Boiler) -> dict[str, Callable[[dict], Awaitable[None]]]:
    """Map method names to async handlers. Add new methods here — no if/elif needed."""
    async def _start(_: dict) -> None:
        await boiler.start()

    async def _stop(_: dict) -> None:
        await boiler.stop()

    async def _reset(_: dict) -> None:
        await boiler.reset()

    async def _set_temperature(payload: dict) -> None:
        parsed = SetTemperaturePayload.model_validate(payload)
        await boiler.set_temperature(parsed.target_celsius)

    return {"start": _start, "stop": _stop, "reset": _reset, "set_temperature": _set_temperature}


async def register_handlers(client: BaseEdgeClient, boiler: Boiler) -> None:
    dispatch = _build_dispatch(boiler)

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
            return DirectMethodResponse(request.request_id, 400, {"error": exc.errors()})  # HTTP 400: bad payload schema
        except (RuntimeError, ValueError) as exc:
            # RuntimeError = wrong state; ValueError = unsafe temperature — both map to HTTP 409
            logger.warning("Method rejected", method=request.name, reason=str(exc))
            return DirectMethodResponse(request.request_id, 409, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Method handler error", method=request.name)
            return DirectMethodResponse(request.request_id, 500, {"error": str(exc)})

    async def handle_twin_update(patch: dict) -> None:
        logger.info("Twin desired properties updated", patch=patch)
        # Guard: silently ignored when not RUNNING — applying a setpoint to an idle boiler has no effect
        if "default_target_c" in patch and boiler.state == AssetState.RUNNING:
            await boiler.set_temperature(float(patch["default_target_c"]))

    client.on_method(handle_method)
    client.on_twin_update(handle_twin_update)


async def telemetry_loop(client: BaseEdgeClient, boiler: Boiler, interval_s: int) -> None:
    while True:
        try:
            boiler.tick(elapsed_s=interval_s)
            telemetry = boiler.get_telemetry()
            await with_retry(
                lambda: client.send_message_to_output(telemetry.to_dict(), output_name="telemetry"),
                label="boiler.send_telemetry",
            )
            await with_retry(
                lambda: client.update_reported_properties(
                    {"state": telemetry.state, "current_temperature_c": telemetry.current_temperature_c}
                ),
                label="boiler.update_twin",
            )
            logger.debug("Telemetry published", state=telemetry.state, temp_c=telemetry.current_temperature_c)
        except Exception:
            logger.exception("Telemetry loop error")
        await asyncio.sleep(interval_s)


async def main() -> None:
    cfg = BoilerConfig()

    client = create_client()
    boiler = Boiler(
        asset_id=cfg.asset_id,
        max_power_kw=cfg.max_power_kw,
        default_target_c=cfg.default_target_c,
        startup_delay_s=cfg.startup_delay_s,
    )

    await client.connect()
    await register_handlers(client, boiler)
    await client.update_reported_properties(
        {
            "asset_id": cfg.asset_id,
            "asset_type": "industrial_boiler",
            "max_power_kw": cfg.max_power_kw,
            "state": AssetState.IDLE,
        }
    )
    logger.info("Boiler module ready", asset_id=cfg.asset_id, max_power_kw=cfg.max_power_kw)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    telemetry_task = asyncio.create_task(telemetry_loop(client, boiler, cfg.telemetry_interval_s))
    await stop_event.wait()
    telemetry_task.cancel()
    await boiler.stop()
    await client.disconnect()
    logger.info("Boiler module shut down gracefully")


if __name__ == "__main__":
    asyncio.run(main())
