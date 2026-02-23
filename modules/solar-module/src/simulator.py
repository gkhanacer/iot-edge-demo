"""Simple solar irradiance model.

Models a sinusoidal irradiance curve based on time of day.
Peak irradiance (1000 W/m²) at solar noon (12:00 UTC).
Zero irradiance before 6:00 and after 18:00.
"""

import math
from datetime import datetime, timezone


def get_irradiance(hour_of_day: float | None = None) -> float:
    """Return estimated solar irradiance in W/m².

    Args:
        hour_of_day: Hour as float (e.g., 13.5 = 13:30). Defaults to current UTC hour.

    Returns:
        Irradiance in W/m², between 0 and 1000.
    """
    if hour_of_day is None:
        now = datetime.now(timezone.utc)
        hour_of_day = now.hour + now.minute / 60.0

    if hour_of_day < 6.0 or hour_of_day > 18.0:
        return 0.0

    angle = math.pi * (hour_of_day - 6.0) / 12.0
    return round(1000.0 * math.sin(angle), 1)
