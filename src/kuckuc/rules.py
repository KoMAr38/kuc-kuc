"""When is the weather dangerous for an older person living alone?

Four thresholds, all set in ``Settings`` so they can be changed without touching code:
heat that feels like 35 °C or more, frost of -10 °C or lower, gusts of 80 km/h or more,
20 cm of snow or more in a day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from kuckuc.config import Settings
from kuckuc.models import Hazard
from kuckuc.weather import DayWeather

# When a day crosses more than one threshold, the first of these is the one the call talks about.
HAZARD_PRIORITY: tuple[Hazard, ...] = ("heat", "cold", "snow", "wind")


@dataclass(frozen=True)
class HazardDay:
    day: date
    hazard: Hazard
    value: float  # the number the call says: degrees, km/h or cm


def hazards_for(days: list[DayWeather], settings: Settings) -> list[HazardDay]:
    """At most one hazard per day, in the order the days were given."""
    found = []
    for d in days:
        crossed: dict[Hazard, float] = {}
        if d.apparent_max_c >= settings.heat_apparent_c:
            crossed["heat"] = d.apparent_max_c
        if d.temp_min_c <= settings.cold_temp_c:
            crossed["cold"] = d.temp_min_c
        if d.snow_cm >= settings.snow_cm:
            crossed["snow"] = d.snow_cm
        if d.gust_max_kmh >= settings.wind_gust_kmh:
            crossed["wind"] = d.gust_max_kmh
        for hazard in HAZARD_PRIORITY:
            if hazard in crossed:
                found.append(HazardDay(d.day, hazard, crossed[hazard]))
                break
    return found
