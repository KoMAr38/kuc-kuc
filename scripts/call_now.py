"""Place one real test call to your own phone and watch what the system does.

    python scripts/call_now.py --lang en
    python scripts/call_now.py --lang sr

The number is asked for on the keyboard, so it never lands in shell history, in a file or
in the repository. It is stored only on a temporary demo person, which this script deletes
when the call is over (and which the database would delete by itself after a day).
"""

from __future__ import annotations

import argparse
import getpass
import re
import time
import uuid

from google.cloud import firestore, pubsub_v1

from kuckuc.cloud import PubSubBus
from kuckuc.config import Settings
from kuckuc.models import EpisodeState, Person, PersonStatus
from kuckuc.store_firestore import FirestoreStore

WAIT_LIMIT_S = 240


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lang", choices=("en", "sr"), default="en")
    parser.add_argument("--name", default=None, help='how the call greets you (default: "there" / "Petar")')
    parser.add_argument("--hazard", choices=("heat", "cold", "wind", "snow"), default="cold")
    args = parser.parse_args()

    settings = Settings()
    if not settings.project:
        print("Set KUCKUC_PROJECT first (your Google Cloud project id).")
        return 2

    phone = re.sub(r"[\s()-]", "", getpass.getpass("Your phone number, with country code (hidden as you type): "))
    if not re.fullmatch(r"\+\d{8,15}", phone):
        print("That does not look like a number in international form, for example +4791234567.")
        return 2

    db = firestore.Client(project=settings.project)
    store = FirestoreStore(db)
    bus = PubSubBus(pubsub_v1.PublisherClient(), f"projects/{settings.project}/topics/events")

    run = uuid.uuid4().hex[:10]
    person_id, episode_id = f"demo-cli-{run}", f"demo-cli-ep-{run}"
    store.put_person(
        Person(
            id=person_id,
            owner_uid="",
            name=args.name or ("there" if args.lang == "en" else "Petar"),
            family_name="",
            gender="m",
            phone=phone,
            lang=args.lang,
            lat=0.0,
            lon=0.0,
            tz="UTC",
            place="test",
            status=PersonStatus.ACTIVE,
            demo=True,
        )
    )
    try:
        bus.publish(
            "check.requested",
            {"person_id": person_id, "kind": "demo", "episode_id": episode_id, "hazard": args.hazard},
        )
        print("Requested. Your phone should ring within about 20 seconds.\n")
        return watch(store, episode_id)
    finally:
        db.collection("persons").document(person_id).delete()


def watch(store: FirestoreStore, episode_id: str) -> int:
    seen = 0
    marks: dict[str, float] = {}
    started = time.monotonic()
    while time.monotonic() - started < WAIT_LIMIT_S:
        for event in store.list_timeline(episode_id, after_seq=seen):
            seen = event.seq
            marks[event.type] = event.at.timestamp()
            details = ", ".join(f"{k}={v}" for k, v in event.public.items() if v)
            print(f"  {event.at:%H:%M:%S}  {event.type:<20} {details}")
        episode = store.get_episode(episode_id)
        if episode is not None and episode.state == EpisodeState.CLOSED:
            print(f"\nclosed: {episode.result.value}")
            if "call_answered" in marks and "answer_interpreted" in marks:
                heard_after = marks["answer_interpreted"] - marks["call_answered"]
                print(f"from pick-up to an understood answer: {heard_after:.1f} s")
            return 0
        time.sleep(1)
    print("\nStill not closed after 4 minutes. The timeline above shows where it stopped.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
