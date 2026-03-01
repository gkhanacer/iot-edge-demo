"""Pydantic schemas for battery module DirectMethod payloads.

Each schema validates the payload at the system boundary (IoT Hub → module).
ValidationError is caught by handle_method and returned as HTTP 400.
"""

from pydantic import BaseModel, Field


class StartChargingPayload(BaseModel):
    # Optional: if omitted the handler falls back to battery.max_power_kw
    power_kw: float | None = Field(None, gt=0.0)


class StartDischargingPayload(BaseModel):
    # Optional: if omitted the handler falls back to battery.max_power_kw
    power_kw: float | None = Field(None, gt=0.0)
