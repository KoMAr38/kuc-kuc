"""The engine service: it receives what happened, asks the state machine what to do, and does it.

Decisions live in ``machine.step``. This module only connects it to storage, queues and
notifications, and makes repeated deliveries harmless.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
import uuid
from dataclasses import replace
from enum import Enum
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI, Response
from pydantic import ValidationError

from kuckuc.deps import Deps
from kuckuc.emergency import emergency_number
from kuckuc.ports import StepInProgress
from kuckuc.engine.guards import call_block_reason
from kuckuc.engine.machine import (
    FOLLOWUP_DELAY_S,
    Action,
    CallBlocked,
    CallDone,
    Close,
    Due,
    Input,
    Notify,
    PlaceCall,
    SetStatus,
    step,
)
from kuckuc.engine.schedule import daytime_start, plan_calls
from kuckuc.budget import budget_caps, off_season, worst_case_usd
from kuckuc.models import (
    Answer,
    BudgetHold,
    CallRecord,
    Episode,
    EpisodeKind,
    EpisodeState,
    Neighbor,
    Payer,
    Person,
    PersonStatus,
    Result,
    ScriptKind,
    Target,
)
from kuckuc.scripts import ScriptParams, params_to_dict, question_for

log = logging.getLogger(__name__)

SEEN_EVENTS_KEPT = 20
# A step saved but not yet marked done may still be under way in another request (a slow message to
# the family takes up to ~20 s). Only after this long is it carried out again by a later delivery.
PENDING_GRACE_S = 90
# Pub/Sub brings a failed event back for days. A demo visitor's (or simulated) step that has failed
# this long is carried out without its calls: a phone must not ring hours after someone tried the
# demo. A real check is closed by the watchdog after 45 minutes, and until then a late call to a
# neighbour about a real person is still worth making.
LATE_STEP_S = 30 * 60
# Demo visitors and simulated people have no neighbour on file: they play the neighbour themselves.
_SELF_NEIGHBORS = (EpisodeKind.DEMO, EpisodeKind.SIM)
# Only demo and simulated checks call the person as their own neighbour, and those are in English; the
# German words are here so that every call language has them.
_AS_NEIGHBOR = {"sr": "vaš komšija", "en": "your neighbour", "de": "Ihr Nachbar"}


def create_engine_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="kuc-kuc engine", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(StepInProgress)
    def come_back_later(request, exc: StepInProgress) -> Response:
        # Not an error: the episode's last step may still be under way. The event comes back later.
        log.warning("%s; asked to come back later", exc)
        return Response(status_code=503)

    @app.post("/events")
    def events(envelope: dict = Body(...)) -> Response:
        """Pub/Sub push. Anything unreadable is acknowledged and dropped, or it would come back forever."""
        try:
            message = envelope["message"]
            payload = json.loads(base64.b64decode(message["data"], validate=True))
            event_id = str(message.get("messageId") or "")
            event_type, data = payload["type"], payload["data"]
        except Exception:
            log.warning("unreadable event dropped")
            return Response(status_code=204)
        handle_event(deps, event_id, event_type, data)
        return Response(status_code=204)

    @app.post("/timers")
    def timers(episode_id: str = Body(embed=True)) -> Response:
        _due(deps, episode_id)
        return Response(status_code=204)

    return app


def handle_event(deps: Deps, event_id: str, type: str, data: dict) -> None:
    """A malformed event is dropped, or it would come back forever. Any failure while handling a
    well-formed one is raised, so the event is delivered again: a step is never lost to a hiccup."""
    try:
        if type == "call.completed":
            ev: Input = CallDone(Target(data["target"]), ScriptKind(data["script"]), Answer(data["answer"]))
        elif type == "call.blocked":
            ev = CallBlocked(str(data.get("reason", "")))
        elif type == "check.requested":
            str(data["person_id"]), EpisodeKind(data["kind"])
        elif type == "intro.requested":
            str(data.get("neighbor_id") or data["person_id"])
        elif type == "alert.created":
            str(data["alert_id"])
        else:
            log.info("event %s ignored", type)
            return
        episode_id = str(data["episode_id"]) if type in ("call.completed", "call.blocked") else ""
    except (KeyError, ValueError, TypeError):
        log.exception("malformed %s event dropped", type)
        return
    # A call ends once. Its report is recognised by the call's id, so a copy the publisher sent again
    # under a new message id counts once.
    seen_as = f"call:{data['call_id']}" if data.get("call_id") else event_id
    try:
        if type == "check.requested":
            _start_check(deps, event_id, data)
        elif type == "intro.requested":
            _start_intro(deps, event_id, data)
        elif type == "alert.created":
            _schedule_alert(deps, data["alert_id"])
        else:
            _advance(deps, episode_id, seen_as, ev)
    except ValidationError:
        log.exception("malformed %s event dropped", type)


def start_episode(deps: Deps, ep: Episode) -> None:
    """Begin an episode now. A second request for the same id does nothing."""
    ep = ep.model_copy(update={"due_at": ep.due_at or deps.clock.now()})
    if deps.store.create_episode(ep):
        deps.store.add_timeline(ep.id, "episode_started", {"kind": ep.kind.value, "hazard": ep.hazard or ""})
    # Also when it existed: a first delivery may have created it and failed before its first step.
    # A Due for an episode that has started changes nothing.
    _advance(deps, ep.id, None, Due())


def _episode_id(event_id: str, data: dict) -> str:
    """The requester may choose the id; otherwise it follows from the event, so a redelivery maps to the same episode."""
    if data.get("episode_id"):
        return str(data["episode_id"])
    return uuid.uuid5(uuid.NAMESPACE_URL, f"kuckuc:{event_id}").hex if event_id else uuid.uuid4().hex


def _start_check(deps: Deps, event_id: str, data: dict) -> None:
    person = deps.store.get_person(data["person_id"])
    if person is None:
        log.warning("check requested for an unknown person; ignored")
        return
    now = deps.clock.now()
    start_episode(
        deps,
        Episode(
            id=_episode_id(event_id, data),
            person_id=person.id,
            kind=EpisodeKind(data["kind"]),
            hazard=data.get("hazard"),
            value=data.get("value"),
            demo_session_id=data.get("demo_session_id"),
            sim_answers=list(data.get("sim_answers") or []),
            created_at=now,
            updated_at=now,
        ),
    )


def _start_intro(deps: Deps, event_id: str, data: dict) -> None:
    now = deps.clock.now()
    if data.get("neighbor_id"):
        neighbor = deps.store.get_neighbor(data["neighbor_id"])
        if neighbor is None:
            return
        person_id, kind, neighbor_id = neighbor.person_id, EpisodeKind.INTRO_NEIGHBOR, neighbor.id
    else:
        person_id, kind, neighbor_id = data["person_id"], EpisodeKind.INTRO, None
    person = deps.store.get_person(person_id)
    if person is None:
        return
    episode = Episode(
        id=_episode_id(event_id, data),
        person_id=person_id,
        kind=kind,
        neighbor_id=neighbor_id,
        created_at=now,
        updated_at=now,
    )
    # The first call from Kuc-kuc is never the one that wakes someone: at night it waits for the morning,
    # unless the family asked for it now (they have just talked to that person, who is waiting by the phone).
    when = daytime_start(person.tz, now, deps.settings.quiet_start_hour, deps.settings.intro_end_hour)
    if data.get("now") or when <= now:
        start_episode(deps, episode)
    elif deps.store.create_episode(episode.model_copy(update={"due_at": when})):
        deps.queue.enqueue_due(episode.id, when)
        deps.store.add_timeline(episode.id, "episode_scheduled", {"kind": kind.value, "hazard": ""})
    else:
        _requeue_if_scheduled(deps, episode.id)


def _requeue_if_scheduled(deps: Deps, episode_id: str) -> None:
    """An episode that exists may never have got its timer (the queue failed once): queue it again.
    The queue keeps one task per episode and time, so a timer that exists is not doubled."""
    existing = deps.store.get_episode(episode_id)
    if existing is not None and existing.state == EpisodeState.SCHEDULED and existing.due_at is not None:
        deps.queue.enqueue_due(existing.id, existing.due_at)


def _schedule_alert(deps: Deps, alert_id: str) -> None:
    """Turn an alert into scheduled episodes. Episode ids follow from person, day and kind, so a
    repeated event, or a second hazard on the same day, schedules nothing new."""
    alert = deps.store.get_alert(alert_id)
    person = deps.store.get_person(alert.person_id) if alert else None
    if alert is None or person is None or person.status != PersonStatus.ACTIVE:
        return
    now = deps.clock.now()
    day_before = alert.day - timedelta(days=1)
    already_running = deps.store.get_alert(f"{alert.person_id}:{alert.hazard}:{day_before}") is not None
    planned = plan_calls(
        alert, person.tz, now, already_running, deps.settings.quiet_start_hour, deps.settings.quiet_end_hour
    )
    # Two planned calls in the same minute would meet a busy line: the second moves an hour later.
    taken = {
        e.due_at for e in deps.store.list_episodes(alert.person_id, limit=20) if e.state == EpisodeState.SCHEDULED
    }
    scheduled = 0
    for kind, when in planned:
        if when in taken:
            when = when + timedelta(hours=1)
        episode = Episode(
            id=f"{alert.person_id}:{alert.day}:{kind.value}",
            person_id=alert.person_id,
            kind=kind,
            hazard=alert.hazard,
            value=alert.value,
            created_at=now,
            updated_at=now,
            due_at=when,
        )
        if deps.store.create_episode(episode):
            deps.queue.enqueue_due(episode.id, when)
            deps.store.add_timeline(episode.id, "episode_scheduled", {"kind": kind.value, "hazard": alert.hazard})
            taken.add(when)
            scheduled += 1
        else:
            _requeue_if_scheduled(deps, episode.id)
    if scheduled:
        # Said only when a call really is on its way, and therefore only once.
        deps.notifier.send(
            person.owner_uid, "alert_created", {"name": person.name, "neighbor": "", "hazard": alert.hazard}, False
        )


def _due(deps: Deps, episode_id: str) -> None:
    """A scheduled moment has come. Someone paused or withdrawn since then is not called."""
    episode = deps.store.get_episode(episode_id)
    if episode is None:
        log.warning("timer for unknown episode %s ignored", episode_id)
        return
    if episode.kind in (EpisodeKind.HEADSUP, EpisodeKind.CHECK) and episode.state == EpisodeState.SCHEDULED:
        person = deps.store.get_person(episode.person_id)
        if person is None or person.status != PersonStatus.ACTIVE:

            def close(ep: Episode) -> Episode | None:
                if ep.state != EpisodeState.SCHEDULED:
                    return None
                return ep.model_copy(
                    update={"state": EpisodeState.CLOSED, "result": Result.NOT_DELIVERED, "updated_at": deps.clock.now()}
                )

            if deps.store.update_episode(episode_id, close) is not None:
                deps.store.add_timeline(episode_id, "episode_closed", {"result": Result.NOT_DELIVERED.value})
            return
    _advance(deps, episode_id, None, Due())


def _advance(deps: Deps, episode_id: str, event_id: str | None, ev: Input) -> None:
    current = deps.store.get_episode(episode_id)
    if current is None:
        log.warning("event for unknown episode %s ignored", episode_id)
        return
    # Who may be called is fixed when the episode starts, so a neighbour who says yes in the middle
    # of an escalation never changes who is asked, or who is asked "how is she?".
    if current.pending:
        # The last step was saved but not carried out. It goes first, and nothing replaces it.
        _finish_pending(deps, current)
    live = None if current.kind in _SELF_NEIGHBORS else [n.id for n in _active_neighbors(deps, current.person_id)]
    actions: list[Action] = []

    def apply(ep: Episode) -> Episode | None:
        actions.clear()  # the store may run this more than once
        if event_id and event_id in ep.seen_event_ids:
            return None
        if ep.pending:
            raise StepInProgress(f"episode {ep.id}: another delivery has just saved a step")
        if live is not None and ep.neighbor_ids is None:
            ep = ep.model_copy(update={"neighbor_ids": live})
        after, todo = step(ep, ev, _neighbor_count(ep))
        if not todo and after == ep:
            return None  # nothing happens: leave the episode, and any unfinished step, as it is
        actions.extend(todo)
        seen = [*ep.seen_event_ids, event_id][-SEEN_EVENTS_KEPT:] if event_id else ep.seen_event_ids
        # The step's actions are saved with it, and cleared once carried out: a failure in between
        # leaves them for the next delivery, instead of losing them.
        return after.model_copy(
            update={
                "seen_event_ids": seen,
                "updated_at": deps.clock.now(),
                "steps": ep.steps + 1,
                "pending": [_dump(a) for a in todo] or None,
            }
        )

    updated = deps.store.update_episode(episode_id, apply)
    if updated is not None:
        _execute(deps, updated, list(actions))
        _step_done(deps, updated)


def _finish_pending(deps: Deps, ep: Episode) -> None:
    """Carries out a step that was saved but not finished, unless it may still be under way."""
    age = deps.clock.now() - ep.updated_at
    if age < timedelta(seconds=PENDING_GRACE_S):
        raise StepInProgress(f"episode {ep.id}: its last step may still be under way")
    actions = [_load(a) for a in ep.pending or []]
    if ep.kind in _SELF_NEIGHBORS and age > timedelta(seconds=LATE_STEP_S) and any(
        isinstance(a, PlaceCall) for a in actions
    ):
        log.warning("episode %s: a step saved %s ago is carried out without its calls, and closed", ep.id, age)
        rest = [a for a in actions if not (isinstance(a, PlaceCall) or (isinstance(a, Notify) and a.key == "neighbor_called"))]
        _execute(deps, ep, rest)
        _step_done(deps, ep, closed=Result.NOT_DELIVERED)
        return
    log.warning("episode %s: carrying out a step that was saved but not finished", ep.id)
    _execute(deps, ep, actions)
    _step_done(deps, ep)


def _step_done(deps: Deps, ep: Episode, closed: Result | None = None) -> None:
    def done(current: Episode) -> Episode | None:
        if current.steps != ep.steps or current.pending is None:
            return None  # a later step (or the watchdog) has moved on: leave it
        update: dict = {"pending": None}
        if closed is not None:
            update |= {"state": EpisodeState.CLOSED, "result": closed, "updated_at": deps.clock.now()}
        return current.model_copy(update=update)

    if deps.store.update_episode(ep.id, done) is not None and closed is not None:
        deps.store.add_timeline(ep.id, "episode_closed", {"result": closed.value})


def _dump(action: Action) -> dict:
    data = {k: (v.value if isinstance(v, Enum) else v) for k, v in dataclasses.asdict(action).items()}
    return {"type": type(action).__name__, **data}


def _load(data: dict) -> Action:
    kind = data["type"]
    if kind == "PlaceCall":
        return PlaceCall(Target(data["target"]), data["neighbor_index"], ScriptKind(data["script"]), int(data["delay_s"]))
    if kind == "Notify":
        return Notify(str(data["key"]), bool(data["urgent"]))
    if kind == "SetStatus":
        return SetStatus(Target(data["target"]), PersonStatus(data["status"]))
    return Close(Result(data["result"]))


def _active_neighbors(deps: Deps, person_id: str) -> list[Neighbor]:
    return [n for n in deps.store.list_neighbors(person_id) if n.status == PersonStatus.ACTIVE]


def _neighbor_count(ep: Episode) -> int:
    return 1 if ep.kind in _SELF_NEIGHBORS else len(ep.neighbor_ids or [])


def _current_neighbor(deps: Deps, ep: Episode) -> Neighbor | None:
    if ep.kind == EpisodeKind.INTRO_NEIGHBOR:
        return deps.store.get_neighbor(ep.neighbor_id) if ep.neighbor_id else None
    if ep.kind in _SELF_NEIGHBORS:
        return None
    ids = ep.neighbor_ids or []  # fixed when the episode started
    if ep.neighbor_index >= len(ids):
        return None
    neighbor = deps.store.get_neighbor(ids[ep.neighbor_index])
    # One who withdrew since the start is not called.
    return neighbor if neighbor is not None and neighbor.status == PersonStatus.ACTIVE else None


def _execute(deps: Deps, ep: Episode, actions: list[Action]) -> None:
    person = deps.store.get_person(ep.person_id)
    if person is None:
        log.error("episode %s has no person; actions dropped", ep.id)
        return
    # Calls first: a slow message to the family must never hold up a call. A message that a
    # neighbour is being called goes only when that call really was queued.
    calls = [a for a in actions if isinstance(a, PlaceCall)]
    try:
        all_queued = all([_place_call(deps, ep, person, action, n) for n, action in enumerate(calls)])
    except Exception:
        # The calls are tried again with the step when the event comes back, but the family must not
        # wait for a queue to recover to hear that she is not well.
        _tell_before_failing(deps, ep, person, [a for a in actions if isinstance(a, Notify) and a.urgent])
        raise
    for action in actions:
        if isinstance(action, PlaceCall):
            continue
        if isinstance(action, Notify):
            if action.key == "neighbor_called" and not all_queued:
                continue
            _notify(deps, ep, person, action)
        elif isinstance(action, SetStatus):
            _set_status(deps, ep, person, action)
        elif isinstance(action, Close):
            deps.store.add_timeline(ep.id, "episode_closed", {"result": action.result.value})


def _tell_before_failing(deps: Deps, ep: Episode, person: Person, notices: list[Notify]) -> None:
    """Sends the step's urgent messages ahead of its failed calls, and takes them out of the saved
    step so that carrying it out again does not send them twice."""
    if not notices:
        return
    try:
        for action in notices:
            _notify(deps, ep, person, action)
        told = [_dump(a) for a in notices]

        def without_told(current: Episode) -> Episode | None:
            if current.steps != ep.steps or not current.pending:
                return None
            return current.model_copy(update={"pending": [a for a in current.pending if a not in told] or None})

        deps.store.update_episode(ep.id, without_told)
    except Exception:
        log.exception("episode %s: the urgent message could not go ahead of the failed call", ep.id)


def _place_call(deps: Deps, ep: Episode, person: Person, action: PlaceCall, n: int = 0) -> bool:
    """Queues the call; False when it was blocked. The call's id follows from the episode's step, so
    carrying out the same step again queues the same call once (the queue keeps one task per call)."""
    call_id = uuid.uuid5(uuid.NAMESPACE_URL, f"kuckuc:call:{ep.id}:{ep.steps}:{n}").hex
    existing = deps.store.get_call(call_id)
    if existing is not None:
        return _queue_again(deps, ep, person, action, existing)
    role = action.target.value
    words = ScriptParams(
        name=person.name,
        family=person.family_name,
        gender=person.gender,
        hazard=ep.hazard,
        value=ep.value,
        emergency=emergency_number(person.phone) or "",
    )
    to, neighbor_id = person.phone, None
    if action.target == Target.PERSON and ep.kind not in _SELF_NEIGHBORS:
        words = replace(words, has_neighbor=bool(ep.neighbor_ids))
    if action.target == Target.NEIGHBOR:
        if ep.kind in _SELF_NEIGHBORS:
            words = replace(words, name=_AS_NEIGHBOR[person.lang], neighbor=person.name)
        else:
            neighbor = _current_neighbor(deps, ep)
            if neighbor is None:
                _blocked(deps, ep, role, "no_callee")
                return False
            to, neighbor_id = neighbor.phone, neighbor.id
            words = replace(words, neighbor=neighbor.name)

    reason = call_block_reason(deps, person, action.target, first_try=ep.person_attempts <= 1)
    if reason is None and _first_call(ep, action) and not (person.demo or person.sim):
        reason = _hold_budget(deps, ep, person)
    if reason:
        _blocked(deps, ep, role, reason)
        return False

    params = params_to_dict(words)
    if ep.kind == EpisodeKind.SIM:
        params["sim_answer"] = _sim_answer(ep)
    call = CallRecord(
        id=call_id,
        episode_id=ep.id,
        target=action.target,
        neighbor_id=neighbor_id,
        script=action.script,
        question=question_for(action.script),
        provider="simulated" if person.sim else "twilio",
        to=to,
        lang=person.lang,
        params=params,
        created_at=deps.clock.now(),
        local_day=deps.clock.now().astimezone(ZoneInfo(person.tz)).date().isoformat(),
    )
    delay_s = call_delay_s(deps, ep, action)
    if not deps.store.create_call(call):
        # Another run of this same step created it a moment ago: never overwrite what voice has done since.
        return _queue_again(deps, ep, person, action, deps.store.get_call(call_id))
    deps.queue.enqueue_call(call.id, delay_s, sim=person.sim)
    deps.store.add_timeline(ep.id, "call_queued", {"role": role, "delay_s": str(delay_s)})
    return True


def _queue_again(deps: Deps, ep: Episode, person: Person, action: PlaceCall, call: CallRecord | None) -> bool:
    """A call made by an earlier run of this step: queued again (the queue keeps one task per call)."""
    if call is None or call.status == "blocked":
        return False
    deps.queue.enqueue_call(call.id, call_delay_s(deps, ep, action), sim=person.sim)
    return True


def call_delay_s(deps: Deps, ep: Episode, action: PlaceCall) -> int:
    """The one place where the machine's waiting times become real ones.

    The rules are the same for everyone; only a demo visitor's waits are shortened, so that
    someone trying the site sees the whole chain in a couple of minutes instead of half an hour.
    """
    if ep.kind == EpisodeKind.SIM:
        return 0  # simulated people exist to measure the system, not to wait
    if ep.kind == EpisodeKind.DEMO and action.delay_s > 0:
        if action.script == ScriptKind.NEIGHBOR_FOLLOWUP and action.delay_s == FOLLOWUP_DELAY_S:
            return deps.settings.demo_followup_s
        return deps.settings.demo_retry_s
    return action.delay_s


def _sim_answer(ep: Episode) -> str:
    """Simulated calls take their prepared answers in the order the calls are placed."""
    index = ep.person_attempts + ep.neighbor_attempts + ep.followup_attempts - 1
    return ep.sim_answers[index] if 0 <= index < len(ep.sim_answers) else Answer.UNCLEAR.value


class BudgetBusy(RuntimeError):
    """The budget could not be read and written in time. The event is delivered again later; a check is
    never blocked because the database was busy."""


def _first_call(ep: Episode, action: PlaceCall) -> bool:
    """The call that starts a check. Only it holds money: every later call of the check is already
    covered, so an escalation that has started always finishes."""
    if ep.kind == EpisodeKind.INTRO_NEIGHBOR:
        return ep.neighbor_attempts <= 1
    return action.target == Target.PERSON and ep.person_attempts <= 1


def _hold_budget(deps: Deps, ep: Episode, person: Person) -> str | None:
    """Holds the check's worst case in its payer's budgets. None when held; otherwise why not."""
    if deps.store.get_hold(ep.id) is not None:
        return None  # this check holds its money already: the same first call, carried out again
    payer = deps.store.get_payer(person.payer_id)
    now = deps.clock.now()
    caps = budget_caps(payer, now) if payer is not None else []
    if not caps:
        refusal: str | None = "off_season" if payer is not None and off_season(payer, now) else "no_budget"
    else:
        if ep.kind == EpisodeKind.INTRO_NEIGHBOR:
            ids = [ep.neighbor_id] if ep.neighbor_id else []
        else:
            ids = ep.neighbor_ids if ep.neighbor_ids is not None else [
                n.id for n in deps.store.list_neighbors(person.id) if n.status == PersonStatus.ACTIVE
            ]
        phones = [n.phone for n in (deps.store.get_neighbor(i) for i in ids) if n is not None]
        usd = worst_case_usd(ep.kind, person.phone, phones, deps.settings.call_price_per_min_usd)
        hold = BudgetHold(episode_id=ep.id, payer_id=person.payer_id, keys=[], usd=round(usd, 4), created_at=now)
        refusal = deps.store.reserve_budget(hold, caps)
    if refusal == "busy":
        raise BudgetBusy(f"episode {ep.id}: the budget of {person.payer_id} is busy")
    if refusal in ("budget", "no_budget") and payer is not None:
        _tell_staff(deps, payer)  # outside its season the staff know: their contract says so
    return refusal


def _tell_staff(deps: Deps, payer: Payer) -> None:
    """The payer's staff hear once a day that its money has run out; the families hear about every check."""
    day = deps.clock.now().astimezone(ZoneInfo(payer.tz)).date()
    key = f"budget_paused:{payer.id}:{day.isoformat()}"
    if not payer.staff_uids or not deps.store.claim_once(key):
        return
    reached = False
    for uid in payer.staff_uids:
        try:
            reached = deps.notifier.send(uid, "budget_paused_staff", {"payer": payer.name}, True) or reached
        except Exception:
            log.exception("payer %s: the staff message about the budget did not go out", payer.id)
    if not reached:
        deps.store.release_claim(key)  # nobody heard it: the next refused check tries again


