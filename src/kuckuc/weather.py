"""The forecast, from Open-Meteo (free, no key; attribution is on the site).

People who live near each other share a cell of about 10 km, so one request covers them all
and the service never asks about an exact location.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = ("temperature_2m", "apparent_temperature", "wind_gusts_10m", "snowfall")
FORECAST_DAYS = 3  # today and the next 48 hours


@dataclass(frozen=True)
class DayWeather:
    """One local calendar day: the extremes that matter, and the day's snowfall."""

    day: date
    apparent_max_c: float | None  # None only when the service sent no temperature at all that day
    temp_min_c: float | None
    gust_max_kmh: float
    snow_cm: float


def cell_of(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 1), round(lon, 1)


def parse_forecast(payload: dict) -> list[DayWeather]:
    """Group the hourly values by local date. Hours the service left empty are skipped;
    a reply with no forecast in it gives an empty list."""
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time") or []
    columns = [hourly.get(name) or [] for name in HOURLY]

    # Each value is taken from the hours that have it: one variable sent as null (Open-Meteo does
    # that) must never hide the others. One temperature column missing is made up from the other
    # (erring towards a call); a day with neither still counts its wind and snow.
    by_day: dict[date, list[list[float | None]]] = {}
    for i, stamp in enumerate(times):
        row = [col[i] if i < len(col) else None for col in columns]
        by_day.setdefault(date.fromisoformat(stamp[:10]), []).append(row)

    days = []
    for day in sorted(by_day):
        temp, apparent, gusts, snow = ([v for v in values if v is not None] for values in zip(*by_day[day], strict=True))
        days.append(
            DayWeather(
                day=day,
                apparent_max_c=max(apparent or temp, default=None),
                temp_min_c=min(temp or apparent, default=None),
                gust_max_kmh=max(gusts, default=0.0),
                snow_cm=round(sum(snow), 1),
            )
        )
    return days


def fetch_forecast(lat: float, lon: float, http_get: Callable[[str, dict], dict]) -> list[DayWeather]:
    """``http_get(url, params)`` returns the decoded JSON reply. Times come back in the place's own timezone."""
    return parse_forecast(
        http_get(
            FORECAST_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": ",".join(HOURLY),
                "forecast_days": FORECAST_DAYS,
                "timezone": "auto",
            },
        )
    )
