"""The whole escalation as one pure function.

``step`` takes an episode and something that happened, and returns the episode as it is now
plus what to do next. It has no clock and touches nothing outside itself, so every rule the
service follows (who is called, when, and when the family is told) can be read and tested here.

Attempts are counted when a call is placed. "At most 3 attempts" therefore reads as
``attempts < 3`` at the moment an unanswered call comes back.
"""

from __future__ import annotations

from dataclasses import dataclass

from kuckuc.models import Answer, Episode, EpisodeKind, EpisodeState, PersonStatus, Result, ScriptKind, Target

PERSON_RETRY_S = 600
PERSON_MAX_ATTEMPTS = 3
HEADSUP_RETRY_S = 1800
HEADSUP_MAX_ATTEMPTS = 2
NEIGHBOR_RETRY_S = 300
NEIGHBOR_MAX_ATTEMPTS = 2
FOLLOWUP_DELAY_S = 1200
FOLLOWUP_RETRY_S = 300
FOLLOWUP_MAX_ATTEMPTS = 2


# What can happen to an episode.
@dataclass(frozen=True)
class Due:
    """Its scheduled moment has come."""


@dataclass(frozen=True)
class CallDone:
    target: Target
    script: ScriptKind
    answer: Answer


@dataclass(frozen=True)
class CallBlocked:
    reason: str


Input = Due | CallDone | CallBlocked


# What the engine is asked to do in return.
@dataclass(frozen=True)
class PlaceCall:
    target: Target
    neighbor_index: int | None
    script: ScriptKind
    delay_s: int = 0


@dataclass(frozen=True)
class Notify:
    key: str
    urgent: bool = False


@dataclass(frozen=True)
class SetStatus:
    target: Target
    status: PersonStatus


@dataclass(frozen=True)
class Close:
    result: Result


Action = PlaceCall | Notify | SetStatus | Close

_ASKS = (ScriptKind.NEIGHBOR_ASK_SILENT, ScriptKind.NEIGHBOR_ASK_UNWELL)
_FIRST_SCRIPT = {
    EpisodeKind.INTRO: ScriptKind.INTRO_PERSON,
    EpisodeKind.HEADSUP: ScriptKind.HEADSUP,
    EpisodeKind.CHECK: ScriptKind.CHECK,
    EpisodeKind.MANUAL: ScriptKind.CHECK_MANUAL,
    EpisodeKind.DEMO: ScriptKind.CHECK,
    EpisodeKind.SIM: ScriptKind.CHECK,
}


def step(ep: Episode, ev: Input, neighbor_count: int) -> tuple[Episode, list[Action]]:
    """``neighbor_count`` is how many neighbours have agreed to be called."""
    if ep.state == EpisodeState.CLOSED:
        return ep, []
    if isinstance(ev, CallBlocked):
        if ev.reason == "refused":
            return _refused(ep, neighbor_count)
        if ev.reason in ("budget", "no_budget", "off_season") and ep.kind not in (
            EpisodeKind.INTRO,
            EpisodeKind.INTRO_NEIGHBOR,
        ):
            # No money for this payer's calls: nobody is being called, so the family must check now.
            key = "budget_off_season" if ev.reason == "off_season" else "budget_paused"
            return _close(ep, Result.BLOCKED, [Notify(key, urgent=True)])
        if ep.kind in (EpisodeKind.INTRO, EpisodeKind.INTRO_NEIGHBOR):
            # Nobody is waiting for help on an introduction: the family starts it again when they can.
            return _close(ep, Result.BLOCKED, [Notify("intro_blocked", urgent=False)])
        if ep.kind == EpisodeKind.HEADSUP and ep.state == EpisodeState.CALLING_PERSON:
            # The day before, while she is being called: nothing has happened yet, and tomorrow's
            # check still comes. Once she has said she is unwell, a blocked call is urgent like any other.
            return _close(ep, Result.NOT_DELIVERED, [Notify("headsup_blocked", urgent=False)])
        return _close(ep, Result.BLOCKED, [Notify("call_blocked", urgent=True)])
    if isinstance(ev, Due):
        return _start(ep) if ep.state == EpisodeState.SCHEDULED else (ep, [])

    # A finished call only counts if it is the call this episode is waiting for.
    # Anything else is a late or repeated report and changes nothing.
    if ep.kind == EpisodeKind.INTRO_NEIGHBOR:
        if ep.state == EpisodeState.CALLING_NEIGHBOR and ev.script == ScriptKind.INTRO_NEIGHBOR:
            return _consent(ep, ev.answer, Target.NEIGHBOR)
        return ep, []
    if ep.state == EpisodeState.CALLING_PERSON and ev.target == Target.PERSON:
        if ep.kind == EpisodeKind.INTRO:
            return _consent(ep, ev.answer, Target.PERSON)
        if ep.kind == EpisodeKind.HEADSUP:
            return _headsup_answer(ep, ev.answer, neighbor_count)
        return _check_answer(ep, ev.answer, neighbor_count)
    if ep.state == EpisodeState.CALLING_NEIGHBOR and ev.target == Target.NEIGHBOR and ev.script in _ASKS:
        return _ask_answer(ep, ev.answer, neighbor_count)
    if (
        ep.state == EpisodeState.WAITING_FOLLOWUP
        and ev.target == Target.NEIGHBOR
        and ev.script == ScriptKind.NEIGHBOR_FOLLOWUP
    ):
        return _followup_answer(ep, ev.answer)
    return ep, []


