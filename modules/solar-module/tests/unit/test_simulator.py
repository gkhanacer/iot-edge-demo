import pytest

from src.simulator import get_irradiance


class TestGetIrradiance:
    def test_zero_before_sunrise(self) -> None:
        assert get_irradiance(hour_of_day=5.9) == 0.0

    def test_zero_after_sunset(self) -> None:
        assert get_irradiance(hour_of_day=18.1) == 0.0

    def test_zero_at_midnight(self) -> None:
        assert get_irradiance(hour_of_day=0.0) == 0.0

    def test_peak_at_noon(self) -> None:
        irradiance = get_irradiance(hour_of_day=12.0)
        assert irradiance == pytest.approx(1000.0, abs=1.0)

    def test_positive_during_daylight(self) -> None:
        for hour in [7.0, 9.0, 12.0, 15.0, 17.0]:
            assert get_irradiance(hour_of_day=hour) > 0.0

    def test_symmetric_around_noon(self) -> None:
        morning = get_irradiance(hour_of_day=9.0)
        afternoon = get_irradiance(hour_of_day=15.0)
        assert morning == pytest.approx(afternoon, abs=1.0)

    def test_returns_float(self) -> None:
        result = get_irradiance(hour_of_day=12.0)
        assert isinstance(result, float)
