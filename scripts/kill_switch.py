"""Stop or allow all new calls.

    python scripts/kill_switch.py on      # no new call is placed from now on
    python scripts/kill_switch.py off
    python scripts/kill_switch.py         # show the current state

A call that is blocked this way makes the family's notifier send an urgent message.
"""

import sys

from google.cloud import firestore

from kuckuc.config import Settings


def main() -> int:
    settings = Settings()
    ref = firestore.Client(project=settings.project or None).collection("runtime").document("config")
    if len(sys.argv) > 1:
        if sys.argv[1] not in ("on", "off"):
            print(__doc__)
            return 2
        ref.set({"kill_switch": sys.argv[1] == "on"}, merge=True)
    snap = ref.get()
    on = bool(snap.exists and snap.to_dict().get("kill_switch"))
    print("kill switch:", "ON - no new calls are placed" if on else "off - calls are allowed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