def _start(ep: Episode) -> tuple[Episode, list[Action]]:
    if ep.kind == EpisodeKind.INTRO_NEIGHBOR:
        ep = ep.model_copy(update={"state": EpisodeState.CALLING_NEIGHBOR, "neighbor_attempts": 1})
        return ep, [PlaceCall(Target.NEIGHBOR, 0, ScriptKind.INTRO_NEIGHBOR)]
    ep = ep.model_copy(update={"state": EpisodeState.CALLING_PERSON, "person_attempts": 1})
    return ep, [PlaceCall(Target.PERSON, None, _FIRST_SCRIPT[ep.kind])]


def _check_answer(ep: Episode, answer: Answer, neighbor_count: int) -> tuple[Episode, list[Action]]:
    if answer == Answer.OK:
        return _close(ep, Result.OK, [Notify("call_ok")])
    if answer == Answer.NEEDS_HELP:
        return _unwell(ep, neighbor_count)
    if answer == Answer.NO_ANSWER and ep.person_attempts < PERSON_MAX_ATTEMPTS:
        return _retry_person(ep, PERSON_RETRY_S)
    # Unclear, silent after three tries, or an answer that does not fit the question: send someone.
    return _call_neighbor(ep, 0, neighbor_count, [])


def _headsup_answer(ep: Episode, answer: Answer, neighbor_count: int) -> tuple[Episode, list[Action]]:
    if answer == Answer.OK:
        return _close(ep, Result.OK, [Notify("headsup_ok")])
    if answer == Answer.NEEDS_HELP:
        return _unwell(ep, neighbor_count)
    # Nothing has happened yet, so a missed heads-up is retried once and then only reported.
    if ep.person_attempts < HEADSUP_MAX_ATTEMPTS:
        return _retry_person(ep, HEADSUP_RETRY_S)
    return _close(ep, Result.NOT_DELIVERED, [Notify("headsup_missed")])


def _unwell(ep: Episode, neighbor_count: int) -> tuple[Episode, list[Action]]:
    ep = ep.model_copy(update={"unwell": True})
    return _call_neighbor(ep, 0, neighbor_count, [Notify("call_unwell", urgent=True)])


def _retry_person(ep: Episode, delay_s: int) -> tuple[Episode, list[Action]]:
    ep = ep.model_copy(update={"person_attempts": ep.person_attempts + 1})
    return ep, [PlaceCall(Target.PERSON, None, _FIRST_SCRIPT[ep.kind], delay_s)]


def _call_neighbor(
    ep: Episode, index: int, neighbor_count: int, before: list[Action]
) -> tuple[Episode, list[Action]]:
    if index >= neighbor_count:
        key = "nobody_reachable_unwell" if ep.unwell else "nobody_reachable"
        return _close(ep, Result.UNREACHABLE, [*before, Notify(key, urgent=True)])
    ep = ep.model_copy(
        update={"state": EpisodeState.CALLING_NEIGHBOR, "neighbor_index": index, "neighbor_attempts": 1}
    )
    return ep, [*before, PlaceCall(Target.NEIGHBOR, index, _ask_script(ep)), Notify("neighbor_called")]


def _ask_script(ep: Episode) -> ScriptKind:
    return ScriptKind.NEIGHBOR_ASK_UNWELL if ep.unwell else ScriptKind.NEIGHBOR_ASK_SILENT


