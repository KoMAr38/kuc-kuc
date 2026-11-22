"""Set or show who pays for calls, and how much they may spend.

    python scripts/payers.py own --name "Kuc-kuc" --month 25
    python scripts/payers.py org-1 --name "Org" --season 400 --from 2027-06-01 --to 2027-09-30 \\
        --month 150 --staff <uid> --invite ORG-CODE --tz Europe/Berlin
    python scripts/payers.py org-1            # show it, with what it has spent and holds

Caps are in US dollars, the currency the phone providers bill in. A payer with season dates pays for
calls inside the season only. A payer with no cap has no money for calls: its checks do not start.
"""

from __future__ import annotations

import argparse
from datetime import date

from kuckuc.budget import budget_caps
from kuckuc.models import Payer


def apply(store, now, argv: list[str]) -> list[str]:
    """Changes the payer as asked (only the options given) and returns lines describing it."""
    parser = argparse.ArgumentParser(prog="payers.py")
    parser.add_argument("id")
    parser.add_argument("--name")
    parser.add_argument("--month", type=float)
    parser.add_argument("--season", type=float)
    parser.add_argument("--from", dest="start", type=date.fromisoformat)
    parser.add_argument("--to", dest="end", type=date.fromisoformat)
    parser.add_argument("--staff", action="append")
    parser.add_argument("--invite")
    parser.add_argument("--tz")
    args = parser.parse_args(argv)
    payer = store.get_payer(args.id)
    changes = {
        key: value
        for key, value in {
            "name": args.name, "month_cap_usd": args.month, "season_cap_usd": args.season, "season_start": args.start,
            "season_end": args.end, "staff_uids": args.staff, "invite_code": args.invite, "tz": args.tz,
        }.items()
        if value is not None
    }  # fmt: skip
    if payer is None:
        if not changes:
            return [f"no payer {args.id}"]
        payer = Payer(id=args.id, name=args.name or args.id)
    if changes:
        payer = payer.model_copy(update=changes)
        store.put_payer(Payer.model_validate(payer.model_dump()))
    lines = [payer.model_dump_json()]
    caps = budget_caps(payer, now)
    if not caps:
        lines.append("no money for calls now: its checks do not start")
    for key, cap in caps:
        used = store.get_budget(key)
        lines.append(f"{key}: spent ${used['spent']:.2f}, held ${used['reserved']:.2f}, cap ${cap:.2f}")
    return lines


def main() -> int:
    import sys
    from datetime import UTC, datetime

    from google.cloud import firestore

    from kuckuc.config import Settings
    from kuckuc.store_firestore import FirestoreStore

    store = FirestoreStore(firestore.Client(project=Settings().project or None))
    print("\n".join(apply(store, datetime.now(UTC), sys.argv[1:])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