def _blocked(deps: Deps, ep: Episode, role: str, reason: str) -> None:
    deps.store.add_timeline(ep.id, "call_blocked", {"role": role, "reason": reason})
    deps.bus.publish("call.blocked", {"episode_id": ep.id, "reason": reason})
    log.warning("episode %s: call blocked (%s)", ep.id, reason)


def _notify(deps: Deps, ep: Episode, person: Person, action: Notify) -> None:
    neighbor = _current_neighbor(deps, ep)
    about = neighbor.name if ep.kind == EpisodeKind.INTRO_NEIGHBOR and neighbor else person.name
    params = {
        "name": about,
        "neighbor": neighbor.name if neighbor else "",
        "hazard": ep.hazard or "",
        "emergency": emergency_number(person.phone) or "",
    }
    delivered = deps.notifier.send(person.owner_uid, action.key, params, action.urgent)
    # The family's page must not say "notified" about a message that never arrived. The demo shows
    # what a family would be told, so there the row stays.
    shown = delivered or ep.kind in _SELF_NEIGHBORS
    deps.store.add_timeline(
        ep.id,
        "family_notified" if shown else "family_not_notified",
        {"key": action.key, "urgent": "true" if action.urgent else "false"},
    )


def _set_status(deps: Deps, ep: Episode, person: Person, action: SetStatus) -> None:
    if action.target == Target.PERSON:
        deps.store.put_person(person.model_copy(update={"status": action.status}))
        return
    neighbor = _current_neighbor(deps, ep)
    if neighbor is not None:
        deps.store.put_neighbor(neighbor.model_copy(update={"status": action.status}))
