"""The Store interface on Firestore.

Documents are the models dumped as JSON (dates as ISO text), so what is stored is what the
tests see in memory. Three things are added on top because Firestore needs them:

* a call document also carries ``person_id`` and ``day``, so the daily cap is one query;
* timeline rows are ordered by Firestore's own commit time, one clock for every service;
* fields named ``expires_at`` are real timestamps, so a TTL policy can delete old demo data.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from enum import Enum

from google.api_core import exceptions as gexc
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from kuckuc.models import (
    Alert,
    Answer,
    CallRecord,
    DemoSession,
    Episode,
    EpisodeState,
    Neighbor,
    Person,
    PersonStatus,
    Runtime,
    Target,
    TimelineEvent,
    User,
)

COLLECTIONS = (
    "users",
    "persons",
    "neighbors",
    "alerts",
    "episodes",
    "calls",
    "demo_sessions",
    "demo_runs",
    "demo_days",
    "runtime",
    "stats",
)
_COUNTERS = "_counters"
DEMO_PREFIX = "demo-"
SIM_PREFIX = "sim-"  # the load test's simulated people and their episodes
DEMO_DATA_KEPT = timedelta(hours=24)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _eq(field: str, value) -> FieldFilter:
    return FieldFilter(field, "==", value)


class FirestoreStore:
    def __init__(self, client: firestore.Client, prefix: str = "", now: Callable[[], datetime] = _utc_now) -> None:
        self._db = client
        self._prefix = prefix
        self._now = now

    def _col(self, name: str):
        return self._db.collection(f"{self._prefix}{name}")

    def _get(self, collection: str, id: str, model):
        if not id:
            return None
        snap = self._col(collection).document(id).get()
        return model.model_validate(snap.to_dict()) if snap.exists else None

    def _put(self, collection: str, id: str, model, **extra) -> None:
        self._col(collection).document(id).set(model.model_dump(mode="json") | extra)

    def _create(self, collection: str, id: str, model, **extra) -> bool:
        try:
            self._col(collection).document(id).create(model.model_dump(mode="json") | extra)
        except gexc.Conflict:
            return False
        return True

    def _demo_ttl(self, id: str) -> dict:
        """Demo and simulated people, their episodes, calls and timeline rows carry an expiry a day
        ahead; the TTL policy then deletes them. Their ids start with ``demo-`` or ``sim-``."""
        return {"expires_at": self._now() + DEMO_DATA_KEPT} if id.startswith((DEMO_PREFIX, SIM_PREFIX)) else {}

    def _where(self, collection: str, **equals):
        query = self._col(collection)
        for field, value in equals.items():
            query = query.where(filter=_eq(field, value))
        return query

    @staticmethod
    def _count(query) -> int:
        return int(query.count().get()[0][0].value)

    # users
    def get_user(self, uid: str) -> User | None:
        return self._get("users", uid, User)

    def put_user(self, u: User) -> None:
        self._put("users", u.uid, u)

    def find_user_by_link_code(self, code: str) -> User | None:
        if not code:
            return None
        for snap in self._where("users", telegram_link_code=code).limit(1).stream():
            return User.model_validate(snap.to_dict())
        return None

    # persons and neighbours
    def get_person(self, id: str) -> Person | None:
        return self._get("persons", id, Person)

    def put_person(self, p: Person) -> None:
        self._put("persons", p.id, p, **self._demo_ttl(p.id))

    def list_active_persons(self) -> list[Person]:
        query = self._where("persons", status=PersonStatus.ACTIVE.value, demo=False, sim=False)
        return [Person.model_validate(s.to_dict()) for s in query.stream()]

    def list_persons_by_owner(self, uid: str) -> list[Person]:
        return [Person.model_validate(s.to_dict()) for s in self._where("persons", owner_uid=uid).stream()]

    def list_neighbors(self, person_id: str) -> list[Neighbor]:
        found = [Neighbor.model_validate(s.to_dict()) for s in self._where("neighbors", person_id=person_id).stream()]
        return sorted(found, key=lambda n: n.order)

    def get_neighbor(self, id: str) -> Neighbor | None:
        return self._get("neighbors", id, Neighbor)

    def put_neighbor(self, n: Neighbor) -> None:
        self._put("neighbors", n.id, n)

    # alerts
    def create_alert(self, a: Alert) -> bool:
        return self._create("alerts", a.id, a)

    def alert_exists(self, alert_id: str) -> bool:
        return bool(self._col("alerts").document(alert_id).get().exists)

    def get_alert(self, alert_id: str) -> Alert | None:
        return self._get("alerts", alert_id, Alert)

    # episodes
    def create_episode(self, e: Episode) -> bool:
        return self._create("episodes", e.id, e, **self._demo_ttl(e.id))

    def get_episode(self, id: str) -> Episode | None:
        return self._get("episodes", id, Episode)

    def update_episode(self, id: str, fn: Callable[[Episode], Episode | None]) -> Episode | None:
        ref = self._col("episodes").document(id)

        @firestore.transactional
        def run(transaction) -> Episode | None:
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return None
            updated = fn(Episode.model_validate(snap.to_dict()))
            if updated is None:
                return None
            transaction.set(ref, updated.model_dump(mode="json") | self._demo_ttl(id))
            return updated

        return run(self._db.transaction())

    def list_episodes(self, person_id: str, limit: int = 20) -> list[Episode]:
        found = [Episode.model_validate(s.to_dict()) for s in self._where("episodes", person_id=person_id).stream()]
        found.sort(key=lambda e: e.created_at, reverse=True)
        return found[:limit]

    def list_open_episodes(self) -> list[Episode]:
        still_open = [s.value for s in EpisodeState if s != EpisodeState.CLOSED]
        query = self._col("episodes").where(filter=FieldFilter("state", "in", still_open))
        return [Episode.model_validate(s.to_dict()) for s in query.stream()]

    # calls
    def put_call(self, c: CallRecord) -> None:
        episode = self.get_episode(c.episode_id)
        self._put(
            "calls",
            c.id,
            c,
            person_id=episode.person_id if episode else "",
            day=c.created_at.astimezone(UTC).date().isoformat(),
            **self._demo_ttl(c.episode_id),
        )

    def get_call(self, id: str) -> CallRecord | None:
        return self._get("calls", id, CallRecord)

    def patch_call(self, id: str, **fields) -> None:
        plain = {k: (v.value if isinstance(v, Enum) else v) for k, v in fields.items()}
        try:
            self._col("calls").document(id).update(plain)
        except gexc.NotFound:
            return
        if plain.get("status") == "placed":
            # Only real calls are ever "placed"; this feeds the public counters.
            today = self._now().astimezone(UTC).date().isoformat()
            self._col("stats").document(_COUNTERS).set(
                {"calls_total": firestore.Increment(1), "calls_by_day": {today: firestore.Increment(1)}},
                merge=True,
            )

    def complete_call(
        self, id: str, answer: Answer, transcript: str | None, recording_key: str | None
    ) -> bool:
        ref = self._col("calls").document(id)

        @firestore.transactional
        def run(transaction) -> bool:
            snap = ref.get(transaction=transaction)
            if not snap.exists or snap.get("status") == "completed":
                return False
            transaction.update(
                ref,
                {
                    "status": "completed",
                    "answer": answer.value,
                    "transcript": transcript,
                    "recording_key": recording_key,
                },
            )
            return True

        return run(self._db.transaction())

    def count_person_calls(self, person_id: str, day: date) -> int:
        query = self._where("calls", person_id=person_id, day=day.isoformat(), target=Target.PERSON.value)
        return sum(1 for snap in query.stream() if snap.get("status") != "blocked")

    def list_calls_for_episode(self, episode_id: str) -> list[CallRecord]:
        found = [CallRecord.model_validate(s.to_dict()) for s in self._where("calls", episode_id=episode_id).stream()]
        return sorted(found, key=lambda c: c.created_at)

    # timeline
    def add_timeline(self, episode_id: str, type: str, public: dict[str, str]) -> None:
        rows = self._col("episodes").document(episode_id).collection("timeline")
        row = {"type": type, "public": dict(public), "at": firestore.SERVER_TIMESTAMP}
        rows.add(row | self._demo_ttl(episode_id))

    def list_timeline(self, episode_id: str, after_seq: int = 0) -> list[TimelineEvent]:
        rows = self._col("episodes").document(episode_id).collection("timeline").order_by("at").stream()
        events = [
            TimelineEvent(seq=i, at=snap.get("at"), type=snap.get("type"), public=snap.get("public") or {})
            for i, snap in enumerate(rows, start=1)
        ]
        return [e for e in events if e.seq > after_seq]

    # runtime switches
    def get_runtime(self) -> Runtime:
        snap = self._col("runtime").document("config").get()
        return Runtime.model_validate(snap.to_dict()) if snap.exists else Runtime()

    # demo
    def put_demo_session(self, s: DemoSession) -> None:
        self._put("demo_sessions", s.token, s, expires_at=s.expires_at)

    def get_demo_session_by_token(self, token: str) -> DemoSession | None:
        return self._get("demo_sessions", token, DemoSession)

    def count_demo_runs(self, phone_hash: str, day: date) -> int:
        return self._count(self._where("demo_runs", phone_hash=phone_hash, day=day.isoformat()))

    def count_ip_runs(self, ip_hash: str, day: date) -> int:
        return self._count(self._where("demo_runs", ip_hash=ip_hash, day=day.isoformat()))

    def record_demo_run(self, phone_hash: str, ip_hash: str, day: date) -> None:
        self._col("demo_runs").add(
            {
                "phone_hash": phone_hash,
                "ip_hash": ip_hash,
                "day": day.isoformat(),
                "expires_at": self._now() + timedelta(days=2),
            }
        )

    def add_demo_spend(self, day: date, usd: float) -> float:
        self._col("demo_days").document(day.isoformat()).set({"usd": firestore.Increment(usd)}, merge=True)
        return self.get_demo_spend(day)

    def get_demo_spend(self, day: date) -> float:
        snap = self._col("demo_days").document(day.isoformat()).get()
        return round(float(snap.get("usd")), 4) if snap.exists else 0.0

    # public counters
    def stats(self) -> dict[str, int]:
        snap = self._col("stats").document(_COUNTERS).get()
        counters = snap.to_dict() if snap.exists else {}
        today = self._now().astimezone(UTC).date().isoformat()
        active = self._where("persons", status=PersonStatus.ACTIVE.value, demo=False, sim=False)
        return {
            "calls_today": int((counters.get("calls_by_day") or {}).get(today, 0)),
            "calls_total": int(counters.get("calls_total", 0)),
            "persons_active": self._count(active),
        }

    def put_stat_doc(self, name: str, data: dict) -> None:
        self._col("stats").document(name).set(dict(data))

    def get_stat_doc(self, name: str) -> dict | None:
        snap = self._col("stats").document(name).get()
        return snap.to_dict() if snap.exists else None
