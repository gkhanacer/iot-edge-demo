"""Battery module configuration.

All environment variables consumed by this module are declared here.
Pydantic-settings reads values directly from env vars and validates
constraints at startup — bad config fails fast with a clear error.
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class BatteryConfig(BaseSettings):
    asset_id: str = "battery-01"
    capacity_kwh: float = Field(500.0, gt=0)
    max_power_kw: float = Field(100.0, gt=0)
    initial_soc: float = Field(0.5, ge=0.0, le=1.0)
    telemetry_interval_s: int = Field(10, ge=1)
    startup_delay_s: float = Field(1.0, ge=0.0)
