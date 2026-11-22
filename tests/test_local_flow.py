"""The design's success criteria (section 12), end to end, with nothing real behind them."""

from factories import make_person
from helpers import real_calls, request_check

from kuckuc.models import Answer, EpisodeState, Result, ScriptKind, Target


def keys(sys):
    return [n.key for n in sys.deps.notifier.sent]


def test_person_answers_fine_and_the_family_is_told(sys):
    ep_id = request_check(sys, kind="check", hazard="cold")
    sys.deps.interpreter.answers = [Answer.OK]
    sys.answer_next_call()
    assert sys.deps.store.get_episode(ep_id).result == Result.OK
    assert keys(sys) == ["call_ok"]
    assert [e.type for e in sys.deps.store.list_timeline(ep_id)] == [
        "episode_started",
        "call_queued",
        "call_placed",
        "call_answered",
        "answer_interpreted",
        "family_notified",
        "episode_closed",
    ]


def test_silent_person_leads_to_neighbour_then_followup_after_20_minutes(sys):
    request_check(sys, kind="check", hazard="cold")
    sys.deps.telephony.script_status = ["no-answer"] * 3  # FakeTelephony drives /status for each placed call
    sys.run_tasks(advance_s=0)
    sys.run_tasks(advance_s=600)
    sys.run_tasks(advance_s=600)
    sys.deps.interpreter.answers = [Answer.YES, Answer.OK]
    sys.answer_next_call()  # neighbour says yes
    assert sys.pending_task_delays() == [1200]
    sys.run_tasks(advance_s=1200)
    sys.answer_next_call()
    assert sys.deps.store.list_episodes("p1")[0].result == Result.OK_BY_NEIGHBOR
    assert [(c.target, c.script) for c in real_calls(sys)] == [
        (Target.PERSON, ScriptKind.CHECK),
        (Target.PERSON, ScriptKind.CHECK),
        (Target.PERSON, ScriptKind.CHECK),
        (Target.NEIGHBOR, ScriptKind.NEIGHBOR_ASK_SILENT),
        (Target.NEIGHBOR, ScriptKind.NEIGHBOR_FOLLOWUP),
    ]
    assert [p.to for p in sys.deps.telephony.placed] == ["+15005550006"] * 3 + ["+15005550007"] * 2
    assert keys(sys) == ["neighbor_called", "neighbor_going", "resolved_ok"]


def test_nothing_is_dialled_before_its_time(sys):
    request_check(sys, kind="check", hazard="cold")
    sys.deps.telephony.script_status = ["no-answer"]
    sys.run_tasks(advance_s=0)
    sys.run_tasks(advance_s=599)
    assert len(sys.deps.telephony.placed) == 1
    sys.run_tasks(advance_s=1)
    assert len(sys.deps.telephony.placed) == 2


def test_person_who_picks_up_and_says_nothing_gets_the_neighbour(sys):
    ep_id = request_check(sys, kind="check", hazard="cold")
    sys.answer_next_call()  # the fake interpreter has nothing prepared: unclear, re-ask, unclear
    ep = sys.deps.store.get_episode(ep_id)
    assert ep.state == EpisodeState.CALLING_NEIGHBOR and ep.result is None
    assert real_calls(sys)[-1].script == ScriptKind.NEIGHBOR_ASK_SILENT
    assert len(sys.deps.interpreter.calls) == 2  # asked once, re-asked once


def test_unwell_person_then_neighbour_reports_trouble_is_an_urgent_194_message(sys):
    ep_id = request_check(sys, kind="check", hazard="heat")
    sys.deps.interpreter.answers = [Answer.NEEDS_HELP, Answer.YES, Answer.NEEDS_HELP]
    sys.answer_next_call()
    sys.answer_next_call()
    sys.run_tasks(advance_s=1200)
    sys.answer_next_call()
    assert sys.deps.store.get_episode(ep_id).result == Result.NEEDS_HELP
    last = sys.deps.notifier.sent[-1]
    assert (last.key, last.urgent) == ("needs_help_194", True)
    # The test phone is a US number, so the emergency number named is 911.
    assert last.params == {"name": "baba Milena", "neighbor": "Dragan", "hazard": "heat", "emergency": "911"}


def test_kill_switch_between_queueing_and_dialling_stops_the_call_and_alerts_the_family(sys):
    ep_id = request_check(sys, kind="check", hazard="cold")
    sys.deps.store.runtime.kill_switch = True
    sys.run_tasks(advance_s=0)
    assert sys.deps.telephony.placed == []
    assert sys.deps.store.get_episode(ep_id).result == Result.BLOCKED
    assert keys(sys) == ["call_blocked"]


