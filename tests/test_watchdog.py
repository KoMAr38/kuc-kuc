"""The watchdog: a check that has stopped moving is closed, and the family is told at once."""

from datetime import timedelta

from factories import T0, make_episode
from helpers import complete, only_new_call, request_check

from kuckuc.jobs.watchdog import run_watchdog
from kuckuc.models import Answer, EpisodeKind, EpisodeState, Result


def notices(sys):
    return [(n.key, n.urgent) for n in sys.deps.notifier.sent]


def test_a_check_whose_neighbour_call_was_lost_is_closed_and_the_family_is_told(sys):
    ep_id = request_check(sys)
    complete(sys, only_new_call(sys), Answer.NEEDS_HELP)  # she is unwell: the neighbour's call is queued
    sys.deps.queue.tasks.clear()  # ...and its task is lost
    sys.deps.notifier.sent.clear()
    sys.clock.advance(46 * 60)
    assert run_watchdog(sys.deps) == [ep_id]
    ep = sys.deps.store.get_episode(ep_id)
    assert (ep.state, ep.result) == (EpisodeState.CLOSED, Result.UNCONFIRMED)
    assert notices(sys) == [("lost_track", True)]
    assert "family_notified" in [e.type for e in sys.deps.store.list_timeline(ep_id)]
    assert run_watchdog(sys.deps) == [] and notices(sys) == [("lost_track", True)]  # once is enough


def test_a_check_waiting_for_its_follow_up_is_left_alone(sys):
    ep_id = request_check(sys)
    complete(sys, only_new_call(sys), Answer.NEEDS_HELP)
    complete(sys, only_new_call(sys), Answer.YES)  # the neighbour goes; the follow-up is 20 minutes away
    sys.clock.advance(25 * 60)
    assert run_watchdog(sys.deps) == []
    assert sys.deps.store.get_episode(ep_id).state != EpisodeState.CLOSED


def test_a_planned_check_whose_timer_never_came_is_reported(sys):
    sys.deps.store.create_episode(
        make_episode(id="p1:2026-11-20:check", state=EpisodeState.SCHEDULED, due_at=T0 + timedelta(hours=1))
    )
    sys.clock.advance(70 * 60)  # ten minutes late: Cloud Tasks may still be retrying
    assert run_watchdog(sys.deps) == []
    sys.clock.advance(15 * 60)  # twenty-five minutes late: the timer is lost
    assert run_watchdog(sys.deps) == ["p1:2026-11-20:check"]
    assert sys.deps.store.get_episode("p1:2026-11-20:check").result == Result.NOT_DELIVERED
    assert notices(sys)[-1] == ("lost_track", True)


def test_a_check_planned_for_later_is_left_alone(sys):
    sys.deps.store.create_episode(make_episode(id="later", state=EpisodeState.SCHEDULED, due_at=T0 + timedelta(days=1)))
    sys.clock.advance(3 * 3600)
    assert run_watchdog(sys.deps) == []


def test_an_introduction_that_stalled_asks_the_family_to_start_it_again_without_alarm(sys):
    sys.deps.store.create_episode(make_episode(id="intro1", kind=EpisodeKind.INTRO, state=EpisodeState.CALLING_PERSON))
    sys.clock.advance(46 * 60)
    assert run_watchdog(sys.deps) == ["intro1"]
    assert sys.deps.store.get_episode("intro1").result == Result.NO_CONSENT
    assert notices(sys)[-1] == ("intro_lost", False)


def test_demo_and_simulated_episodes_are_not_watched(sys):
    for id in ("demo-x", "sim-x"):
        sys.deps.store.create_episode(make_episode(id=id, state=EpisodeState.CALLING_PERSON))
    sys.clock.advance(3 * 3600)
    assert run_watchdog(sys.deps) == []


def test_every_check_remembers_when_it_was_due(sys):
    ep_id = request_check(sys)
    assert sys.deps.store.get_episode(ep_id).due_at == T0


def test_a_check_that_moves_while_the_watchdog_looks_is_left_alone(sys):
    ep_id = request_check(sys)
    complete(sys, only_new_call(sys), Answer.NEEDS_HELP)
    sys.deps.queue.tasks.clear()
    sys.clock.advance(46 * 60)
    listed = sys.deps.store.list_open_episodes

    def listed_then_moved():
        found = listed()
        complete(sys, only_new_call(sys), Answer.YES)  # the neighbour answers just after the listing
        return found

    sys.deps.store.list_open_episodes = listed_then_moved
    assert run_watchdog(sys.deps) == []
    assert sys.deps.store.get_episode(ep_id).state == EpisodeState.WAITING_FOLLOWUP
