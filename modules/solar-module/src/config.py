"""Solar module configuration.

All environment variables consumed by this module are declared here.
Pydantic-settings reads values directly from env vars and validates
constraints at startup — bad config fails fast with a clear error.
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class SolarConfig(BaseSettings):
    asset_id: str = "solar-01"
    max_power_kw: float = Field(100.0, gt=0)
    telemetry_interval_s: int = Field(10, ge=1)
    startup_delay_s: float = Field(2.0, ge=0.0)
