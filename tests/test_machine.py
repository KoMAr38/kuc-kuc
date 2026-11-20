from factories import make_episode

from kuckuc.engine.machine import (
    CallBlocked,
    CallDone,
    Close,
    Due,
    Notify,
    PlaceCall,
    SetStatus,
    step,
)
from kuckuc.models import Answer, EpisodeKind, EpisodeState, PersonStatus, Result, ScriptKind, Target


def run(kind, inputs, neighbors=2):
    ep = make_episode(kind=kind)
    acts = []
    for ev in inputs:
        ep, new = step(ep, ev, neighbors)
        acts += new
    return ep, acts


def pa(a):
    return CallDone(Target.PERSON, ScriptKind.CHECK, a)


def na(script, a):
    return CallDone(Target.NEIGHBOR, script, a)


def calls(acts, target=None):
    return [a for a in acts if isinstance(a, PlaceCall) and (target is None or a.target == target)]


SILENT = ScriptKind.NEIGHBOR_ASK_SILENT
UNWELL = ScriptKind.NEIGHBOR_ASK_UNWELL
FOLLOWUP = ScriptKind.NEIGHBOR_FOLLOWUP


def test_check_ok():
    ep, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.OK)])
    assert acts == [PlaceCall(Target.PERSON, None, ScriptKind.CHECK), Notify("call_ok"), Close(Result.OK)]
    assert ep.state == EpisodeState.CLOSED and ep.result == Result.OK


def test_check_three_no_answers_then_neighbour():
    _, acts = run(EpisodeKind.CHECK, [Due()] + [pa(Answer.NO_ANSWER)] * 3)
    assert calls(acts) == [
        PlaceCall(Target.PERSON, None, ScriptKind.CHECK),
        PlaceCall(Target.PERSON, None, ScriptKind.CHECK, 600),
        PlaceCall(Target.PERSON, None, ScriptKind.CHECK, 600),
        PlaceCall(Target.NEIGHBOR, 0, SILENT),
    ]


def test_needs_help_goes_to_neighbour_at_once_with_unwell_script():
    ep, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.NEEDS_HELP)])
    assert Notify("call_unwell", True) in acts
    assert PlaceCall(Target.NEIGHBOR, 0, UNWELL) in acts
    assert ep.unwell is True and ep.state == EpisodeState.CALLING_NEIGHBOR


def test_unclear_goes_to_neighbour_never_ok():
    ep, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.UNCLEAR)])
    assert PlaceCall(Target.NEIGHBOR, 0, SILENT) in acts and ep.result is None


def test_wrong_kind_of_answer_to_are_you_okay_is_treated_as_unclear():
    for odd in (Answer.YES, Answer.NO):
        ep, acts = run(EpisodeKind.CHECK, [Due(), pa(odd)])
        assert PlaceCall(Target.NEIGHBOR, 0, SILENT) in acts and ep.result is None


def test_family_hears_once_per_neighbour_called_not_per_retry():
    _, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.UNCLEAR), na(SILENT, Answer.NO_ANSWER)])
    assert acts.count(Notify("neighbor_called")) == 1


def test_neighbour_yes_then_followup_ok():
    ep, acts = run(
        EpisodeKind.CHECK,
        [Due(), pa(Answer.UNCLEAR), na(SILENT, Answer.YES), na(FOLLOWUP, Answer.OK)],
    )
    assert PlaceCall(Target.NEIGHBOR, 0, FOLLOWUP, 1200) in acts
    assert Notify("neighbor_going") in acts and Notify("resolved_ok") in acts
    assert ep.result == Result.OK_BY_NEIGHBOR


def test_waiting_for_the_followup_is_its_own_state():
    ep, _ = run(EpisodeKind.CHECK, [Due(), pa(Answer.UNCLEAR), na(SILENT, Answer.YES)])
    assert ep.state == EpisodeState.WAITING_FOLLOWUP


def test_followup_needs_help_is_urgent():
    ep, acts = run(
        EpisodeKind.CHECK,
        [Due(), pa(Answer.UNCLEAR), na(SILENT, Answer.YES), na(FOLLOWUP, Answer.NEEDS_HELP)],
    )
    assert Notify("needs_help_194", True) in acts and ep.result == Result.NEEDS_HELP


def test_neighbour_no_answer_retries_once_then_next():
    _, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.UNCLEAR)] + [na(SILENT, Answer.NO_ANSWER)] * 2)
    assert calls(acts, Target.NEIGHBOR) == [
        PlaceCall(Target.NEIGHBOR, 0, SILENT),
        PlaceCall(Target.NEIGHBOR, 0, SILENT, 300),
        PlaceCall(Target.NEIGHBOR, 1, SILENT),
    ]