def _ask_answer(ep: Episode, answer: Answer, neighbor_count: int) -> tuple[Episode, list[Action]]:
    if answer == Answer.YES:
        ep = ep.model_copy(update={"state": EpisodeState.WAITING_FOLLOWUP, "followup_attempts": 1})
        return ep, [
            Notify("neighbor_going"),
            PlaceCall(Target.NEIGHBOR, ep.neighbor_index, ScriptKind.NEIGHBOR_FOLLOWUP, FOLLOWUP_DELAY_S),
        ]
    if answer == Answer.NO_ANSWER and ep.neighbor_attempts < NEIGHBOR_MAX_ATTEMPTS:
        ep = ep.model_copy(update={"neighbor_attempts": ep.neighbor_attempts + 1})
        return ep, [PlaceCall(Target.NEIGHBOR, ep.neighbor_index, _ask_script(ep), NEIGHBOR_RETRY_S)]
    return _call_neighbor(ep, ep.neighbor_index + 1, neighbor_count, [])


def _followup_answer(ep: Episode, answer: Answer) -> tuple[Episode, list[Action]]:
    if answer == Answer.OK:
        return _close(ep, Result.OK_BY_NEIGHBOR, [Notify("resolved_ok")])
    if answer == Answer.NEEDS_HELP:
        return _close(ep, Result.NEEDS_HELP, [Notify("needs_help_194", urgent=True)])
    if ep.followup_attempts < FOLLOWUP_MAX_ATTEMPTS:
        ep = ep.model_copy(update={"followup_attempts": ep.followup_attempts + 1})
        return ep, [PlaceCall(Target.NEIGHBOR, ep.neighbor_index, ScriptKind.NEIGHBOR_FOLLOWUP, FOLLOWUP_RETRY_S)]
    return _close(ep, Result.UNCONFIRMED, [Notify("neighbor_unconfirmed", urgent=True)])


def _consent(ep: Episode, answer: Answer, target: Target) -> tuple[Episode, list[Action]]:
    if answer == Answer.YES:
        return _close(ep, Result.CONSENTED, [SetStatus(target, PersonStatus.ACTIVE), Notify("consent_yes")])
    if answer == Answer.NO:
        return _close(ep, Result.DECLINED, [SetStatus(target, PersonStatus.DECLINED), Notify("consent_no")])
    if target == Target.PERSON and ep.person_attempts < PERSON_MAX_ATTEMPTS:
        return _retry_person(ep, PERSON_RETRY_S)
    if target == Target.NEIGHBOR and ep.neighbor_attempts < PERSON_MAX_ATTEMPTS:
        ep = ep.model_copy(update={"neighbor_attempts": ep.neighbor_attempts + 1})
        return ep, [PlaceCall(Target.NEIGHBOR, 0, ScriptKind.INTRO_NEIGHBOR, PERSON_RETRY_S)]
    return _close(ep, Result.NO_CONSENT, [Notify("consent_missing")])


def _refused(ep: Episode, neighbor_count: int) -> tuple[Episode, list[Action]]:
    """The phone network refused the number, so the phone never rang. Trying again would only
    repeat the refusal, and "no answer" would be untrue. A check goes on as if nobody answered
    after the last try, so someone is still sent; the family hears what really happened."""
    if ep.kind in (EpisodeKind.INTRO, EpisodeKind.INTRO_NEIGHBOR):
        return _close(ep, Result.BLOCKED, [Notify("intro_refused", urgent=False)])
    if ep.state == EpisodeState.CALLING_PERSON:
        if ep.kind == EpisodeKind.HEADSUP:
            return _close(ep, Result.NOT_DELIVERED, [Notify("call_refused", urgent=True)])
        return _call_neighbor(ep, 0, neighbor_count, [Notify("call_refused", urgent=True)])
    if ep.state == EpisodeState.CALLING_NEIGHBOR:
        return _call_neighbor(ep, ep.neighbor_index + 1, neighbor_count, [Notify("neighbor_refused", urgent=True)])
    return _close(ep, Result.UNCONFIRMED, [Notify("neighbor_unconfirmed", urgent=True)])


def _close(ep: Episode, result: Result, before: list[Action]) -> tuple[Episode, list[Action]]:
    ep = ep.model_copy(update={"state": EpisodeState.CLOSED, "result": result})
    return ep, [*before, Close(result)]
