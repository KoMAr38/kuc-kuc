"""The watchdog: ``python -m kuckuc.jobs.watchdog``, started by Cloud Scheduler every ten minutes.

Every step of a check is a message or a timer, and either can be lost: a queue that refuses
a task, an event that is never published, a phone company that never reports how a call
ended. None of that may leave a check silently unfinished while the family believes someone
is on the way. So this job looks at every check that is still open; one that has stopped
moving is closed and the family is told at once. It never places a call itself.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from kuckuc.deps import Deps
from kuckuc.models import Episode, EpisodeKind, EpisodeState, Result

log = logging.getLogger(__name__)

# The longest quiet gap in a working chain is the 20-minute wait before asking the neighbour
# how it went, plus a call or two; nothing healthy is silent for 45 minutes.
STALE_AFTER = timedelta(minutes=45)
# A planned call this late has lost its timer (Cloud Tasks retries for some minutes first).
LATE_AFTER = timedelta(minutes=20)
_INTROS = (EpisodeKind.INTRO, EpisodeKind.INTRO_NEIGHBOR)


def _stuck(ep: Episode, now: datetime) -> bool:
    if ep.id.startswith(("demo-", "sim-")):
        return False  # the demo and the load test are watched by the people running them
    if ep.state == EpisodeState.SCHEDULED:
        return ep.due_at is not None and now - ep.due_at > LATE_AFTER
    return now - ep.updated_at > STALE_AFTER


def _result(ep: Episode) -> Result:
    if ep.kind in _INTROS:
        return Result.NO_CONSENT
    # "The neighbour did not report back" only when a neighbour had gone to look.
    return Result.UNCONFIRMED if ep.state == EpisodeState.WAITING_FOLLOWUP else Result.NOT_DELIVERED


def run_watchdog(deps: Deps) -> list[str]:
    """Closes every check that has stopped moving and tells the family. Returns the ids closed."""
    now = deps.clock.now()
    closed = []
    for listed in deps.store.list_open_episodes():
        if not _stuck(listed, now):
            continue
        try:
            # Read again just before telling anyone: one that moved since the listing is not stuck,
            # and the family must not be alarmed about a check that is moving.
            ep = deps.store.get_episode(listed.id)
            if ep is None or (ep.state, ep.updated_at) != (listed.state, listed.updated_at):
                continue
            result = _result(ep)

            def close(current: Episode, seen: Episode = ep, result: Result = result) -> Episode | None:
                if current.state != seen.state or current.updated_at != seen.updated_at:
                    return None  # it moved since we looked: not stuck after all
                update = {"state": EpisodeState.CLOSED, "result": result, "updated_at": now, "pending": None}
                return current.model_copy(update=update)

            # The family is told first: if that fails, the check stays open and the next run tries
            # again. (Closed first, a failure here lost the message for good.)
            _tell_family(deps, ep)
            if deps.store.update_episode(ep.id, close) is None:
                continue
            deps.store.add_timeline(ep.id, "episode_closed", {"result": result.value})
        except Exception:
            log.exception("episode %s could not be closed now; the next run tries again", listed.id)
            continue
        log.error("episode %s had stopped moving (%s); closed as %s", ep.id, ep.state.value, result.value)
        closed.append(ep.id)
    log.info("watchdog: %d stopped checks closed", len(closed))
    return closed


def _tell_family(deps: Deps, ep: Episode) -> None:
    person = deps.store.get_person(ep.person_id)
    if person is None:
        return
    name = person.name
    if ep.kind == EpisodeKind.INTRO_NEIGHBOR and ep.neighbor_id:
        neighbor = deps.store.get_neighbor(ep.neighbor_id)
        name = neighbor.name if neighbor else name
    if ep.kind in _INTROS:
        key, urgent = "intro_lost", False
    elif ep.kind == EpisodeKind.HEADSUP and ep.state in (EpisodeState.SCHEDULED, EpisodeState.CALLING_PERSON):
        key, urgent = "headsup_blocked", False  # the day before, nothing happened yet: tomorrow's check still comes
    else:
        key, urgent = ("lost_track_unwell" if ep.unwell else "lost_track"), True
    params = {"name": name, "neighbor": "", "hazard": ep.hazard or ""}
    delivered = deps.notifier.send(person.owner_uid, key, params, urgent)
    deps.store.add_timeline(
        ep.id,
        "family_notified" if delivered else "family_not_notified",
        {"key": key, "urgent": "true" if urgent else "false"},
    )


def main() -> int:
    from kuckuc.config import Settings
    from kuckuc.deps import build_cloud_deps

    logging.basicConfig(level=logging.INFO)
    # The engine's dependencies: the watchdog tells the family through the same Telegram bot.
    run_watchdog(build_cloud_deps(Settings(), "engine"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
