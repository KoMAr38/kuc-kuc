"""From an alert to the moments the phone will ring.

All times are worked out on the clock of the place where the person lives, daylight saving
included, and returned in UTC.

* A heads-up the day before at 17:00: only before the first day of a hazard, and only if
  that moment is still ahead.
* A check on the day itself: 17:00 for heat (the hottest part of the day is over),
  10:00 for everything else. If the hazard was discovered after that time, the check runs
  at once, but only while it is daytime there; nobody is woken for a forecast.
* An introduction call asked for at night waits for the morning (``daytime_start``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from kuckuc.models import Alert, EpisodeKind

HEADSUP_HOUR = 17
CHECK_HOUR = 10
HEAT_CHECK_HOUR = 17


def plan_calls(
    alert: Alert,
    tz: str,
    now: datetime,
    previous_day_alert: bool,
    quiet_start_hour: int = 8,
    quiet_end_hour: int = 20,
) -> list[tuple[EpisodeKind, datetime]]:
    zone = ZoneInfo(tz)
    planned: list[tuple[EpisodeKind, datetime]] = []

    headsup_at = _at(alert.day - timedelta(days=1), HEADSUP_HOUR, zone)
    if not previous_day_alert and headsup_at > now:
        planned.append((EpisodeKind.HEADSUP, headsup_at))

    check_at = _at(alert.day, HEAT_CHECK_HOUR if alert.hazard == "heat" else CHECK_HOUR, zone)
    if check_at > now:
        planned.append((EpisodeKind.CHECK, check_at))
    elif now.astimezone(zone).date() == alert.day and daytime_start(tz, now, quiet_start_hour, quiet_end_hour) == now:
        planned.append((EpisodeKind.CHECK, now))  # the day has come and it is daytime there: check at once
    return planned


def daytime_start(tz: str, now: datetime, start_hour: int = 8, end_hour: int = 20) -> datetime:
    """When a call that can wait may ring: ``now`` while it is daytime where the person lives,
    otherwise the next ``start_hour`` o'clock there (in UTC)."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    if start_hour <= local.hour < end_hour:
        return now
    day = local.date() if local.hour < start_hour else local.date() + timedelta(days=1)
    return _at(day, start_hour, zone)


def _at(day: date, hour: int, zone: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=zone).astimezone(UTC)