def test_second_neighbour_also_hears_that_the_person_is_unwell():
    _, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.NEEDS_HELP), na(UNWELL, Answer.NO_ANSWER), na(UNWELL, Answer.NO)])
    assert calls(acts, Target.NEIGHBOR) == [
        PlaceCall(Target.NEIGHBOR, 0, UNWELL),
        PlaceCall(Target.NEIGHBOR, 0, UNWELL, 300),
        PlaceCall(Target.NEIGHBOR, 1, UNWELL),
    ]


def test_unclear_neighbour_answer_moves_to_the_next_neighbour():
    _, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.UNCLEAR), na(SILENT, Answer.UNCLEAR)])
    assert calls(acts, Target.NEIGHBOR)[-1] == PlaceCall(Target.NEIGHBOR, 1, SILENT)


def test_all_neighbours_decline_is_urgent_unreachable():
    ep, acts = run(
        EpisodeKind.CHECK,
        [Due(), pa(Answer.UNCLEAR), na(SILENT, Answer.NO), na(SILENT, Answer.NO)],
    )
    assert Notify("nobody_reachable", True) in acts and ep.result == Result.UNREACHABLE


def test_no_neighbours_goes_straight_to_family():
    ep, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.NEEDS_HELP)], neighbors=0)
    assert calls(acts, Target.NEIGHBOR) == []
    assert Notify("nobody_reachable_unwell", True) in acts  # she said she is not well: the family is told so
    assert ep.result == Result.UNREACHABLE


def test_headsup_ok_confirms_to_family():
    ep, acts = run(EpisodeKind.HEADSUP, [Due(), CallDone(Target.PERSON, ScriptKind.HEADSUP, Answer.OK)])
    assert acts == [PlaceCall(Target.PERSON, None, ScriptKind.HEADSUP), Notify("headsup_ok"), Close(Result.OK)]


def test_headsup_unanswered_never_calls_neighbour():
    miss = CallDone(Target.PERSON, ScriptKind.HEADSUP, Answer.NO_ANSWER)
    ep, acts = run(EpisodeKind.HEADSUP, [Due(), miss, miss])
    assert PlaceCall(Target.PERSON, None, ScriptKind.HEADSUP, 1800) in acts
    assert calls(acts, Target.NEIGHBOR) == []
    assert Notify("headsup_missed") in acts and ep.result == Result.NOT_DELIVERED


def test_headsup_unclear_is_retried_once_like_no_answer():
    unclear = CallDone(Target.PERSON, ScriptKind.HEADSUP, Answer.UNCLEAR)
    ep, acts = run(EpisodeKind.HEADSUP, [Due(), unclear, unclear])
    assert len(calls(acts, Target.PERSON)) == 2 and calls(acts, Target.NEIGHBOR) == []
    assert ep.result == Result.NOT_DELIVERED


def test_headsup_needs_help_calls_neighbour():
    _, acts = run(EpisodeKind.HEADSUP, [Due(), CallDone(Target.PERSON, ScriptKind.HEADSUP, Answer.NEEDS_HELP)])
    assert Notify("call_unwell", True) in acts
    assert PlaceCall(Target.NEIGHBOR, 0, UNWELL) in acts


def test_followup_missed_once_is_tried_again_after_five_minutes():
    ep, acts = run(
        EpisodeKind.CHECK,
        [Due(), pa(Answer.UNCLEAR), na(SILENT, Answer.YES), na(FOLLOWUP, Answer.NO_ANSWER)],
    )
    assert acts[-1] == PlaceCall(Target.NEIGHBOR, 0, FOLLOWUP, 300) and ep.result is None


def test_followup_unanswered_twice_is_unconfirmed():
    ep, acts = run(
        EpisodeKind.CHECK,
        [Due(), pa(Answer.UNCLEAR), na(SILENT, Answer.YES)] + [na(FOLLOWUP, Answer.NO_ANSWER)] * 2,
    )
    assert Notify("neighbor_unconfirmed", True) in acts and ep.result == Result.UNCONFIRMED


