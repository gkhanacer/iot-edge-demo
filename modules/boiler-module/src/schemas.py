"""Pydantic schemas for boiler module DirectMethod payloads.

Each schema validates the payload at the system boundary (IoT Hub → module).
ValidationError is caught by handle_method and returned as HTTP 400.
"""

from pydantic import BaseModel, Field


class SetTemperaturePayload(BaseModel):
    # Must match the safe operating range enforced by Boiler.set_temperature()
    target_celsius: float = Field(ge=40.0, le=120.0)
