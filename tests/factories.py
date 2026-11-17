"""Builders for test data. Phone numbers here are fakes; real numbers never go in fixtures."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from kuckuc.models import (
    Alert,
    CallRecord,
    Episode,
    EpisodeKind,
    Neighbor,
    Person,
    PersonStatus,
    Question,
    ScriptKind,
    Target,
)

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
TODAY: date = T0.date()
TOMORROW: date = TODAY + timedelta(days=1)

PERSON_PHONE = "+15005550006"
NEIGHBOR_PHONE = "+15005550007"


def make_person(**over) -> Person:
    base = dict(
        id="p1",
        owner_uid="u1",
        name="baba Milena",
        family_name="vaš unuk Petar",
        gender="f",
        phone=PERSON_PHONE,
        lang="sr",
        lat=44.81,
        lon=20.46,
        tz="Europe/Belgrade",
        place="southern Serbia",
        status=PersonStatus.ACTIVE,
    )
    return Person(**(base | over))


def make_neighbor(**over) -> Neighbor:
    base = dict(
        id="n1",
        person_id="p1",
        order=0,
        name="Dragan",
        phone=NEIGHBOR_PHONE,
        status=PersonStatus.ACTIVE,
    )
    return Neighbor(**(base | over))


def make_episode(**over) -> Episode:
    base = dict(
        id="e1",
        person_id="p1",
        kind=EpisodeKind.CHECK,
        created_at=T0,
        updated_at=T0,
    )
    return Episode(**(base | over))


def make_call(**over) -> CallRecord:
    base = dict(
        id="c1",
        episode_id="e1",
        target=Target.PERSON,
        neighbor_id=None,
        script=ScriptKind.CHECK,
        question=Question.WELLBEING,
        provider="twilio",
        to=PERSON_PHONE,
        lang="sr",
        params={"name": "baba Milena", "gender": "f", "hazard": "cold"},
        created_at=T0,
    )
    return CallRecord(**(base | over))


def make_alert(**over) -> Alert:
    base = dict(person_id="p1", hazard="cold", day=TOMORROW, value=-12.4, created_at=T0)
    data = base | over
    data.setdefault("id", f"{data['person_id']}:{data['hazard']}:{data['day']}")
    return Alert(**data)