def test_intro_yes_activates_and_no_declines():
    ep, acts = run(EpisodeKind.INTRO, [Due(), CallDone(Target.PERSON, ScriptKind.INTRO_PERSON, Answer.YES)])
    assert acts[0] == PlaceCall(Target.PERSON, None, ScriptKind.INTRO_PERSON)
    assert SetStatus(Target.PERSON, PersonStatus.ACTIVE) in acts and ep.result == Result.CONSENTED
    assert Notify("consent_yes") in acts
    ep, acts = run(EpisodeKind.INTRO, [Due(), CallDone(Target.PERSON, ScriptKind.INTRO_PERSON, Answer.NO)])
    assert SetStatus(Target.PERSON, PersonStatus.DECLINED) in acts and ep.result == Result.DECLINED
    assert Notify("consent_no") in acts


def test_intro_miss_is_retried_after_ten_minutes():
    _, acts = run(EpisodeKind.INTRO, [Due(), CallDone(Target.PERSON, ScriptKind.INTRO_PERSON, Answer.NO_ANSWER)])
    assert acts[-1] == PlaceCall(Target.PERSON, None, ScriptKind.INTRO_PERSON, 600)


def test_intro_three_misses_is_no_consent_and_status_unchanged():
    ep, acts = run(
        EpisodeKind.INTRO,
        [Due()] + [CallDone(Target.PERSON, ScriptKind.INTRO_PERSON, Answer.UNCLEAR)] * 3,
    )
    assert ep.result == Result.NO_CONSENT and not any(isinstance(a, SetStatus) for a in acts)
    assert Notify("consent_missing") in acts and len(calls(acts)) == 3


def test_neighbour_intro_asks_the_neighbour_and_sets_their_status():
    done = CallDone(Target.NEIGHBOR, ScriptKind.INTRO_NEIGHBOR, Answer.YES)
    ep, acts = run(EpisodeKind.INTRO_NEIGHBOR, [Due(), done])
    assert acts[0] == PlaceCall(Target.NEIGHBOR, 0, ScriptKind.INTRO_NEIGHBOR)
    assert SetStatus(Target.NEIGHBOR, PersonStatus.ACTIVE) in acts and ep.result == Result.CONSENTED
    ep, acts = run(EpisodeKind.INTRO_NEIGHBOR, [Due(), CallDone(Target.NEIGHBOR, ScriptKind.INTRO_NEIGHBOR, Answer.NO)])
    assert SetStatus(Target.NEIGHBOR, PersonStatus.DECLINED) in acts and ep.result == Result.DECLINED


def test_neighbour_intro_three_misses_is_no_consent():
    miss = CallDone(Target.NEIGHBOR, ScriptKind.INTRO_NEIGHBOR, Answer.NO_ANSWER)
    ep, acts = run(EpisodeKind.INTRO_NEIGHBOR, [Due(), miss, miss, miss])
    assert calls(acts) == [
        PlaceCall(Target.NEIGHBOR, 0, ScriptKind.INTRO_NEIGHBOR),
        PlaceCall(Target.NEIGHBOR, 0, ScriptKind.INTRO_NEIGHBOR, 600),
        PlaceCall(Target.NEIGHBOR, 0, ScriptKind.INTRO_NEIGHBOR, 600),
    ]
    assert ep.result == Result.NO_CONSENT


def test_blocked_is_urgent():
    ep, acts = run(EpisodeKind.CHECK, [Due(), CallBlocked("kill_switch")])
    assert acts[-2:] == [Notify("call_blocked", True), Close(Result.BLOCKED)]
    assert ep.state == EpisodeState.CLOSED and ep.result == Result.BLOCKED


def test_closed_episode_ignores_everything():
    ep, _ = run(EpisodeKind.CHECK, [Due(), pa(Answer.OK)])
    assert step(ep, pa(Answer.NEEDS_HELP), 2) == (ep, [])
    assert step(ep, Due(), 2) == (ep, [])
    assert step(ep, CallBlocked("kill_switch"), 2) == (ep, [])


def test_manual_uses_manual_script_and_demo_follows_check_rules():
    _, acts = run(EpisodeKind.MANUAL, [Due()])
    assert acts == [PlaceCall(Target.PERSON, None, ScriptKind.CHECK_MANUAL)]
    _, acts = run(EpisodeKind.MANUAL, [Due(), CallDone(Target.PERSON, ScriptKind.CHECK_MANUAL, Answer.NO_ANSWER)])
    assert acts[-1] == PlaceCall(Target.PERSON, None, ScriptKind.CHECK_MANUAL, 600)
    _, acts = run(EpisodeKind.DEMO, [Due(), pa(Answer.NO_ANSWER)])
    assert PlaceCall(Target.PERSON, None, ScriptKind.CHECK, 600) in acts
    _, acts = run(EpisodeKind.SIM, [Due(), pa(Answer.NEEDS_HELP)])
    assert PlaceCall(Target.NEIGHBOR, 0, UNWELL) in acts


