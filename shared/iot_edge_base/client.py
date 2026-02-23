"""IoT Edge client abstraction.

Supports two modes, controlled by the EDGE_MODE environment variable:
  - production (default): uses azure-iot-device SDK, connects to real edgeHub
  - local: uses asyncio-mqtt to connect to a local Mosquitto broker for development
"""

import asyncio
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable

import structlog

logger = structlog.get_logger()


@dataclass
class DirectMethodRequest:
    request_id: str
    name: str
    payload: dict


@dataclass
class DirectMethodResponse:
    request_id: str
    status: int
    payload: dict


MessageHandler = Callable[[dict, str], Awaitable[None]]
MethodHandler = Callable[[DirectMethodRequest], Awaitable[DirectMethodResponse]]
TwinHandler = Callable[[dict], Awaitable[None]]


class BaseEdgeClient(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def send_message_to_output(self, data: dict, output_name: str) -> None: ...

    @abstractmethod
    async def send_method_response(self, response: DirectMethodResponse) -> None: ...

    @abstractmethod
    async def update_reported_properties(self, props: dict) -> None: ...

    @abstractmethod
    async def invoke_method(
        self,
        target_module_id: str,
        method_name: str,
        payload: dict,
        timeout_s: int = 10,
    ) -> dict: ...

    @abstractmethod
    def on_message(self, handler: MessageHandler) -> None: ...

    @abstractmethod
    def on_method(self, handler: MethodHandler) -> None: ...

    @abstractmethod
    def on_twin_update(self, handler: TwinHandler) -> None: ...


class AzureEdgeClient(BaseEdgeClient):
    """Production client — wraps azure-iot-device IoTHubModuleClient."""

    def __init__(self) -> None:
        from azure.iot.device.aio import IoTHubModuleClient  # type: ignore[import]

        self._client = IoTHubModuleClient.create_from_edge_environment()

    async def connect(self) -> None:
        await self._client.connect()
        logger.info("Connected to edgeHub")

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def send_message_to_output(self, data: dict, output_name: str) -> None:
        from azure.iot.device import Message  # type: ignore[import]

        msg = Message(json.dumps(data))
        msg.content_type = "application/json"
        msg.content_encoding = "utf-8"
        await self._client.send_message_to_output(msg, output_name)

    async def send_method_response(self, response: DirectMethodResponse) -> None:
        from azure.iot.device import MethodResponse  # type: ignore[import]

        resp = MethodResponse(response.request_id, response.status, response.payload)
        await self._client.send_method_response(resp)

    async def update_reported_properties(self, props: dict) -> None:
        await self._client.patch_twin_reported_properties(props)

    async def invoke_method(
        self,
        target_module_id: str,
        method_name: str,
        payload: dict,
        timeout_s: int = 10,
    ) -> dict:
        device_id = os.environ["IOTEDGE_DEVICEID"]
        result = await self._client.invoke_method(
            method_params={
                "methodName": method_name,
                "payload": payload,
                "responseTimeoutInSeconds": timeout_s,
            },
            device_id=device_id,
            module_id=target_module_id,
        )
        return result.payload

    def on_message(self, handler: MessageHandler) -> None:
        async def _wrap(message):
            data = json.loads(message.data)
            input_name = message.input_name or "default"
            await handler(data, input_name)

        self._client.on_message_received = _wrap

    def on_method(self, handler: MethodHandler) -> None:
        async def _wrap(method_request):
            req = DirectMethodRequest(
                request_id=method_request.request_id,
                name=method_request.name,
                payload=method_request.payload or {},
            )
            resp = await handler(req)
            from azure.iot.device import MethodResponse  # type: ignore[import]

            await self._client.send_method_response(
                MethodResponse(resp.request_id, resp.status, resp.payload)
            )

        self._client.on_method_request_received = _wrap

    def on_twin_update(self, handler: TwinHandler) -> None:
        self._client.on_twin_desired_properties_patch_received = handler


class LocalMqttEdgeClient(BaseEdgeClient):
    """Local development client — uses MQTT broker (Mosquitto in docker-compose).

    Topic conventions:
      Publish telemetry : edge/{module_id}/outputs/{output_name}
      Receive inputs    : edge/{module_id}/inputs/#
      Direct methods    : edge/{module_id}/methods/{method_name}
      Method response   : edge/{module_id}/methods/response/{request_id}
      Twin updates      : edge/{module_id}/twin/desired

    IoT Edge message routing is simulated via the LOCAL_INPUT_TOPICS environment
    variable.  Set it to a comma-separated list of wildcard MQTT topics that this
    module should treat as incoming messages:

      # controller-module docker-compose env:
      LOCAL_INPUT_TOPICS: "edge/+/outputs/telemetry"
    """

    def __init__(self, broker_host: str, module_id: str) -> None:
        self._broker_host = broker_host
        self._module_id = module_id
        self._client = None
        self._message_handler: MessageHandler | None = None
        self._method_handler: MethodHandler | None = None
        self._twin_handler: TwinHandler | None = None
        self._listen_task: asyncio.Task | None = None
        # Pending invoke_method responses keyed by request_id
        self._pending: dict[str, asyncio.Future] = {}
        # Extra topic subscriptions that simulate edgeHub routing
        extra = os.environ.get("LOCAL_INPUT_TOPICS", "")
        self._extra_topics: list[str] = [t.strip() for t in extra.split(",") if t.strip()]

    async def connect(self) -> None:
        import asyncio_mqtt as mqtt  # type: ignore[import]

        self._client = mqtt.Client(self._broker_host)
        await self._client.__aenter__()
        logger.info("Connected to local MQTT broker", host=self._broker_host, module=self._module_id)

        # Subscribe to this module's own inputs, methods, and method responses.
        # Also subscribe to any extra topics (simulates edgeHub routing).
        topics = [
            f"edge/{self._module_id}/inputs/#",
            f"edge/{self._module_id}/methods/+",
            f"edge/+/methods/response/#",  # needed to receive invoke_method replies
        ] + self._extra_topics

        for topic in topics:
            await self._client.subscribe(topic)

        self._listen_task = asyncio.get_running_loop().create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        try:
            async with self._client.messages() as messages:
                async for message in messages:
                    topic = str(message.topic)
                    try:
                        payload = json.loads(message.payload)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    await self._dispatch(topic, payload)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("MQTT listener crashed", module=self._module_id)

    async def _dispatch(self, topic: str, payload: dict) -> None:
        # ── Method response (for invoke_method) ──────────────────────────────
        if "/methods/response/" in topic:
            request_id = topic.rsplit("/methods/response/", 1)[-1]
            future = self._pending.pop(request_id, None)
            if future and not future.done():
                future.set_result(payload.get("payload", {}))
            return

        # ── Incoming direct method call ───────────────────────────────────────
        if f"edge/{self._module_id}/methods/" in topic:
            method_name = topic.rsplit("/methods/", 1)[-1]
            if self._method_handler:
                request_id = payload.pop("_request_id", f"{method_name}-{id(payload)}")
                req = DirectMethodRequest(
                    request_id=request_id,
                    name=method_name,
                    payload=payload,
                )
                resp = await self._method_handler(req)
                await self.send_method_response(resp)
            return

        # ── Incoming message (own inputs OR extra routed topics) ──────────────
        if self._message_handler:
            if "/outputs/" in topic:
                input_name = topic.rsplit("/outputs/", 1)[-1]
            elif "/inputs/" in topic:
                input_name = topic.rsplit("/inputs/", 1)[-1].split("/")[0]
            else:
                input_name = "default"
            await self._message_handler(payload, input_name)

    async def disconnect(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.__aexit__(None, None, None)

    async def send_message_to_output(self, data: dict, output_name: str) -> None:
        topic = f"edge/{self._module_id}/outputs/{output_name}"
        await self._client.publish(topic, json.dumps(data))

    async def send_method_response(self, response: DirectMethodResponse) -> None:
        topic = f"edge/{self._module_id}/methods/response/{response.request_id}"
        await self._client.publish(
            topic, json.dumps({"status": response.status, "payload": response.payload})
        )

    async def update_reported_properties(self, props: dict) -> None:
        topic = f"edge/{self._module_id}/twin/reported"
        await self._client.publish(topic, json.dumps(props))

    async def invoke_method(
        self,
        target_module_id: str,
        method_name: str,
        payload: dict,
        timeout_s: int = 10,
    ) -> dict:
        loop = asyncio.get_running_loop()
        request_id = f"{method_name}-{loop.time():.6f}"

        # Embed request_id so the target module echoes it back in the response topic
        outgoing = {**payload, "_request_id": request_id}
        topic = f"edge/{target_module_id}/methods/{method_name}"

        future: asyncio.Future[dict] = loop.create_future()
        self._pending[request_id] = future

        await self._client.publish(topic, json.dumps(outgoing))

        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise RuntimeError(
                f"Method {method_name} on {target_module_id} timed out after {timeout_s}s"
            )

    def on_message(self, handler: MessageHandler) -> None:
        self._message_handler = handler

    def on_method(self, handler: MethodHandler) -> None:
        self._method_handler = handler

    def on_twin_update(self, handler: TwinHandler) -> None:
        self._twin_handler = handler


def create_client() -> BaseEdgeClient:
    mode = os.environ.get("EDGE_MODE", "production")
    if mode == "local":
        broker = os.environ.get("MQTT_BROKER_HOST", "mosquitto")
        module_id = os.environ.get("IOTEDGE_MODULEID", "unknown-module")
        logger.info("Using local MQTT client", broker=broker, module_id=module_id)
        return LocalMqttEdgeClient(broker_host=broker, module_id=module_id)
    logger.info("Using Azure IoT Edge client")
    return AzureEdgeClient()
