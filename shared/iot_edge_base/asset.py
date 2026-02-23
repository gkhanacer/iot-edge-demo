import asyncio
from abc import ABC, abstractmethod
from enum import Enum

import structlog

logger = structlog.get_logger()


class AssetState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAULT = "FAULT"


class BaseAsset(ABC):
    """Base class for all energy asset drivers.

    Implements the common state machine transitions:
        IDLE → STARTING → RUNNING → STOPPING → IDLE
                                 → FAULT → (reset) → IDLE
    """

    def __init__(self, asset_id: str) -> None:
        self.asset_id = asset_id
        self._state = AssetState.IDLE
        self._fault_code: str | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> AssetState:
        return self._state

    @property
    def fault_code(self) -> str | None:
        return self._fault_code

    async def start(self) -> None:
        async with self._lock:
            if self._state != AssetState.IDLE:
                logger.warning("start() ignored", asset_id=self.asset_id, state=self._state)
                return
            self._state = AssetState.STARTING
            logger.info("Asset starting", asset_id=self.asset_id)
            await self._on_start()
            self._state = AssetState.RUNNING
            logger.info("Asset running", asset_id=self.asset_id)

    async def stop(self) -> None:
        async with self._lock:
            if self._state not in (AssetState.RUNNING, AssetState.STARTING):
                return
            self._state = AssetState.STOPPING
            await self._on_stop()
            self._state = AssetState.IDLE
            logger.info("Asset stopped", asset_id=self.asset_id)

    async def fault(self, code: str) -> None:
        self._state = AssetState.FAULT
        self._fault_code = code
        await self._on_fault(code)
        logger.error("Asset fault", asset_id=self.asset_id, fault_code=code)

    async def reset(self) -> None:
        if self._state != AssetState.FAULT:
            return
        self._fault_code = None
        self._state = AssetState.IDLE
        logger.info("Asset reset", asset_id=self.asset_id)

    @abstractmethod
    async def _on_start(self) -> None:
        """Hook called during transition STARTING → RUNNING."""

    @abstractmethod
    async def _on_stop(self) -> None:
        """Hook called during transition STOPPING → IDLE."""

    @abstractmethod
    async def _on_fault(self, code: str) -> None:
        """Hook called on fault."""

    @abstractmethod
    def get_telemetry(self) -> dict:
        """Return current telemetry as a plain dict."""
