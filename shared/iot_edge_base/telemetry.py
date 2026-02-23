from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class BaseTelemetry:
    asset_id: str
    asset_type: str
    state: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    fault_code: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None or k == "fault_code"}
