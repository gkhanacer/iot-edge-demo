"""Tests for the get_irradiance() solar irradiance model.

The model uses a sinusoidal curve based on time of day (UTC):

    irradiance = 1000 * sin(π * (hour - 6) / 12)   for 6 ≤ hour ≤ 18
    irradiance = 0                                   otherwise

Key properties of this model:
- Daylight window: [6:00, 18:00] UTC (12-hour symmetric window)
- Peak: 1000 W/m² at solar noon (12:00)
- Symmetric: irradiance at (12 - Δh) == irradiance at (12 + Δh)
- Monotonically increasing 6→12, decreasing 12→18

get_irradiance() is called every telemetry cycle from the solar module's
telemetry loop. Tests pass explicit hour_of_day values to avoid dependence
on the system clock (which is the default when the argument is omitted).
"""

import pytest

from src.simulator import get_irradiance


# ── TestGetIrradiance ─────────────────────────────────────────────────────────
# All tests pass hour_of_day explicitly so results are deterministic regardless
# of when the test suite runs.

class TestGetIrradiance:
    def test_zero_before_sunrise(self) -> None:
        # 05:54 is before the 06:00 daylight window — no sunlight yet.
        assert get_irradiance(hour_of_day=5.9) == 0.0

    def test_zero_after_sunset(self) -> None:
        # 18:06 is past the 18:00 cutoff — model returns 0 regardless of
        # how slowly the real sun sets.
        assert get_irradiance(hour_of_day=18.1) == 0.0

    def test_zero_at_midnight(self) -> None:
        # Midnight is the extreme of the night period — confirms the guard
        # works for hours well outside the [6, 18] window.
        assert get_irradiance(hour_of_day=0.0) == 0.0

    def test_peak_at_noon(self) -> None:
        # sin(π/2) = 1, so irradiance at hour=12 should equal the 1000 W/m²
        # STC peak value used by the inverter physics model.
        irradiance = get_irradiance(hour_of_day=12.0)
        assert irradiance == pytest.approx(1000.0, abs=1.0)

    def test_positive_during_daylight(self) -> None:
        # Spot-check several hours spread across the daylight window to confirm
        # the model produces physically meaningful positive values throughout.
        for hour in [7.0, 9.0, 12.0, 15.0, 17.0]:
            assert get_irradiance(hour_of_day=hour) > 0.0

    def test_symmetric_around_noon(self) -> None:
        # The sinusoidal model is symmetric: sin at (12 - 3h) == sin at (12 + 3h).
        # This reflects the equal-length morning and afternoon sun arcs.
        # 09:00 and 15:00 are both 3 hours from noon.
        morning = get_irradiance(hour_of_day=9.0)
        afternoon = get_irradiance(hour_of_day=15.0)
        assert morning == pytest.approx(afternoon, abs=1.0)

    def test_returns_float(self) -> None:
        # Downstream code (inverter physics model) expects a float for arithmetic.
        # An int would still work numerically but indicates a type contract break.
        result = get_irradiance(hour_of_day=12.0)
        assert isinstance(result, float)