def test_a_second_timer_does_not_place_a_second_first_call():
    ep, acts = run(EpisodeKind.CHECK, [Due(), Due()])
    assert acts == [PlaceCall(Target.PERSON, None, ScriptKind.CHECK)]
    assert ep.person_attempts == 1


def test_a_late_person_answer_cannot_close_an_escalation_as_ok():
    ep, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.UNCLEAR), pa(Answer.OK)])
    assert ep.state == EpisodeState.CALLING_NEIGHBOR and ep.result is None
    assert Notify("call_ok") not in acts


def test_a_late_neighbour_yes_does_not_restart_the_followup_clock():
    ep, acts = run(
        EpisodeKind.CHECK,
        [Due(), pa(Answer.UNCLEAR), na(SILENT, Answer.YES), na(SILENT, Answer.YES)],
    )
    assert len([a for a in acts if a == PlaceCall(Target.NEIGHBOR, 0, FOLLOWUP, 1200)]) == 1
    assert ep.state == EpisodeState.WAITING_FOLLOWUP


def test_a_followup_answer_before_any_followup_was_asked_is_ignored():
    ep, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.UNCLEAR), na(FOLLOWUP, Answer.OK)])
    assert ep.result is None and Notify("resolved_ok") not in acts


def test_step_does_not_change_the_episode_it_was_given():
    ep = make_episode(kind=EpisodeKind.CHECK)
    after, _ = step(ep, Due(), 2)
    assert ep.state == EpisodeState.SCHEDULED and ep.person_attempts == 0
    assert after.state == EpisodeState.CALLING_PERSON and after.person_attempts == 1


def test_a_number_the_network_refuses_is_reported_as_such_not_as_no_answer():
    # Her number refused during a check: no pointless retries, the neighbour is asked at once,
    # and the family hears that the network refused the number, not that she did not answer.
    ep, acts = run(EpisodeKind.CHECK, [Due(), CallBlocked("refused")])
    assert acts[-3:] == [
        Notify("call_refused", True),
        PlaceCall(Target.NEIGHBOR, 0, ScriptKind.NEIGHBOR_ASK_SILENT),
        Notify("neighbor_called"),
    ]
    assert ep.state == EpisodeState.CALLING_NEIGHBOR
    # A neighbour's number refused: the next neighbour is asked.
    _, acts = run(EpisodeKind.CHECK, [Due(), pa(Answer.NEEDS_HELP), CallBlocked("refused")])
    assert acts[-3:] == [
        Notify("neighbor_refused", True),
        PlaceCall(Target.NEIGHBOR, 1, ScriptKind.NEIGHBOR_ASK_UNWELL),
        Notify("neighbor_called"),
    ]
    # No neighbour left: the family is told nobody could be reached.
    ep, acts = run(EpisodeKind.CHECK, [Due(), CallBlocked("refused")], neighbors=0)
    assert ep.result == Result.UNREACHABLE and Notify("call_refused", True) in acts
    for kind in (EpisodeKind.INTRO, EpisodeKind.INTRO_NEIGHBOR):  # no alarm: check the number
        _, acts = run(kind, [Due(), CallBlocked("refused")])
        assert acts[-2:] == [Notify("intro_refused", False), Close(Result.BLOCKED)], kind



def test_a_blocked_heads_up_is_a_quiet_note_because_nothing_has_happened_yet():
    from kuckuc.engine.machine import CallBlocked

    ep, acts = run(EpisodeKind.HEADSUP, [Due(), CallBlocked("kill_switch")])
    assert ep.result == Result.NOT_DELIVERED and Notify("headsup_blocked", False) in acts



def test_a_heads_up_where_she_is_unwell_and_the_neighbours_call_is_blocked_is_urgent():
    from kuckuc.engine.machine import CallBlocked

    unwell = CallDone(Target.PERSON, ScriptKind.HEADSUP, Answer.NEEDS_HELP)
    ep, acts = run(EpisodeKind.HEADSUP, [Due(), unwell, CallBlocked("kill_switch")])
    assert ep.result == Result.BLOCKED and Notify("call_blocked", True) in acts
    assert Notify("headsup_blocked", False) not in acts


def test_a_refused_number_on_a_heads_up_is_reported_at_once():
    from kuckuc.engine.machine import CallBlocked

    ep, acts = run(EpisodeKind.HEADSUP, [Due(), CallBlocked("refused")])
    assert ep.result == Result.NOT_DELIVERED and Notify("call_refused", True) in acts
