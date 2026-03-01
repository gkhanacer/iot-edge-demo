"""Pydantic schemas for solar module DirectMethod payloads.

Each schema validates the payload at the system boundary (IoT Hub → module).
ValidationError is caught by handle_method and returned as HTTP 400.
"""

from pydantic import BaseModel, Field


class SetOutputPayload(BaseModel):
    target_kw: float = Field(ge=0.0)
