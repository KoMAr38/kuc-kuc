"""Enums and data models shared by every service."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

Lang = Literal["sr", "en"]
Gender = Literal["f", "m"]
Hazard = Literal["heat", "cold", "wind", "snow"]


class PersonStatus(StrEnum):
    PENDING_CONSENT = "pending_consent"
    ACTIVE = "active"
    PAUSED = "paused"
    DECLINED = "declined"


class EpisodeKind(StrEnum):
    INTRO = "intro"
    INTRO_NEIGHBOR = "intro_neighbor"
    HEADSUP = "headsup"
    CHECK = "check"
    MANUAL = "manual"
    DEMO = "demo"
    SIM = "sim"


class EpisodeState(StrEnum):
    SCHEDULED = "scheduled"
    CALLING_PERSON = "calling_person"
    CALLING_NEIGHBOR = "calling_neighbor"
    WAITING_FOLLOWUP = "waiting_followup"
    CLOSED = "closed"


class Result(StrEnum):
    OK = "ok"
    OK_BY_NEIGHBOR = "ok_by_neighbor"
    NEEDS_HELP = "needs_help"
    UNREACHABLE = "unreachable"
    UNCONFIRMED = "unconfirmed"
    NOT_DELIVERED = "not_delivered"
    CONSENTED = "consented"
    DECLINED = "declined"
    NO_CONSENT = "no_consent"
    BLOCKED = "blocked"


class Answer(StrEnum):
    OK = "ok"
    NEEDS_HELP = "needs_help"
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"
    NO_ANSWER = "no_answer"


class Question(StrEnum):
    WELLBEING = "wellbeing"
    YES_NO = "yes_no"
    NEIGHBOR_REPORT = "neighbor_report"


class Target(StrEnum):
    PERSON = "person"
    NEIGHBOR = "neighbor"


class ScriptKind(StrEnum):
    INTRO_PERSON = "intro_person"
    INTRO_NEIGHBOR = "intro_neighbor"
    HEADSUP = "headsup"
    CHECK = "check"
    CHECK_MANUAL = "check_manual"
    NEIGHBOR_ASK_SILENT = "neighbor_ask_silent"
    NEIGHBOR_ASK_UNWELL = "neighbor_ask_unwell"
    NEIGHBOR_FOLLOWUP = "neighbor_followup"


class User(BaseModel):
    uid: str
    email: str
    lang: Lang = "en"
    telegram_chat_id: str | None = None
    telegram_link_code: str | None = None


class Person(BaseModel):
    """Someone we phone. No street address is ever stored: lat/lon are only for the forecast."""

    id: str
    owner_uid: str
    name: str  # as spoken in the call, e.g. "baba Milena"
    family_name: str  # how the family member is named to the person, e.g. "vaš unuk Petar"
    gender: Gender
    phone: str
    lang: Lang
    lat: float
    lon: float
    tz: str
    place: str
    status: PersonStatus
    demo: bool = False
    sim: bool = False


class Neighbor(BaseModel):
    id: str
    person_id: str
    order: int
    name: str
    phone: str
    status: PersonStatus


class Alert(BaseModel):
    id: str  # f"{person_id}:{hazard}:{day}"
    person_id: str
    hazard: Hazard
    day: date
    value: float
    created_at: datetime


class Episode(BaseModel):
    """One run of the escalation chain. Scheduled ids are f"{person_id}:{day}:{kind}"; others are uuid4 hex."""

    id: str
    person_id: str
    kind: EpisodeKind
    state: EpisodeState = EpisodeState.SCHEDULED
    result: Result | None = None
    hazard: Hazard | None = None
    value: float | None = None
    neighbor_id: str | None = None
    person_attempts: int = 0
    neighbor_index: int = 0
    # The neighbours who had said yes when the episode started, in order; None until it starts.
    neighbor_ids: list[str] | None = None
    # How many steps were saved, and the actions of the last one until they are carried out.
    steps: int = 0
    pending: list[dict] | None = None
    neighbor_attempts: int = 0
    followup_attempts: int = 0
    unwell: bool = False
    seen_event_ids: list[str] = Field(default_factory=list)
    sim_answers: list[str] = Field(default_factory=list)
    demo_session_id: str | None = None
    created_at: datetime
    updated_at: datetime
    due_at: datetime | None = None  # when its first call was meant to go; the watchdog compares against it


class CallRecord(BaseModel):
    id: str
    episode_id: str
    target: Target
    neighbor_id: str | None
    script: ScriptKind
    question: Question
    provider: Literal["twilio", "simulated"]
    to: str
    lang: Lang
    params: dict[str, str]
    status: Literal["queued", "dialing", "placed", "completed", "blocked"] = "queued"
    answer: Answer | None = None
    transcript: str | None = None
    recording_key: str | None = None
    provider_sid: str | None = None
    created_at: datetime
    # When dialling began. A call is queued long before it rings (a follow-up 20 minutes ahead).
    placed_at: datetime | None = None
    # The date where the person lives when the call was queued (YYYY-MM-DD): the daily cap counts it.
    local_day: str | None = None


class TimelineEvent(BaseModel):
    """One row the family (or a demo visitor) can see. ``public`` never holds a phone number or a name."""

    seq: int
    at: datetime
    type: str
    public: dict[str, str]


class DemoSession(BaseModel):
    id: str
    token: str
    phone_hash: str
    country: str
    expires_at: datetime


class Runtime(BaseModel):
    kill_switch: bool = False


# An episode is saved as closed a moment before its last history rows (the message to the family,
# "Done"); a page that stopped watching then never showed them. So a page watches until the closing
# row is there, or, should that one write have failed for good, until two minutes after the close.
CLOSING_ROWS_WAIT = timedelta(minutes=2)


def finished_for_pages(episode: Episode, rows: list[TimelineEvent], now: datetime) -> bool:
    if any(row.type == "episode_closed" for row in rows):
        return True
    return episode.state == EpisodeState.CLOSED and now - episode.updated_at > CLOSING_ROWS_WAIT
