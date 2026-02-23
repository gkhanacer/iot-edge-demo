"""Command dispatcher.

Sends direct method calls to asset modules via the IoT Edge client,
with retry logic and structured logging.
"""

import asyncio

import structlog

from iot_edge_base.client import BaseEdgeClient

logger = structlog.get_logger()

DEFAULT_TIMEOUT_S = 10
MAX_RETRIES = 2
RETRY_DELAY_S = 2.0


class CommandDispatcher:
    """Dispatches direct method calls to asset modules.

    Args:
        client: Connected IoT Edge client.
        max_retries: Number of retry attempts on transient failures.
    """

    def __init__(self, client: BaseEdgeClient, max_retries: int = MAX_RETRIES) -> None:
        self._client = client
        self._max_retries = max_retries

    async def send(
        self,
        module_id: str,
        method_name: str,
        payload: dict,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> dict:
        """Send a direct method call with retry on failure.

        Returns:
            Response payload dict from the target module.

        Raises:
            RuntimeError: If all retries are exhausted.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                logger.info(
                    "Dispatching command",
                    module_id=module_id,
                    method=method_name,
                    attempt=attempt,
                )
                result = await self._client.invoke_method(
                    target_module_id=module_id,
                    method_name=method_name,
                    payload=payload,
                    timeout_s=timeout_s,
                )
                logger.info("Command succeeded", module_id=module_id, method=method_name)
                return result

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Command failed, retrying",
                    module_id=module_id,
                    method=method_name,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt <= self._max_retries:
                    await asyncio.sleep(RETRY_DELAY_S)

        raise RuntimeError(
            f"Command {method_name} to {module_id} failed after {self._max_retries + 1} attempts: {last_exc}"
        )

    async def start_asset(self, module_id: str) -> dict:
        return await self.send(module_id, "start", {})

    async def stop_asset(self, module_id: str) -> dict:
        return await self.send(module_id, "stop", {})

    async def set_solar_output(self, module_id: str, target_kw: float) -> dict:
        return await self.send(module_id, "set_output", {"target_kw": target_kw})

    async def charge_battery(self, module_id: str, power_kw: float) -> dict:
        return await self.send(module_id, "start_charging", {"power_kw": power_kw})

    async def discharge_battery(self, module_id: str, power_kw: float) -> dict:
        return await self.send(module_id, "start_discharging", {"power_kw": power_kw})

    async def set_boiler_temperature(self, module_id: str, target_celsius: float) -> dict:
        return await self.send(module_id, "set_temperature", {"target_celsius": target_celsius})
