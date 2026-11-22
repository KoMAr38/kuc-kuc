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
    # No neighbour had gone to look yet: "not delivered", not "the neighbour did not report back".
    assert (ep.state, ep.result) == (EpisodeState.CLOSED, Result.NOT_DELIVERED)
    assert notices(sys) == [("lost_track_unwell", True)]  # she had said she is not well: the family is told so
    assert "family_notified" in [e.type for e in sys.deps.store.list_timeline(ep_id)]
    assert run_watchdog(sys.deps) == [] and notices(sys) == [("lost_track_unwell", True)]  # once is enough


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
    sys.deps.notifier.sent.clear()
    assert run_watchdog(sys.deps) == []
    assert sys.deps.store.get_episode(ep_id).state == EpisodeState.WAITING_FOLLOWUP
    assert notices(sys) == [("neighbor_going", False)]  # and no alarm about a check that is moving



def test_a_heads_up_whose_timer_was_lost_is_reported_quietly(sys):
    sys.deps.store.create_episode(
        make_episode(id="h1", kind=EpisodeKind.HEADSUP, state=EpisodeState.SCHEDULED, due_at=T0, updated_at=T0)
    )
    sys.clock.advance(46 * 60)
    assert run_watchdog(sys.deps) == ["h1"]
    assert notices(sys)[-1] == ("headsup_blocked", False)



def test_a_heads_up_that_had_already_sent_for_the_neighbour_is_reported_urgently(sys):
    sys.deps.store.create_episode(
        make_episode(id="h2", kind=EpisodeKind.HEADSUP, state=EpisodeState.WAITING_FOLLOWUP, due_at=T0, updated_at=T0)
    )
    sys.clock.advance(46 * 60)
    assert run_watchdog(sys.deps) == ["h2"]
    assert notices(sys)[-1] == ("lost_track", True)


def test_a_check_whose_family_could_not_be_told_stays_open_for_the_next_run(sys):
    for id in ("w1", "w2"):
        sys.deps.store.create_episode(
            make_episode(id=id, state=EpisodeState.CALLING_PERSON, due_at=T0, updated_at=T0)
        )
    sys.clock.advance(46 * 60)
    read, broken = sys.deps.store.get_person, []

    def fails_once(person_id):
        if not broken:
            broken.append(person_id)
            raise RuntimeError("database hiccup")
        return read(person_id)

    sys.deps.store.get_person = fails_once
    first = run_watchdog(sys.deps)  # one failure must not stop the others
    assert len(first) == 1
    assert run_watchdog(sys.deps) == [id for id in ("w1", "w2") if id not in first]
    assert [n for n in notices(sys) if n == ("lost_track", True)] == [("lost_track", True)] * 2



def test_the_watchdog_says_she_is_unwell_when_that_is_known(sys):
    from kuckuc.engine.notify import render_message

    for id, unwell in (("e-silent", False), ("e-unwell", True)):
        sys.deps.store.create_episode(make_episode(id=id, state=EpisodeState.CALLING_NEIGHBOR, unwell=unwell))
    sys.clock.advance(46 * 60)
    assert sorted(run_watchdog(sys.deps)) == ["e-silent", "e-unwell"]
    assert sorted(notices(sys)) == [("lost_track", True), ("lost_track_unwell", True)]
    p = {"name": "N", "neighbor": "", "hazard": ""}
    assert render_message("lost_track_unwell", "sr", p).startswith("HITNO") and "nije dobro" in render_message(
        "lost_track_unwell", "sr", p
    )
    assert render_message("lost_track_unwell", "en", p).startswith("URGENT")


def test_a_stalled_check_is_unconfirmed_only_when_a_neighbour_had_gone_to_look(sys):
    states = {
        "e-person": EpisodeState.CALLING_PERSON,
        "e-neighbour": EpisodeState.CALLING_NEIGHBOR,
        "e-went": EpisodeState.WAITING_FOLLOWUP,
    }
    for id, state in states.items():
        sys.deps.store.create_episode(make_episode(id=id, state=state))
    sys.clock.advance(46 * 60)
    run_watchdog(sys.deps)
    assert {id: sys.deps.store.get_episode(id).result for id in states} == {
        "e-person": Result.NOT_DELIVERED,
        "e-neighbour": Result.NOT_DELIVERED,
        "e-went": Result.UNCONFIRMED,
    }



def test_the_watchdog_clears_the_unfinished_step_and_writes_the_closing_row(sys):
    saved = [{"type": "PlaceCall", "target": "neighbor", "neighbor_index": 0, "script": "neighbor_ask_silent", "delay_s": 0}]
    sys.deps.store.create_episode(make_episode(id="e-stuck", state=EpisodeState.CALLING_NEIGHBOR, pending=saved))
    sys.clock.advance(46 * 60)
    assert run_watchdog(sys.deps) == ["e-stuck"]
    assert sys.deps.store.get_episode("e-stuck").pending is None  # a late delivery must not carry it out
    rows = [(e.type, e.public) for e in sys.deps.store.list_timeline("e-stuck")]
    assert ("episode_closed", {"result": "not_delivered"}) in rows


def test_a_heads_up_stalled_while_calling_her_is_reported_quietly(sys):
    sys.deps.store.create_episode(make_episode(id="h3", kind=EpisodeKind.HEADSUP, state=EpisodeState.CALLING_PERSON))
    sys.clock.advance(46 * 60)
    assert run_watchdog(sys.deps) == ["h3"]
    assert notices(sys)[-1] == ("headsup_blocked", False)


def test_a_closed_checks_money_is_settled_at_what_its_calls_cost_and_only_once(sys):
    from kuckuc.budget import line_price_usd
    from kuckuc.jobs.watchdog import settle_budgets

    ep_id = request_check(sys)
    held = sys.deps.store.get_budget("own:month:2026-11")["reserved"]
    assert held > 0 and settle_budgets(sys.deps) == []  # still running: its money stays held
    call = only_new_call(sys)
    complete(sys, call, Answer.OK)
    assert sys.deps.store.get_episode(ep_id).state == EpisodeState.CLOSED
    assert settle_budgets(sys.deps) == [ep_id]
    price = line_price_usd(call.to, sys.deps.settings.call_price_per_min_usd)
    assert sys.deps.store.get_budget("own:month:2026-11") == {"reserved": 0.0, "spent": round(price, 4)}
    assert settle_budgets(sys.deps) == []


def test_one_budget_that_cannot_be_settled_never_stops_the_others(sys):
    from kuckuc.jobs.watchdog import settle_budgets

    first = request_check(sys)
    complete(sys, only_new_call(sys), Answer.OK)
    settle = sys.deps.store.settle_budget

    def fails_for_first(episode_id, spent):
        if episode_id == first:
            raise RuntimeError("database hiccup")
        return settle(episode_id, spent)

    sys.deps.store.settle_budget = fails_for_first
    sys.clock.advance(3600)
    second = request_check(sys)
    complete(sys, only_new_call(sys), Answer.OK)
    assert settle_budgets(sys.deps) == [second]
