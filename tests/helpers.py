"""Shortcuts for driving the whole system in memory."""

from __future__ import annotations

from factories import T0, make_call, make_episode, make_neighbor, make_person

from kuckuc.local import LocalSystem, build_local
from kuckuc.models import Answer, CallRecord


def build_sys() -> LocalSystem:
    """One person under watch (p1) with two neighbours who have agreed (n1, then n2)."""
    sys = build_local(now=T0)
    sys.deps.store.put_person(make_person(id="p1"))
    sys.deps.store.put_neighbor(make_neighbor(id="n1", order=0, name="Dragan", phone="+15005550007"))
    sys.deps.store.put_neighbor(make_neighbor(id="n2", order=1, name="Rada", phone="+15005550008"))
    return sys


def request_check(sys: LocalSystem, kind: str = "manual", **data) -> str:
    """Ask for a check the way the family page or the forecast does; returns the episode id."""
    before = {e.id for e in sys.deps.store.list_episodes("p1", limit=100)}
    sys.deps.bus.publish("check.requested", {"person_id": "p1", "kind": kind, **data})
    (new,) = [e.id for e in sys.deps.store.list_episodes("p1", limit=100) if e.id not in before]
    return new


def real_calls(sys: LocalSystem) -> list[CallRecord]:
    return [c for c in sys.deps.store.all_calls() if not c.id.startswith("seed")]


def only_call(sys: LocalSystem) -> CallRecord:
    (call,) = real_calls(sys)
    return call


def only_new_call(sys: LocalSystem) -> CallRecord:
    (call,) = [c for c in real_calls(sys) if c.status == "queued"]
    return call


def seed_person_calls(sys: LocalSystem, person_id: str, n: int) -> None:
    """Calls the person already received today."""
    sys.deps.store.create_episode(make_episode(id="seed-ep", person_id=person_id))
    for i in range(n):
        sys.deps.store.create_call(make_call(id=f"seed-{i}", episode_id="seed-ep", status="completed"))


def complete(sys: LocalSystem, call: CallRecord, answer: Answer) -> None:
    """What the voice service does when a call ends."""
    assert sys.deps.store.complete_call(call.id, answer, None, None)
    sys.deps.bus.publish(
        "call.completed",
        {
            "call_id": call.id,
            "episode_id": call.episode_id,
            "target": call.target.value,
            "script": call.script.value,
            "answer": answer.value,
        },
    )
