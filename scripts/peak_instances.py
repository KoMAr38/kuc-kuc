"""Peak number of Cloud Run instances per service in a time window, read from Cloud Monitoring.

    python scripts/peak_instances.py 2026-11-13T21:00:00Z 2026-11-13T21:10:00Z

Used after a load test: the number it prints for engine + voice is what the architecture page
shows as the peak (stored with ``python -m kuckuc.jobs.loadtest --peak-instances=N``).
Cloud Monitoring samples the instance count once a minute, so a very short burst reads low.
"""

from __future__ import annotations

import sys
from collections import defaultdict

import google.auth
from google.auth.transport.requests import AuthorizedSession

SERVICES = ("engine", "voice", "web")


def peaks(project: str, start: str, end: str) -> dict[str, int]:
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/monitoring.read"])
    session = AuthorizedSession(credentials)
    session.headers["x-goog-user-project"] = project
    reply = session.get(
        f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries",
        params={
            "filter": 'metric.type="run.googleapis.com/container/instance_count"',
            "interval.startTime": start,
            "interval.endTime": end,
            "aggregation.alignmentPeriod": "60s",
            "aggregation.perSeriesAligner": "ALIGN_MAX",
        },
        timeout=30,
    )
    reply.raise_for_status()
    # One series per service, revision and state (active or idle): add them up minute by minute.
    per_minute: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for series in reply.json().get("timeSeries", []):
        service = series["resource"]["labels"].get("service_name", "")
        for point in series["points"]:
            value = point["value"]
            per_minute[service][point["interval"]["endTime"]] += int(value.get("int64Value", value.get("doubleValue", 0)))
    return {service: max(per_minute[service].values(), default=0) for service in SERVICES}


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    _, project = google.auth.default()
    found = peaks(project or "", sys.argv[1], sys.argv[2])
    for service, peak in found.items():
        print(f"{service:7} {peak}")
    print(f"engine + voice: {found['engine'] + found['voice']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
