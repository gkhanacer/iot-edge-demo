"""Controller module configuration.

All environment variables consumed by this module are declared here.
Pydantic-settings reads values directly from env vars and validates
constraints at startup — bad config fails fast with a clear error.

Note: device_id reads from IOTEDGE_DEVICEID (set automatically by the
Azure IoT Edge runtime). populate_by_name=True allows direct construction
in tests without using the alias.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ControllerConfig(BaseSettings):
    model_config = SettingsConfigDict(populate_by_name=True)

    # IOTEDGE_DEVICEID is injected automatically by the Azure IoT Edge runtime
    device_id: str = Field("edge-device-01", alias="iotedge_deviceid")
    reporting_interval_s: int = Field(30, ge=1)
    surplus_threshold_kw: float = Field(10.0, gt=0)
    # Module IDs must match the deployment manifest / docker-compose service names
    solar_module_id: str = "solar-module"
    battery_module_id: str = "battery-module"
    boiler_module_id: str = "boiler-module"
