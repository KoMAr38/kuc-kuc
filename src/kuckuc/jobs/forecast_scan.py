"""The forecast job: ``python -m kuckuc.jobs.forecast_scan``.

Cloud Scheduler starts it every three hours. It asks for one forecast per cell of about
10 km in which someone under watch lives, and creates an alert for each person whose today
or tomorrow crosses a threshold. An alert exists once per person, hazard and day, and is
announced again on every scan, so a lost announcement is made good three hours later at
the latest, while a repeated one changes nothing. Then it exits; the engine does the rest.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from zoneinfo import ZoneInfo

from kuckuc.deps import Deps
from kuckuc.models import Alert, Person
from kuckuc.rules import hazards_for
from kuckuc.weather import cell_of, fetch_forecast

log = logging.getLogger(__name__)


def run_scan(deps: Deps, http_get: Callable[[str, dict], dict]) -> int:
    """Returns how many alerts were created. One cell failing is logged and skipped."""
    cells: dict[tuple[float, float], list[Person]] = {}
    for person in deps.store.list_active_persons():
        cells.setdefault(cell_of(person.lat, person.lon), []).append(person)

    now = deps.clock.now()
    created = 0
    for (lat, lon), people in cells.items():
        try:
            hazards = hazards_for(fetch_forecast(lat, lon, http_get), deps.settings)
        except Exception as exc:
            log.error("forecast for one cell failed (%s); %d people skipped this round", type(exc).__name__, len(people))
            continue
        for person in people:
            today = now.astimezone(ZoneInfo(person.tz)).date()
            for hazard in hazards:
                if hazard.day not in (today, today + timedelta(days=1)):
                    continue
                alert = Alert(
                    id=f"{person.id}:{hazard.hazard}:{hazard.day}",
                    person_id=person.id,
                    hazard=hazard.hazard,
                    day=hazard.day,
                    value=hazard.value,
                    created_at=now,
                )
                if deps.store.create_alert(alert):
                    created += 1
                # Announced on every scan, new or not: if an earlier announcement was lost, this one
                # schedules the calls; if not, the engine's fixed episode ids make it change nothing.
                deps.bus.publish("alert.created", {"alert_id": alert.id})
    log.info("forecast scan: %d cells, %d alerts created", len(cells), created)
    return created


def main() -> int:
    import httpx

    from kuckuc.config import Settings
    from kuckuc.deps import build_cloud_deps

    logging.basicConfig(level=logging.INFO)
    deps = build_cloud_deps(Settings(), "job")
    with httpx.Client(timeout=20) as http:

        def http_get(url: str, params: dict) -> dict:
            reply = http.get(url, params=params)
            reply.raise_for_status()
            return reply.json()

        run_scan(deps, http_get)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
