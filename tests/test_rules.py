import json
from datetime import date
from pathlib import Path

import pytest

from kuckuc.rules import HAZARD_PRIORITY, HazardDay, hazards_for
from kuckuc.weather import DayWeather, cell_of, fetch_forecast, parse_forecast

FIXTURES = Path(__file__).parent / "fixtures"
D = date(2026, 11, 21)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def hourly(times, temp, apparent=None, gusts=None, snow=None):
    n = len(times)
    return {
        "hourly": {
            "time": times,
            "temperature_2m": temp,
            "apparent_temperature": apparent or temp,
            "wind_gusts_10m": gusts or [0.0] * n,
            "snowfall": snow or [0.0] * n,
        }
    }


# --- cells -----------------------------------------------------------------------------------


def test_cell_rounding():
    assert cell_of(44.8125, 20.4612) == (44.8, 20.5)


def test_nearby_places_share_a_cell_and_distant_ones_do_not():
    assert cell_of(44.84, 20.46) == cell_of(44.76, 20.54) == (44.8, 20.5)
    assert cell_of(44.86, 20.46) == (44.9, 20.5)
    assert cell_of(-33.449, -70.669) == (-33.4, -70.7)


# --- reading a forecast ----------------------------------------------------------------------


def test_parse_groups_by_local_date():
    days = parse_forecast(load("openmeteo_cold.json"))
    assert len(days) == 3 and days[1].temp_min_c == -12.4
    assert [d.day for d in days] == [date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22)]


def test_parse_takes_the_extremes_and_adds_up_the_snow():
    payload = hourly(
        ["2026-11-21T00:00", "2026-11-21T12:00", "2026-11-21T23:00", "2026-11-22T00:00"],
        temp=[-3.0, 4.5, -7.2, 1.0],
        apparent=[-6.0, 2.0, -11.0, 0.0],
        gusts=[20.0, 61.5, 40.0, 5.0],
        snow=[1.4, 0.0, 2.1, 0.7],
    )
    first, second = parse_forecast(payload)
    assert first == DayWeather(date(2026, 11, 21), apparent_max_c=2.0, temp_min_c=-7.2, gust_max_kmh=61.5, snow_cm=3.5)
    assert second == DayWeather(date(2026, 11, 22), apparent_max_c=0.0, temp_min_c=1.0, gust_max_kmh=5.0, snow_cm=0.7)


def test_parse_skips_hours_the_service_left_empty():
    payload = hourly(
        ["2026-11-21T00:00", "2026-11-21T01:00"], temp=[None, -4.0], apparent=[None, -8.0], gusts=[None, 30.0], snow=[None, 1.0]
    )
    assert parse_forecast(payload) == [DayWeather(date(2026, 11, 21), -8.0, -4.0, 30.0, 1.0)]


def test_parse_of_a_reply_with_no_forecast_is_empty():
    assert parse_forecast({}) == []
    assert parse_forecast({"hourly": {"time": []}}) == []
    assert parse_forecast({"error": True, "reason": "bad request"}) == []


def test_fetch_asks_for_three_days_in_the_places_own_time():
    asked = []

    def http_get(url, params):
        asked.append((url, params))
        return load("openmeteo_mild.json")

    days = fetch_forecast(44.8, 20.5, http_get)
    assert len(days) == 3
    url, params = asked[0]
    assert url == "https://api.open-meteo.com/v1/forecast"
    assert params == {
        "latitude": 44.8,
        "longitude": 20.5,
        "hourly": "temperature_2m,apparent_temperature,wind_gusts_10m,snowfall",
        "forecast_days": 3,
        "timezone": "auto",
    }


# --- rules -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dw,expected",
    [
        (DayWeather(D, 35.0, 20, 10, 0), "heat"),
        (DayWeather(D, 34.9, 20, 10, 0), None),
        (DayWeather(D, 0, -10.0, 10, 0), "cold"),
        (DayWeather(D, 0, -9.9, 10, 0), None),
        (DayWeather(D, 0, 0, 80.0, 0), "wind"),
        (DayWeather(D, 0, 0, 79.9, 0), None),
        (DayWeather(D, 0, 0, 10, 20.0), "snow"),
        (DayWeather(D, 0, 0, 10, 19.9), None),
        (DayWeather(D, 0, -15, 90, 30), "cold"),
        (DayWeather(D, 0, 0, 90, 30), "snow"),
    ],
)
def test_thresholds_at_the_boundary(dw, expected, settings):
    hz = hazards_for([dw], settings)
    assert (hz[0].hazard if hz else None) == expected


def test_the_value_is_the_number_the_call_will_say(settings):
    assert hazards_for([DayWeather(D, 38.6, 24, 10, 0)], settings) == [HazardDay(D, "heat", 38.6)]
    assert hazards_for([DayWeather(D, -18, -12.4, 10, 0)], settings) == [HazardDay(D, "cold", -12.4)]
    assert hazards_for([DayWeather(D, 0, 0, 95.5, 0)], settings) == [HazardDay(D, "wind", 95.5)]
    assert hazards_for([DayWeather(D, 0, 0, 10, 27.0)], settings) == [HazardDay(D, "snow", 27.0)]


def test_at_most_one_hazard_per_day_and_days_stay_in_order(settings):
    days = [DayWeather(date(2026, 11, 20), 0, 5, 10, 0), DayWeather(D, 0, -15, 90, 30), DayWeather(date(2026, 11, 22), 0, 0, 85, 0)]
    assert hazards_for(days, settings) == [HazardDay(D, "cold", -15), HazardDay(date(2026, 11, 22), "wind", 85)]


def test_thresholds_come_from_settings(settings):
    settings.cold_temp_c = -5.0
    assert hazards_for([DayWeather(D, 0, -6.0, 10, 0)], settings)[0].hazard == "cold"


def test_priority_order_is_heat_cold_snow_wind():
    assert HAZARD_PRIORITY == ("heat", "cold", "snow", "wind")


def test_cold_fixture_gives_exactly_tomorrows_frost(settings):
    assert hazards_for(parse_forecast(load("openmeteo_cold.json")), settings) == [HazardDay(D, "cold", -12.4)]


def test_mild_forecast_has_no_hazards(settings):
    assert hazards_for(parse_forecast(load("openmeteo_mild.json")), settings) == []
