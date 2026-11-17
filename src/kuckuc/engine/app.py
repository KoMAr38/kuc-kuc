"""The engine service: it receives what happened, asks the state machine what to do, and does it.

Decisions live in ``machine.step``. This module only connects it to storage, queues and
notifications, and makes repeated deliveries harmless.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import replace
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI, Response
from pydantic import ValidationError

from kuckuc.deps import Deps
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
from kuckuc.models import (
    Answer,
    CallRecord,
    Episode,
    EpisodeKind,
    EpisodeState,
    Neighbor,
    Person,
    PersonStatus,
    Result,
    ScriptKind,
    Target,
)
from kuckuc.scripts import ScriptParams, params_to_dict, question_for

log = logging.getLogger(__name__)

SEEN_EVENTS_KEPT = 20
# Demo visitors and simulated people have no neighbour on file: they play the neighbour themselves.
_SELF_NEIGHBORS = (EpisodeKind.DEMO, EpisodeKind.SIM)
_AS_NEIGHBOR = {"sr": "vaš komšija", "en": "your neighbour"}


def create_engine_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="kuc-kuc engine", docs_url=None, redoc_url=None, openapi_url=None)

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
    if not deps.store.create_episode(ep):
        return
    deps.store.add_timeline(ep.id, "episode_started", {"kind": ep.kind.value, "hazard": ep.hazard or ""})
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
    if alert is None or person is None or person.status != PersonStatus.ACTIVE or person.demo or person.sim:
        return
    now = deps.clock.now()
    day_before = alert.day - timedelta(days=1)
    already_running = deps.store.alert_exists(f"{alert.person_id}:{alert.hazard}:{day_before}")
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
    live = None if current.kind in _SELF_NEIGHBORS else [n.id for n in _active_neighbors(deps, current.person_id)]
    actions: list[Action] = []

    def apply(ep: Episode) -> Episode | None:
        actions.clear()  # the store may run this more than once
        if event_id and event_id in ep.seen_event_ids:
            return None
        if live is not None and ep.neighbor_ids is None:
            ep = ep.model_copy(update={"neighbor_ids": live})
        after, todo = step(ep, ev, _neighbor_count(ep))
        actions.extend(todo)
        seen = [*ep.seen_event_ids, event_id][-SEEN_EVENTS_KEPT:] if event_id else ep.seen_event_ids
        return after.model_copy(update={"seen_event_ids": seen, "updated_at": deps.clock.now()})

    updated = deps.store.update_episode(episode_id, apply)
    if updated is not None:
        _execute(deps, updated, actions)


def _active_neighbors(deps: Deps, person_id: str) -> list[Neighbor]:
    return [n for n in deps.store.list_neighbors(person_id) if n.status == PersonStatus.ACTIVE]


def _neighbor_count(ep: Episode) -> int:
    return 1 if ep.kind in _SELF_NEIGHBORS else len(ep.neighbor_ids or [])


def _neighbor_ids(deps: Deps, ep: Episode) -> list[str]:
    """The neighbours this episode may call, in order: fixed when it started."""
    if ep.neighbor_ids is not None:
        return ep.neighbor_ids
    return [n.id for n in _active_neighbors(deps, ep.person_id)]


def _current_neighbor(deps: Deps, ep: Episode) -> Neighbor | None:
    if ep.kind == EpisodeKind.INTRO_NEIGHBOR:
        return deps.store.get_neighbor(ep.neighbor_id) if ep.neighbor_id else None
    if ep.kind in _SELF_NEIGHBORS:
        return None
    ids = _neighbor_ids(deps, ep)
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
    for action in actions:
        if isinstance(action, PlaceCall):
            _place_call(deps, ep, person, action)
        elif isinstance(action, Notify):
            _notify(deps, ep, person, action)
        elif isinstance(action, SetStatus):
            _set_status(deps, ep, person, action)
        elif isinstance(action, Close):
            deps.store.add_timeline(ep.id, "episode_closed", {"result": action.result.value})


def _place_call(deps: Deps, ep: Episode, person: Person, action: PlaceCall) -> None:
    role = action.target.value
    words = ScriptParams(
        name=person.name, family=person.family_name, gender=person.gender, hazard=ep.hazard, value=ep.value
    )
    to, neighbor_id = person.phone, None
    if action.target == Target.PERSON and ep.kind not in _SELF_NEIGHBORS:
        words = replace(words, has_neighbor=bool(_neighbor_ids(deps, ep)))
    if action.target == Target.NEIGHBOR:
        if ep.kind in _SELF_NEIGHBORS:
            words = replace(words, name=_AS_NEIGHBOR[person.lang], neighbor=person.name)
        else:
            neighbor = _current_neighbor(deps, ep)
            if neighbor is None:
                _blocked(deps, ep, role, "no_callee")
                return
            to, neighbor_id = neighbor.phone, neighbor.id
            words = replace(words, neighbor=neighbor.name)

    reason = call_block_reason(deps, person, action.target, first_try=ep.person_attempts <= 1)
    if reason:
        _blocked(deps, ep, role, reason)
        return

    params = params_to_dict(words)
    if ep.kind == EpisodeKind.SIM:
        params["sim_answer"] = _sim_answer(ep)
    call = CallRecord(
        id=uuid.uuid4().hex,
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
    deps.store.put_call(call)
    deps.queue.enqueue_call(call.id, delay_s, sim=person.sim)
    deps.store.add_timeline(ep.id, "call_queued", {"role": role, "delay_s": str(delay_s)})


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


def _blocked(deps: Deps, ep: Episode, role: str, reason: str) -> None:
    deps.store.add_timeline(ep.id, "call_blocked", {"role": role, "reason": reason})
    deps.bus.publish("call.blocked", {"episode_id": ep.id, "reason": reason})
    log.warning("episode %s: call blocked (%s)", ep.id, reason)


def _notify(deps: Deps, ep: Episode, person: Person, action: Notify) -> None:
    neighbor = _current_neighbor(deps, ep)
    about = neighbor.name if ep.kind == EpisodeKind.INTRO_NEIGHBOR and neighbor else person.name
    params = {"name": about, "neighbor": neighbor.name if neighbor else "", "hazard": ep.hazard or ""}
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
