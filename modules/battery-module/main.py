"""Battery module entry point."""

import asyncio
import signal
from typing import Awaitable, Callable

import structlog

from iot_edge_base.asset import AssetState
from iot_edge_base.client import BaseEdgeClient, DirectMethodRequest, DirectMethodResponse, create_client
from pydantic import ValidationError

from iot_edge_base.retry import with_retry
from src.battery import BatteryStorage
from src.config import BatteryConfig
from src.schemas import StartChargingPayload, StartDischargingPayload

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()


def _build_dispatch(battery: BatteryStorage) -> dict[str, Callable[[dict], Awaitable[None]]]:
    """Map method names to async handlers. Add new methods here — no if/elif needed."""
    async def _start(_: dict) -> None:
        await battery.start()

    async def _stop(_: dict) -> None:
        await battery.stop()

    async def _set_idle(_: dict) -> None:
        await battery.set_idle()

    async def _reset(_: dict) -> None:
        await battery.reset()

    async def _start_charging(payload: dict) -> None:
        parsed = StartChargingPayload.model_validate(payload)
        power_kw = parsed.power_kw if parsed.power_kw is not None else battery.max_power_kw
        await battery.start_charging(power_kw)

    async def _start_discharging(payload: dict) -> None:
        parsed = StartDischargingPayload.model_validate(payload)
        power_kw = parsed.power_kw if parsed.power_kw is not None else battery.max_power_kw
        await battery.start_discharging(power_kw)

    return {
        "start": _start,
        "stop": _stop,
        "set_idle": _set_idle,
        "reset": _reset,
        "start_charging": _start_charging,
        "start_discharging": _start_discharging,
    }


async def register_handlers(client: BaseEdgeClient, battery: BatteryStorage) -> None:
    dispatch = _build_dispatch(battery)

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
            battery.max_power_kw = float(patch["max_power_kw"])

    client.on_method(handle_method)
    client.on_twin_update(handle_twin_update)


async def telemetry_loop(client: BaseEdgeClient, battery: BatteryStorage, interval_s: int) -> None:
    while True:
        try:
            battery.tick(elapsed_s=interval_s)
            telemetry = battery.get_telemetry()
            await with_retry(
                lambda: client.send_message_to_output(telemetry.to_dict(), output_name="telemetry"),
                label="battery.send_telemetry",
            )
            await with_retry(
                lambda: client.update_reported_properties(
                    {
                        "state": telemetry.state,
                        "mode": telemetry.mode,
                        "state_of_charge": telemetry.state_of_charge,
                    }
                ),
                label="battery.update_twin",
            )
            logger.debug("Telemetry published", mode=telemetry.mode, soc=telemetry.state_of_charge)
        except Exception:
            logger.exception("Telemetry loop error")
        await asyncio.sleep(interval_s)


async def main() -> None:
    cfg = BatteryConfig()

    client = create_client()
    battery = BatteryStorage(
        asset_id=cfg.asset_id,
        capacity_kwh=cfg.capacity_kwh,
        max_power_kw=cfg.max_power_kw,
        initial_soc=cfg.initial_soc,
        startup_delay_s=cfg.startup_delay_s,
    )

    await client.connect()
    await register_handlers(client, battery)
    await client.update_reported_properties(
        {
            "asset_id": cfg.asset_id,
            "asset_type": "battery_storage",
            "capacity_kwh": cfg.capacity_kwh,
            "state": AssetState.IDLE,
        }
    )
    logger.info("Battery module ready", asset_id=cfg.asset_id, capacity_kwh=cfg.capacity_kwh)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    loop.add_signal_handler(signal.SIGINT, stop_event.set)

    telemetry_task = asyncio.create_task(telemetry_loop(client, battery, cfg.telemetry_interval_s))
    await stop_event.wait()
    telemetry_task.cancel()
    await battery.stop()
    await client.disconnect()
    logger.info("Battery module shut down gracefully")


if __name__ == "__main__":
    asyncio.run(main())
