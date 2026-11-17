"""Is a phone call under way right now? Run by deploy.sh before it replaces voice or engine.

    python scripts/live_calls.py      # exit 0: nobody is on the phone; exit 1: a call is live

A release swaps servers under a running call: on 17 November 2026 that made one answer reach two
servers and turned it into "unclear". A call counts as live while it is being dialled or has been
placed (ringing or talking) and dialling began less than 15 minutes ago; an older one only lost its
last status report. Age counts from dialling, not from queueing: a follow-up is queued 20 minutes
before it rings.
Prints counts only, never a number or a name.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

LIVE_FOR = timedelta(minutes=15)
LIVE_STATUSES = ("dialing", "placed")


def live_calls(rows: list[dict], now: datetime) -> int:
    """How many of these call documents are live at ``now``."""
    count = 0
    for row in rows:
        began = row.get("placed_at") or row.get("created_at")
        if isinstance(began, str):
            began = datetime.fromisoformat(began)
        live = row.get("status") in LIVE_STATUSES and row.get("provider") != "simulated"
        if live and began and now - began < LIVE_FOR:
            count += 1
    return count


def main() -> int:
    from google.cloud import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter

    from kuckuc.config import Settings

    db = firestore.Client(project=Settings().project or None)
    query = db.collection("calls").where(filter=FieldFilter("status", "in", list(LIVE_STATUSES)))
    rows = [s.to_dict() for s in query.stream()]
    live = live_calls(rows, datetime.now(UTC))
    print(f"calls under way: {live}")
    return 1 if live else 0


if __name__ == "__main__":
    sys.exit(main())