def test_simulated_person_runs_the_whole_chain_without_any_telephony(sys):
    sys.deps.store.put_person(make_person(id="sim-1", sim=True, owner_uid=""))
    sys.deps.bus.publish(
        "check.requested",
        {"person_id": "sim-1", "kind": "sim", "episode_id": "s1", "sim_answers": ["needs_help", "yes", "ok"]},
    )
    sys.run_tasks(advance_s=0)
    sys.run_tasks(advance_s=1200)
    assert sys.deps.store.get_episode("s1").result == Result.OK_BY_NEIGHBOR
    assert sys.deps.telephony.placed == []
    assert [c.provider for c in sys.deps.store.all_calls()] == ["simulated"] * 3


# --- the public demo: one phone plays both roles ----------------------------------------------


def start_demo_episode(sys, hazard="heat"):
    sys.deps.store.put_person(
        make_person(id="demo-1", name="there", lang="en", phone="+15005550009", demo=True, owner_uid="")
    )
    sys.deps.bus.publish(
        "check.requested", {"person_id": "demo-1", "kind": "demo", "episode_id": "demo-ep-1", "hazard": hazard}
    )
    return sys.deps.store.get_episode("demo-ep-1")


def demo_calls(sys):
    return [c for c in sys.deps.store.all_calls() if c.episode_id == "demo-ep-1"]


def test_demo_silent_visitor_gets_called_back_as_neighbour(sys):
    ep = start_demo_episode(sys)
    sys.deps.telephony.script_status = ["no-answer"] * 3
    for _ in range(3):
        sys.run_tasks(advance_s=600)
    neighbour_calls = [c for c in sys.deps.store.all_calls() if c.target == Target.NEIGHBOR]
    assert len(neighbour_calls) == 1 and neighbour_calls[0].to == sys.deps.store.get_person(ep.person_id).phone


def test_a_demo_visitor_who_does_not_pick_up_is_tried_again_after_30_seconds_not_10_minutes(sys):
    start_demo_episode(sys)
    sys.deps.telephony.script_status = ["no-answer"] * 3
    sys.run_tasks(advance_s=0)
    assert sys.pending_task_delays() == [30]
    sys.run_tasks(advance_s=29)
    assert len(sys.deps.telephony.placed) == 1
    sys.run_tasks(advance_s=1)
    assert len(sys.deps.telephony.placed) == 2
    sys.run_tasks(advance_s=30)  # third try, also unanswered: straight on to the neighbour role
    assert [c.target for c in demo_calls(sys)] == [Target.PERSON] * 3 + [Target.NEIGHBOR]
    assert len(sys.deps.telephony.placed) == 4


def test_demo_follow_up_comes_after_45_seconds_not_20_minutes(sys):
    start_demo_episode(sys)
    sys.deps.interpreter.answers = [Answer.NEEDS_HELP, Answer.YES, Answer.OK]
    sys.answer_next_call()  # the visitor says they feel unwell
    sys.answer_next_call()  # called back as the neighbour: "yes, I can go"
    assert sys.pending_task_delays() == [45]
    sys.run_tasks(advance_s=44)
    assert len(sys.deps.telephony.placed) == 2
    sys.run_tasks(advance_s=1)
    sys.answer_next_call()  # "she is fine"
    assert sys.deps.store.get_episode("demo-ep-1").result == Result.OK_BY_NEIGHBOR


def test_the_timeline_tells_the_page_how_long_the_real_wait_is(sys):
    start_demo_episode(sys)
    sys.deps.telephony.script_status = ["no-answer"]
    sys.run_tasks(advance_s=0)
    queued = [e.public for e in sys.deps.store.list_timeline("demo-ep-1") if e.type == "call_queued"]
    assert queued == [{"role": "person", "delay_s": "0"}, {"role": "person", "delay_s": "30"}]


def test_a_real_persons_waits_are_not_shortened(sys):
    request_check(sys, kind="check", hazard="cold")
    sys.deps.telephony.script_status = ["no-answer"]
    sys.run_tasks(advance_s=0)
    assert sys.pending_task_delays() == [600]


def test_a_simulated_person_never_waits(sys):
    # The load test must not sit through ten-minute retries: simulated waits are zero.
    sys.deps.store.put_person(make_person(id="sim-2", sim=True, owner_uid=""))
    sys.deps.bus.publish(
        "check.requested",
        {
            "person_id": "sim-2",
            "kind": "sim",
            "episode_id": "s2",
            "sim_answers": ["no_answer", "no_answer", "no_answer", "yes", "ok"],
        },
    )
    sys.run_tasks(advance_s=0)
    assert sys.deps.store.get_episode("s2").result == Result.OK_BY_NEIGHBOR
    assert sys.pending_task_delays() == []
    assert len([c for c in sys.deps.store.all_calls() if c.episode_id == "s2"]) == 5
