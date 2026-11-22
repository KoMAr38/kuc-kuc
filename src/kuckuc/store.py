"""Storage interface and the in-memory version used by tests and local preview."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Protocol

from kuckuc.models import (
    BudgetHold,
    Payer,
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


class Store(Protocol):
    def get_user(self, uid: str) -> User | None: ...
    def put_user(self, u: User) -> None: ...
    def find_user_by_link_code(self, code: str) -> User | None: ...

    def get_person(self, id: str) -> Person | None: ...
    def put_person(self, p: Person) -> None: ...
    def list_active_persons(self) -> list[Person]: ...
    def list_persons_by_owner(self, uid: str) -> list[Person]: ...

    def list_neighbors(self, person_id: str) -> list[Neighbor]: ...
    def get_neighbor(self, id: str) -> Neighbor | None: ...
    def put_neighbor(self, n: Neighbor) -> None: ...

    def create_alert(self, a: Alert) -> bool: ...
    def get_alert(self, alert_id: str) -> Alert | None: ...

    def create_episode(self, e: Episode) -> bool: ...
    def get_episode(self, id: str) -> Episode | None: ...
    def update_episode(self, id: str, fn: Callable[[Episode], Episode | None]) -> Episode | None: ...
    def list_episodes(self, person_id: str, limit: int = 20) -> list[Episode]: ...
    def list_open_episodes(self) -> list[Episode]: ...

    def create_call(self, c: CallRecord) -> bool: ...
    def get_call(self, id: str) -> CallRecord | None: ...
    def patch_call(self, id: str, **fields) -> None: ...
    def complete_call(
        self, id: str, answer: Answer, transcript: str | None, recording_key: str | None
    ) -> bool: ...
    def count_person_calls(self, person_id: str, day: date) -> int: ...
    def list_calls_for_episode(self, episode_id: str) -> list[CallRecord]: ...
    def claim_recording(self, call_id: str, n: int) -> bool: ...
    def start_dialing(self, call_id: str, at: datetime) -> bool: ...
    def mark_placed(self, call_id: str, provider_sid: str) -> bool: ...

    def add_timeline(self, episode_id: str, type: str, public: dict[str, str]) -> None: ...
    def list_timeline(self, episode_id: str, after_seq: int = 0) -> list[TimelineEvent]: ...

    def get_runtime(self) -> Runtime: ...

    def put_demo_session(self, s: DemoSession) -> None: ...
    def get_demo_session_by_token(self, token: str) -> DemoSession | None: ...
    def demo_count(self, day: date, key: str) -> int: ...
    def reserve_demo(
        self, day: date, quotas: list[tuple[str, int, str]], usd: float, cap_usd: float
    ) -> str | None: ...
    def add_demo_spend(self, day: date, usd: float) -> float: ...
    def get_demo_spend(self, day: date) -> float: ...

    def get_payer(self, id: str) -> Payer | None: ...
    def put_payer(self, p: Payer) -> None: ...
    def find_payer_by_invite(self, code: str) -> Payer | None: ...
    def list_payers_for_staff(self, uid: str) -> list[Payer]: ...
    def reserve_budget(self, hold: BudgetHold, caps: list[tuple[str, float]]) -> str | None:
        """Holds ``hold.usd`` in every ``(key, cap)`` budget, all or nothing, once per episode.
        None when held (now or by an earlier run of the same step), "budget" when it would pass a cap,
        "busy" when the database could not settle the race in time."""
        ...
    def settle_budget(self, episode_id: str, spent_usd: float) -> bool:
        """Swaps the episode's hold for what it spent, once. False when there is nothing left to settle."""
        ...
    def list_open_holds(self) -> list[BudgetHold]: ...
    def get_hold(self, episode_id: str) -> BudgetHold | None: ...
    def release_claim(self, key: str) -> None: ...
    def get_budget(self, key: str) -> dict[str, float]: ...
    def claim_once(self, key: str) -> bool:
        """True for the first caller only. A claim is kept for a week."""
        ...

    def stats(self) -> dict[str, int]: ...
    def put_stat_doc(self, name: str, data: dict) -> None: ...
    def get_stat_doc(self, name: str) -> dict | None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryStore:
    """Everything in dictionaries. Objects are copied on the way in and out, like a real database."""

    def __init__(self, now: Callable[[], datetime] = _utc_now) -> None:
        self._now = now
        self.runtime = Runtime()
        self._users: dict[str, User] = {}
        self._persons: dict[str, Person] = {}
        self._neighbors: dict[str, Neighbor] = {}
        self._alerts: dict[str, Alert] = {}
        self._episodes: dict[str, Episode] = {}
        self._calls: dict[str, CallRecord] = {}
        self._recording_claims: set[tuple[str, int]] = set()
        self._timeline: dict[str, list[TimelineEvent]] = {}
        self._demo_sessions: dict[str, DemoSession] = {}
        self._demo_counts: dict[tuple[date, str], int] = {}
        self._demo_spend: dict[date, float] = {}
        self._lock = threading.Lock()
        self._stat_docs: dict[str, dict] = {}
        self._placed_by_day: dict[str, int] = {}  # as Firestore counts: one per call that rang, by UTC day
        self._payers: dict[str, Payer] = {}
        self._budgets: dict[str, dict[str, float]] = {}
        self._holds: dict[str, BudgetHold] = {}
        self._claims: set[str] = set()

    # users
    def get_user(self, uid: str) -> User | None:
        return _copy(self._users.get(uid))

    def put_user(self, u: User) -> None:
        self._users[u.uid] = u.model_copy(deep=True)

    def find_user_by_link_code(self, code: str) -> User | None:
        if not code:
            return None
        return _copy(next((u for u in self._users.values() if u.telegram_link_code == code), None))

    # persons and neighbours
    def get_person(self, id: str) -> Person | None:
        return _copy(self._persons.get(id))

    def put_person(self, p: Person) -> None:
        self._persons[p.id] = p.model_copy(deep=True)

    def list_active_persons(self) -> list[Person]:
        return [
            p.model_copy(deep=True)
            for p in self._persons.values()
            if p.status == PersonStatus.ACTIVE and not p.demo and not p.sim
        ]

    def list_persons_by_owner(self, uid: str) -> list[Person]:
        return [p.model_copy(deep=True) for p in self._persons.values() if p.owner_uid == uid]

    def list_neighbors(self, person_id: str) -> list[Neighbor]:
        found = [n for n in self._neighbors.values() if n.person_id == person_id]
        return [n.model_copy(deep=True) for n in sorted(found, key=lambda n: n.order)]

    def get_neighbor(self, id: str) -> Neighbor | None:
        return _copy(self._neighbors.get(id))

    def put_neighbor(self, n: Neighbor) -> None:
        self._neighbors[n.id] = n.model_copy(deep=True)

    # alerts
    def create_alert(self, a: Alert) -> bool:
        if a.id in self._alerts:
            return False
        self._alerts[a.id] = a.model_copy(deep=True)
        return True


    def get_alert(self, alert_id: str) -> Alert | None:
        return _copy(self._alerts.get(alert_id))

    # episodes
    def create_episode(self, e: Episode) -> bool:
        if e.id in self._episodes:
            return False
        self._episodes[e.id] = e.model_copy(deep=True)
        return True

    def get_episode(self, id: str) -> Episode | None:
        return _copy(self._episodes.get(id))

    def update_episode(self, id: str, fn: Callable[[Episode], Episode | None]) -> Episode | None:
        current = self._episodes.get(id)
        if current is None:
            return None
        updated = fn(current.model_copy(deep=True))
        if updated is None:
            return None
        self._episodes[id] = updated.model_copy(deep=True)
        return updated.model_copy(deep=True)

    def list_episodes(self, person_id: str, limit: int = 20) -> list[Episode]:
        # Insertion order breaks ties, so two episodes created in the same instant keep their order.
        found = [(e.created_at, i, e) for i, e in enumerate(self._episodes.values()) if e.person_id == person_id]
        found.sort(key=lambda item: item[:2], reverse=True)
        return [e.model_copy(deep=True) for _, _, e in found[:limit]]

    def list_open_episodes(self) -> list[Episode]:
        return [e.model_copy(deep=True) for e in self._episodes.values() if e.state != EpisodeState.CLOSED]

    # calls
    def create_call(self, c: CallRecord) -> bool:
        """Stores the call only if it does not exist yet."""
        with self._lock:
            if c.id in self._calls:
                return False
            self._calls[c.id] = c.model_copy(deep=True)
            return True

    def get_call(self, id: str) -> CallRecord | None:
        return _copy(self._calls.get(id))

    def patch_call(self, id: str, **fields) -> None:
        if id in self._calls:
            self._calls[id] = self._calls[id].model_copy(update=fields)

    def complete_call(
        self, id: str, answer: Answer, transcript: str | None, recording_key: str | None
    ) -> bool:
        call = self._calls.get(id)
        if call is None or call.status == "completed":
            return False
        self._calls[id] = call.model_copy(
            update={
                "status": "completed",
                "answer": answer,
                "transcript": transcript,
                "recording_key": recording_key,
            }
        )
        return True

    def count_person_calls(self, person_id: str, day: date) -> int:
        """Calls to the person themself on that day where they live (older calls: the UTC day).
        Blocked calls never rang, so they do not count."""
        count = 0
        for c in self._calls.values():
            if c.target != Target.PERSON or c.status == "blocked":
                continue
            if (c.local_day or c.created_at.astimezone(UTC).date().isoformat()) != day.isoformat():
                continue
            ep = self._episodes.get(c.episode_id)
            if ep is not None and ep.person_id == person_id:
                count += 1
        return count

    def list_calls_for_episode(self, episode_id: str) -> list[CallRecord]:
        found = [c for c in self._calls.values() if c.episode_id == episode_id]
        return [c.model_copy(deep=True) for c in sorted(found, key=lambda c: c.created_at)]

    def start_dialing(self, call_id: str, at: datetime) -> bool:
        """Moves a queued call to "dialing"; True for the first caller only, so a task delivered
        twice rings once."""
        with self._lock:  # Firestore does this in a transaction; parallel requests must see one winner here too
            call = self._calls.get(call_id)
            if call is None or call.status != "queued":
                return False
            self._calls[call_id] = call.model_copy(update={"status": "dialing", "placed_at": at})
            return True

    def mark_placed(self, call_id: str, provider_sid: str) -> bool:
        """"dialing" becomes "placed"; any other status (a call already finished) stays as it is."""
        with self._lock:
            call = self._calls.get(call_id)
            if call is None or call.status != "dialing":
                return False
            self._calls[call_id] = call.model_copy(update={"status": "placed", "provider_sid": provider_sid})
            day = self._now().astimezone(UTC).date().isoformat()
            self._placed_by_day[day] = self._placed_by_day.get(day, 0) + 1
            return True

    def claim_recording(self, call_id: str, n: int) -> bool:
        """True for the first caller only: a recording delivered twice is handled once."""
        if (call_id, n) in self._recording_claims:
            return False
        self._recording_claims.add((call_id, n))
        return True

    def all_calls(self) -> list[CallRecord]:
        return [c.model_copy(deep=True) for c in self._calls.values()]

    # timeline
    def add_timeline(self, episode_id: str, type: str, public: dict[str, str]) -> None:
        events = self._timeline.setdefault(episode_id, [])
        events.append(TimelineEvent(seq=len(events) + 1, at=self._now(), type=type, public=dict(public)))

    def list_timeline(self, episode_id: str, after_seq: int = 0) -> list[TimelineEvent]:
        return [e.model_copy(deep=True) for e in self._timeline.get(episode_id, []) if e.seq > after_seq]

    # runtime switches
    def get_runtime(self) -> Runtime:
        return self.runtime.model_copy()

    # demo
    def put_demo_session(self, s: DemoSession) -> None:
        self._demo_sessions[s.token] = s.model_copy(deep=True)

    def get_demo_session_by_token(self, token: str) -> DemoSession | None:
        return _copy(self._demo_sessions.get(token))

    def demo_count(self, day: date, key: str) -> int:
        return self._demo_counts.get((day, key), 0)

    def reserve_demo(
        self, day: date, quotas: list[tuple[str, int, str]], usd: float, cap_usd: float
    ) -> str | None:
        """Counts one use of every ``(key, limit, refusal)`` quota and adds ``usd`` to the day's
        spend, all at once or not at all. Returns the refusal of the first quota at its limit,
        "budget" when the spend would pass ``cap_usd``, or None when reserved."""
        with self._lock:
            for key, limit, refusal in quotas:
                if self._demo_counts.get((day, key), 0) >= limit:
                    return refusal
            if usd and self._demo_spend.get(day, 0.0) + usd > cap_usd:
                return "budget"
            for key, _, _ in quotas:
                self._demo_counts[(day, key)] = self._demo_counts.get((day, key), 0) + 1
            if usd:
                self._demo_spend[day] = round(self._demo_spend.get(day, 0.0) + usd, 4)
            return None

    def add_demo_spend(self, day: date, usd: float) -> float:
        with self._lock:
            self._demo_spend[day] = round(self._demo_spend.get(day, 0.0) + usd, 4)
            return self._demo_spend[day]

    def get_demo_spend(self, day: date) -> float:
        return self._demo_spend.get(day, 0.0)

    # payers and call budgets
    def get_payer(self, id: str) -> Payer | None:
        return _copy(self._payers.get(id))

    def put_payer(self, p: Payer) -> None:
        self._payers[p.id] = p.model_copy(deep=True)

    def find_payer_by_invite(self, code: str) -> Payer | None:
        if not code:
            return None
        return _copy(next((p for p in self._payers.values() if p.invite_code == code), None))

    def list_payers_for_staff(self, uid: str) -> list[Payer]:
        return [p.model_copy(deep=True) for p in self._payers.values() if uid in p.staff_uids]

    def reserve_budget(self, hold: BudgetHold, caps: list[tuple[str, float]]) -> str | None:
        with self._lock:
            if hold.episode_id in self._holds:
                return None
            for key, cap in caps:
                budget = self._budgets.get(key, {"reserved": 0.0, "spent": 0.0})
                if budget["reserved"] + budget["spent"] + hold.usd > cap + 1e-9:
                    return "budget"
            for key, _ in caps:
                budget = self._budgets.setdefault(key, {"reserved": 0.0, "spent": 0.0})
                budget["reserved"] = round(budget["reserved"] + hold.usd, 4)
            self._holds[hold.episode_id] = hold.model_copy(update={"keys": [key for key, _ in caps]})
            return None

    def settle_budget(self, episode_id: str, spent_usd: float) -> bool:
        with self._lock:
            hold = self._holds.get(episode_id)
            if hold is None or hold.settled:
                return False
            for key in hold.keys:
                budget = self._budgets[key]
                budget["reserved"] = round(max(0.0, budget["reserved"] - hold.usd), 4)
                budget["spent"] = round(budget["spent"] + spent_usd, 4)
            self._holds[episode_id] = hold.model_copy(update={"settled": True})
            return True

    def list_open_holds(self) -> list[BudgetHold]:
        return [h.model_copy(deep=True) for h in self._holds.values() if not h.settled]

    def get_budget(self, key: str) -> dict[str, float]:
        return dict(self._budgets.get(key, {"reserved": 0.0, "spent": 0.0}))

    def get_hold(self, episode_id: str) -> BudgetHold | None:
        return _copy(self._holds.get(episode_id))

    def release_claim(self, key: str) -> None:
        with self._lock:
            self._claims.discard(key)

    def claim_once(self, key: str) -> bool:
        with self._lock:
            if key in self._claims:
                return False
            self._claims.add(key)
            return True

    # public counters
    def stats(self) -> dict[str, int]:
        today = self._now().astimezone(UTC).date().isoformat()
        return {
            "calls_today": self._placed_by_day.get(today, 0),
            "calls_total": sum(self._placed_by_day.values()),
            "persons_active": len(self.list_active_persons()),
        }

    def put_stat_doc(self, name: str, data: dict) -> None:
        self._stat_docs[name] = dict(data)

    def get_stat_doc(self, name: str) -> dict | None:
        doc = self._stat_docs.get(name)
        return dict(doc) if doc is not None else None


def _copy(model):
    return model.model_copy(deep=True) if model is not None else None
