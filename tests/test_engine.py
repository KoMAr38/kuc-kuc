import base64
import json

import pytest

from factories import make_episode, make_neighbor, make_person
from fastapi.testclient import TestClient
from helpers import build_sys, complete, only_call, only_new_call, real_calls, request_check, seed_person_calls

from kuckuc.engine.app import StepInProgress, handle_event
from kuckuc.engine.guards import call_block_reason
from kuckuc.engine.notify import render_message
from kuckuc.models import (
    Answer,
    EpisodeKind,
    EpisodeState,
    PersonStatus,
    Result,
    ScriptKind,
    Target,
)


def neighbour_calls(sys):
    return [c for c in real_calls(sys) if c.target == Target.NEIGHBOR]


def sent_keys(sys):
    return [n.key for n in sys.deps.notifier.sent]


def envelope(type, data, message_id="m-1"):
    payload = base64.b64encode(json.dumps({"type": type, "data": data}).encode()).decode()
    return {"message": {"data": payload, "messageId": message_id}, "subscription": "events-to-engine"}


# --- starting checks -------------------------------------------------------------------------


def test_manual_check_queues_one_person_call(sys):
    sys.deps.bus.publish("check.requested", {"person_id": "p1", "kind": "manual"})
    calls = sys.deps.store.all_calls()
    assert len(calls) == 1 and calls[0].target == Target.PERSON and calls[0].script == ScriptKind.CHECK_MANUAL
    assert calls[0].to == "+15005550006" and calls[0].lang == "sr" and calls[0].provider == "twilio"
    assert calls[0].question.value == "wellbeing" and calls[0].status == "queued"
    assert [(t.kind, t.id) for t in sys.deps.queue.tasks] == [("call", calls[0].id)]


def test_person_call_carries_what_the_script_needs(sys):
    request_check(sys, kind="check", hazard="cold", value=-12.4)
    assert only_call(sys).params == {
        "name": "baba Milena",
        "family": "vaš unuk Petar",
        "neighbor": "",
        "gender": "f",
        "hazard": "cold",
        "value": "-12.4",
        "has_neighbor": "True",
        "emergency": "911",  # the test phone is a US number
    }


def test_check_for_unknown_person_is_ignored(sys):
    sys.deps.bus.publish("check.requested", {"person_id": "ghost", "kind": "manual"})
    assert sys.deps.store.all_calls() == []


def test_the_requester_can_choose_the_episode_id(sys):
    sys.deps.bus.publish("check.requested", {"person_id": "p1", "kind": "manual", "episode_id": "ep-from-web"})
    assert sys.deps.store.get_episode("ep-from-web").kind == EpisodeKind.MANUAL


def test_same_request_delivered_twice_starts_one_check(sys):
    data = {"person_id": "p1", "kind": "manual"}
    handle_event(sys.deps, "msg-7", "check.requested", data)
    handle_event(sys.deps, "msg-7", "check.requested", data)
    assert len(sys.deps.store.list_episodes("p1")) == 1 and len(real_calls(sys)) == 1


def test_timeline_starts_with_the_episode_and_the_queued_call(sys):
    ep_id = request_check(sys, kind="check", hazard="cold")
    assert [(e.type, e.public) for e in sys.deps.store.list_timeline(ep_id)] == [
        ("episode_started", {"kind": "check", "hazard": "cold"}),
        ("call_queued", {"role": "person", "delay_s": "0"}),
    ]


# --- answers ---------------------------------------------------------------------------------


def test_ok_closes_the_episode_and_tells_the_family(sys):
    ep_id = request_check(sys)
    complete(sys, only_call(sys), Answer.OK)
    ep = sys.deps.store.get_episode(ep_id)
    assert (ep.state, ep.result) == (EpisodeState.CLOSED, Result.OK)
    note = sys.deps.notifier.sent[-1]
    assert (note.owner_uid, note.key, note.urgent) == ("u1", "call_ok", False)
    assert note.params["name"] == "baba Milena"


def test_duplicate_completion_event_calls_neighbour_once(sys):
    ep_id = request_check(sys)
    c = only_call(sys)
    for _ in range(2):
        handle_event(
            sys.deps,
            "evt-1",
            "call.completed",
            {
                "call_id": c.id,
                "episode_id": ep_id,
                "target": "person",
                "script": "check_manual",
                "answer": "needs_help",
            },
        )
    assert len(neighbour_calls(sys)) == 1
    assert sent_keys(sys).count("call_unwell") == 1


def test_neighbour_call_goes_to_the_first_neighbour_with_the_right_words(sys):
    request_check(sys, kind="check", hazard="cold")
    complete(sys, only_call(sys), Answer.NEEDS_HELP)
    (call,) = neighbour_calls(sys)
    assert (call.to, call.neighbor_id, call.script) == ("+15005550007", "n1", ScriptKind.NEIGHBOR_ASK_UNWELL)
    assert call.params["neighbor"] == "Dragan" and call.params["name"] == "baba Milena"
    assert call.params["hazard"] == "cold" and call.lang == "sr"
    told = {n.key: n for n in sys.deps.notifier.sent}
    assert told["call_unwell"].urgent is True
    assert told["neighbor_called"].params["neighbor"] == "Dragan"


def test_only_neighbours_who_agreed_are_called(sys):
    sys.deps.store.put_neighbor(make_neighbor(id="n1", order=0, status=PersonStatus.PENDING_CONSENT))
    request_check(sys)
    complete(sys, only_call(sys), Answer.UNCLEAR)
    (call,) = neighbour_calls(sys)
    assert call.neighbor_id == "n2" and call.to == "+15005550008"


def test_person_with_no_agreed_neighbour_goes_straight_to_urgent_notice(sys):
    for nid, order in (("n1", 0), ("n2", 1)):
        sys.deps.store.put_neighbor(make_neighbor(id=nid, order=order, status=PersonStatus.DECLINED))
    ep_id = request_check(sys)
    complete(sys, only_call(sys), Answer.UNCLEAR)
    assert neighbour_calls(sys) == []
    assert sys.deps.notifier.sent[-1].key == "nobody_reachable" and sys.deps.notifier.sent[-1].urgent is True
    assert sys.deps.store.get_episode(ep_id).result == Result.UNREACHABLE


def test_retry_is_queued_with_its_delay(sys):
    request_check(sys)
    complete(sys, only_call(sys), Answer.NO_ANSWER)
    assert sys.pending_task_delays() == [0, 600]


def test_completion_for_unknown_episode_is_ignored(sys):
    handle_event(
        sys.deps,
        "evt-9",
        "call.completed",
        {"call_id": "x", "episode_id": "ghost", "target": "person", "script": "check", "answer": "ok"},
    )
    assert sys.deps.notifier.sent == []


# --- limits ----------------------------------------------------------------------------------


def test_daily_cap_blocks_seventh_person_call_with_urgent_notice(sys):
    seed_person_calls(sys, "p1", 6)
    ep_id = request_check(sys)
    assert sys.deps.notifier.sent[-1].key == "call_blocked" and sys.deps.notifier.sent[-1].urgent is True
    assert real_calls(sys) == [] and sys.deps.queue.tasks == []
    assert sys.deps.store.get_episode(ep_id).result == Result.BLOCKED
    assert ("call_blocked", {"role": "person", "reason": "daily_cap"}) in [
        (e.type, e.public) for e in sys.deps.store.list_timeline(ep_id)
    ]


def test_sixth_person_call_is_still_allowed(sys):
    seed_person_calls(sys, "p1", 5)
    request_check(sys)
    assert only_call(sys).status == "queued"


def test_neighbour_calls_not_blocked_by_person_cap(sys):
    seed_person_calls(sys, "p1", 5)
    request_check(sys)
    complete(sys, only_new_call(sys), Answer.NEEDS_HELP)
    assert any(c.target == Target.NEIGHBOR and c.status == "queued" for c in sys.deps.store.all_calls())


def test_kill_switch_stops_new_calls_and_the_family_hears_at_once(sys):
    sys.deps.store.runtime.kill_switch = True
    ep_id = request_check(sys)
    assert real_calls(sys) == []
    assert sent_keys(sys) == ["call_blocked"] and sys.deps.notifier.sent[0].urgent is True
    assert sys.deps.store.get_episode(ep_id).result == Result.BLOCKED


def test_block_reasons(sys):
    person = make_person(id="p1")
    assert call_block_reason(sys.deps, person, Target.PERSON) is None
    seed_person_calls(sys, "p1", 6)
    assert call_block_reason(sys.deps, person, Target.PERSON) == "daily_cap"
    assert call_block_reason(sys.deps, person, Target.NEIGHBOR) is None
    assert call_block_reason(sys.deps, make_person(id="p1", demo=True), Target.PERSON) is None
    assert call_block_reason(sys.deps, make_person(id="p1", sim=True), Target.PERSON) is None
    sys.deps.store.runtime.kill_switch = True
    assert call_block_reason(sys.deps, person, Target.NEIGHBOR) == "kill_switch"
    assert call_block_reason(sys.deps, make_person(id="p1", demo=True), Target.PERSON) == "kill_switch"


# --- privacy ---------------------------------------------------------------------------------


def test_timeline_public_fields_contain_no_phone_or_name(sys):
    ep_id = request_check(sys)
    complete(sys, only_call(sys), Answer.NEEDS_HELP)
    blob = json.dumps([e.public for e in sys.deps.store.list_timeline(ep_id)])
    assert "+1500" not in blob and "baba" not in blob and "Dragan" not in blob
    assert "family_notified" in [e.type for e in sys.deps.store.list_timeline(ep_id)]


# --- consent calls ---------------------------------------------------------------------------


def test_intro_yes_makes_the_person_active(sys):
    sys.deps.store.put_person(make_person(id="p1", status=PersonStatus.PENDING_CONSENT))
    sys.deps.bus.publish("intro.requested", {"person_id": "p1"})
    call = only_call(sys)
    assert (call.script, call.target, call.to) == (ScriptKind.INTRO_PERSON, Target.PERSON, "+15005550006")
    complete(sys, call, Answer.YES)
    assert sys.deps.store.get_person("p1").status == PersonStatus.ACTIVE
    assert sent_keys(sys) == ["consent_yes"]


def test_intro_no_marks_the_person_declined(sys):
    sys.deps.store.put_person(make_person(id="p1", status=PersonStatus.PENDING_CONSENT))
    sys.deps.bus.publish("intro.requested", {"person_id": "p1"})
    complete(sys, only_call(sys), Answer.NO)
    assert sys.deps.store.get_person("p1").status == PersonStatus.DECLINED


def test_neighbour_intro_calls_that_neighbour_and_sets_their_status(sys):
    sys.deps.store.put_neighbor(
        make_neighbor(id="n2", order=1, name="Rada", phone="+15005550008", status=PersonStatus.PENDING_CONSENT)
    )
    sys.deps.bus.publish("intro.requested", {"neighbor_id": "n2"})
    call = only_call(sys)
    assert (call.script, call.target, call.to, call.neighbor_id) == (
        ScriptKind.INTRO_NEIGHBOR,
        Target.NEIGHBOR,
        "+15005550008",
        "n2",
    )
    assert call.params["neighbor"] == "Rada" and call.params["name"] == "baba Milena"
    complete(sys, call, Answer.YES)
    assert sys.deps.store.get_neighbor("n2").status == PersonStatus.ACTIVE
    assert sys.deps.store.get_neighbor("n1").status == PersonStatus.ACTIVE
    assert sys.deps.notifier.sent[-1].params["name"] == "Rada"


# --- demo ------------------------------------------------------------------------------------


def test_demo_visitor_is_called_back_as_their_own_neighbour(sys):
    sys.deps.store.put_person(make_person(id="demo-1", name="there", lang="en", phone="+15005550009", demo=True, owner_uid=""))
    sys.deps.bus.publish("check.requested", {"person_id": "demo-1", "kind": "demo", "hazard": "heat", "episode_id": "d1"})
    (first,) = [c for c in real_calls(sys) if c.episode_id == "d1"]
    complete(sys, first, Answer.UNCLEAR)
    (second,) = [c for c in real_calls(sys) if c.episode_id == "d1" and c.target == Target.NEIGHBOR]
    assert second.to == "+15005550009" and second.lang == "en"
    assert (second.params["neighbor"], second.params["name"]) == ("there", "your neighbour")


# --- HTTP edges ------------------------------------------------------------------------------


def test_events_endpoint_reads_the_pubsub_envelope(sys):
    client = TestClient(sys.engine)
    r = client.post("/events", json=envelope("check.requested", {"person_id": "p1", "kind": "manual"}))
    assert r.status_code == 204 and len(real_calls(sys)) == 1
    client.post("/events", json=envelope("check.requested", {"person_id": "p1", "kind": "manual"}))
    assert len(real_calls(sys)) == 1  # same messageId: delivered twice, handled once


def test_events_endpoint_acknowledges_what_it_cannot_read(sys):
    client = TestClient(sys.engine)
    assert client.post("/events", json={"message": {"data": "!!not base64!!", "messageId": "m"}}).status_code == 204
    assert client.post("/events", json={"nothing": "here"}).status_code == 204
    assert client.post("/events", json=envelope("something.else", {})).status_code == 204
    assert sys.deps.store.all_calls() == []


def test_timer_starts_a_scheduled_episode_once(sys):
    sys.deps.store.create_episode(make_episode(id="p1:2026-11-20:check", person_id="p1", hazard="cold"))
    client = TestClient(sys.engine)
    assert client.post("/timers", json={"episode_id": "p1:2026-11-20:check"}).status_code == 204
    client.post("/timers", json={"episode_id": "p1:2026-11-20:check"})
    assert len(real_calls(sys)) == 1 and only_call(sys).script == ScriptKind.CHECK
    assert client.post("/timers", json={"episode_id": "ghost"}).status_code == 204


# --- messages to the family ------------------------------------------------------------------


def test_exact_urgent_wording_in_serbian():
    p = {"name": "baba Milena", "neighbor": "Dragan"}
    assert render_message("needs_help_194", "sr", p) == (
        "HITNO: Dragan javlja da baba Milena treba pomoć. Pozovi hitnu pomoć 194."
    )
    assert render_message("nobody_reachable", "sr", p) == (
        "HITNO: nijedan komšija nije dostupan, a ne znamo kako je baba Milena. "
        "Proveri odmah: pozovi telefonom ili zamoli nekoga ko je blizu."
    )
    assert render_message("nobody_reachable_unwell", "sr", p) == (
        "HITNO: baba Milena kaže da nije dobro, a nijedan komšija nije dostupan. "
        "Pozovi odmah nekoga ko je blizu ili hitnu pomoć 194."
    )
    assert render_message("call_blocked", "sr", p) == (
        "HITNO: poziv nije obavljen, pa ne znamo kako je baba Milena. "
        "Proveri odmah: pozovi telefonom ili zamoli nekoga ko je blizu."
    )


def test_every_urgent_key_says_so_up_front():
    p = {"name": "N", "neighbor": "K", "hazard": "heat"}
    for key in ("call_unwell", "needs_help_194", "nobody_reachable", "neighbor_unconfirmed", "call_blocked"):
        assert render_message(key, "sr", p).startswith("HITNO: ")
        assert render_message(key, "en", p).startswith("URGENT: ")


def test_alert_message_names_the_hazard_in_words():
    assert "velika hladnoća" in render_message("alert_created", "sr", {"name": "N", "hazard": "cold"})
    assert "extreme heat" in render_message("alert_created", "en", {"name": "N", "hazard": "heat"})


def test_unknown_message_key_still_tells_the_family_something():
    assert "N" in render_message("some_future_key", "en", {"name": "N"})


def test_the_daily_limit_never_cuts_off_an_escalation_that_has_started(sys):
    seed_person_calls(sys, "p1", 5)
    ep_id = request_check(sys)  # the sixth call today: still allowed
    for _ in range(3):  # she does not answer; the two retries are the seventh and eighth calls
        complete(sys, only_new_call(sys), Answer.NO_ANSWER)
    ep = sys.deps.store.get_episode(ep_id)
    assert ep.result != Result.BLOCKED and "call_blocked" not in sent_keys(sys)
    assert any(c.target == Target.NEIGHBOR and c.status == "queued" for c in sys.deps.store.all_calls())


def test_the_call_knows_whether_a_neighbour_has_said_yes(sys):
    request_check(sys)
    assert only_call(sys).params["has_neighbor"] == "True"


def test_the_call_knows_when_no_neighbour_has_said_yes(sys):
    for neighbor in sys.deps.store.list_neighbors("p1"):
        sys.deps.store.put_neighbor(neighbor.model_copy(update={"status": PersonStatus.PENDING_CONSENT}))
    request_check(sys)
    assert only_call(sys).params["has_neighbor"] == "False"


class Undelivered:
    """A notifier whose messages never arrive, as when the family has not connected Telegram."""

    def __init__(self):
        self.sent = []

    def send(self, owner_uid, key, params, urgent):
        self.sent.append(key)
        return False


def test_a_notice_that_did_not_arrive_is_shown_as_not_delivered(sys):
    sys.deps.notifier = Undelivered()
    ep_id = request_check(sys)
    complete(sys, only_new_call(sys), Answer.OK)
    types = [e.type for e in sys.deps.store.list_timeline(ep_id)]
    assert "family_not_notified" in types and "family_notified" not in types


def test_the_demo_still_shows_what_the_family_would_be_told(sys):
    from factories import make_person as person

    sys.deps.notifier = Undelivered()
    sys.deps.store.put_person(person(id="demo-p", demo=True, owner_uid=""))
    sys.deps.bus.publish("check.requested", {"person_id": "demo-p", "kind": "demo", "episode_id": "demo-e"})
    (call,) = [c for c in sys.deps.store.all_calls() if c.episode_id == "demo-e"]
    complete(sys, call, Answer.OK)
    assert "family_notified" in [e.type for e in sys.deps.store.list_timeline("demo-e")]


# --- audit 17 Nov: steps that must not be lost, messages that must be true -------------------


def completion(call, answer):
    return {
        "call_id": call.id,
        "episode_id": call.episode_id,
        "target": call.target.value,
        "script": call.script.value,
        "answer": answer.value,
    }


def test_a_completion_published_twice_under_new_message_ids_counts_once(sys):
    # The publisher retries after a lost acknowledgement, and the copy carries a new message id.
    ep_id = request_check(sys)
    call = only_new_call(sys)
    sys.deps.store.complete_call(call.id, Answer.NO_ANSWER, None, None)
    handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NO_ANSWER))
    handle_event(sys.deps, "m-2", "call.completed", completion(call, Answer.NO_ANSWER))
    assert sys.deps.store.get_episode(ep_id).person_attempts == 2


def test_a_store_that_gives_up_on_a_busy_write_gets_the_event_again_instead_of_dropping_it(sys):
    request_check(sys)
    call = only_new_call(sys)

    def give_up(*args, **kwargs):
        raise ValueError("Failed to commit transaction in 5 attempts.")  # what Firestore raises

    sys.deps.store.update_episode = give_up
    with pytest.raises(ValueError):
        handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.OK))


def test_a_planned_call_whose_timer_was_not_queued_gets_it_when_the_alert_comes_again(sys):
    from factories import TOMORROW, make_alert

    original, failed = sys.deps.queue.enqueue_due, []

    def fail_once(episode_id, at):
        if not failed:
            failed.append(episode_id)
            raise RuntimeError("queue unavailable")
        return original(episode_id, at)

    sys.deps.queue.enqueue_due = fail_once
    alert = make_alert(person_id="p1", hazard="cold", day=TOMORROW)
    sys.deps.store.create_alert(alert)
    with pytest.raises(RuntimeError):
        handle_event(sys.deps, "m-1", "alert.created", {"alert_id": alert.id})
    handle_event(sys.deps, "m-1", "alert.created", {"alert_id": alert.id})  # delivered again
    timers = {t.id for t in sys.deps.queue.tasks if t.kind == "due"}
    assert {f"p1:{TOMORROW}:headsup", f"p1:{TOMORROW}:check"} <= timers


def test_with_no_neighbour_who_said_yes_the_family_is_not_promised_one(sys):
    for neighbor in sys.deps.store.list_neighbors("p1"):
        sys.deps.store.put_neighbor(neighbor.model_copy(update={"status": PersonStatus.PENDING_CONSENT}))
    request_check(sys)
    complete(sys, only_new_call(sys), Answer.NEEDS_HELP)
    assert sent_keys(sys) == ["call_unwell", "nobody_reachable_unwell"]
    for key in sent_keys(sys):
        text = render_message(key, "sr", {"name": "baba Milena", "neighbor": ""})
        assert "Zovemo" not in text and "se ne javlja" not in text


def test_a_blocked_introduction_is_not_reported_as_an_urgent_check(sys):
    sys.deps.store.runtime.kill_switch = True
    sys.deps.bus.publish("intro.requested", {"neighbor_id": "n1", "now": True})
    assert sent_keys(sys)[-1] == "intro_blocked" and sys.deps.notifier.sent[-1].urgent is False


def test_the_neighbour_who_went_is_the_one_asked_how_she_is_even_if_another_agrees_meanwhile(sys):
    dragan = sys.deps.store.get_neighbor("n1")
    sys.deps.store.put_neighbor(dragan.model_copy(update={"status": PersonStatus.PENDING_CONSENT}))
    request_check(sys)
    complete(sys, only_new_call(sys), Answer.NEEDS_HELP)
    ask = only_new_call(sys)
    assert ask.neighbor_id == "n2"  # Rada is the only one who has said yes
    sys.deps.store.put_neighbor(dragan)  # Dragan says yes while Rada is on her way
    complete(sys, ask, Answer.YES)
    follow_up = only_new_call(sys)
    assert (follow_up.neighbor_id, follow_up.to) == ("n2", "+15005550008")


def test_a_heads_up_never_rings_in_the_same_minute_as_another_planned_call(sys):
    from datetime import timedelta

    from factories import TODAY, TOMORROW, make_alert

    for hazard, day in (("wind", TOMORROW), ("heat", TODAY)):  # either order
        alert = make_alert(person_id="p1", hazard=hazard, day=day, value=36.0)
        sys.deps.store.create_alert(alert)
        sys.deps.bus.publish("alert.created", {"alert_id": alert.id})
    headsup = sys.deps.store.get_episode(f"p1:{TOMORROW}:headsup")
    check = sys.deps.store.get_episode(f"p1:{TODAY}:check")
    assert abs(headsup.due_at - check.due_at) == timedelta(hours=1)


def test_the_daily_limit_counts_calls_in_the_persons_own_day(sys):
    # Six calls by the afternoon in Los Angeles; at 16:30 there it is already tomorrow in UTC.
    person = sys.deps.store.get_person("p1")
    sys.deps.store.put_person(person.model_copy(update={"tz": "America/Los_Angeles"}))
    seed_person_calls(sys, "p1", 6)
    sys.clock.advance(int(15.5 * 3600))
    request_check(sys)
    assert "call_blocked" in sent_keys(sys)


def test_every_message_exists_in_both_languages():
    from kuckuc.engine.notify import _MESSAGES

    assert set(_MESSAGES["sr"]) == set(_MESSAGES["en"])
    for key in _MESSAGES["sr"]:
        for lang in ("sr", "en"):
            text = render_message(key, lang, {"name": "N", "neighbor": "K", "hazard": "cold"})
            assert text and "{" not in text and "(" + key + ")" not in text


def test_every_urgent_message_starts_with_the_word_urgent():
    p = {"name": "N", "neighbor": "K", "hazard": "heat"}
    keys = (
        "call_unwell", "needs_help_194", "nobody_reachable", "nobody_reachable_unwell", "neighbor_unconfirmed",
        "call_blocked", "lost_track", "call_refused", "neighbor_refused",
    )
    for key in keys:
        assert render_message(key, "sr", p).startswith("HITNO"), key
        assert render_message(key, "en", p).startswith("URGENT"), key


def test_a_store_that_runs_a_change_twice_still_queues_one_call(sys):
    # Firestore runs a transaction again when another write got there first; the actions of the
    # first run must not be carried out too.
    store = sys.deps.store
    original = store.update_episode

    def twice(episode_id, fn):
        fn(store.get_episode(episode_id))
        return original(episode_id, fn)

    store.update_episode = twice
    request_check(sys)
    assert len(real_calls(sys)) == 1



# --- audit 17 Nov, second round -------------------------------------------------------------


def test_a_check_whose_first_step_failed_starts_when_the_request_comes_again(sys):
    original, failed = sys.deps.store.update_episode, []

    def give_up_once(episode_id, fn):
        if not failed:
            failed.append(episode_id)
            raise ValueError("Failed to commit transaction in 5 attempts.")
        return original(episode_id, fn)

    sys.deps.store.update_episode = give_up_once
    data = {"person_id": "p1", "kind": "manual", "episode_id": "e-now"}
    with pytest.raises(ValueError):
        handle_event(sys.deps, "m-1", "check.requested", data)
    handle_event(sys.deps, "m-1", "check.requested", data)  # Pub/Sub delivers it again
    assert [c.target for c in real_calls(sys)] == [Target.PERSON]


def test_a_step_saved_but_not_carried_out_is_carried_out_when_the_event_comes_again(sys):
    # She says she is not well; the neighbour's call is saved in the episode, then queueing it fails once.
    request_check(sys)
    call = only_new_call(sys)
    sys.deps.store.complete_call(call.id, Answer.NEEDS_HELP, None, None)
    original, failed = sys.deps.queue.enqueue_call, []

    def fail_once(call_id, delay_s, sim=False):
        if not failed:
            failed.append(call_id)
            raise RuntimeError("Cloud Tasks deadline exceeded")
        return original(call_id, delay_s, sim)

    sys.deps.queue.enqueue_call = fail_once
    with pytest.raises(RuntimeError):
        handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    with pytest.raises(RuntimeError):
        # Too soon: the first delivery may still be carrying it out; the event is asked to come back later.
        handle_event(sys.deps, "m-2", "call.completed", completion(call, Answer.NEEDS_HELP))
    sys.clock.advance(120)
    handle_event(sys.deps, "m-3", "call.completed", completion(call, Answer.NEEDS_HELP))
    neighbour_calls = [c for c in real_calls(sys) if c.target == Target.NEIGHBOR]
    assert len(neighbour_calls) == 1
    assert ("call", neighbour_calls[0].id) in [(t.kind, t.id) for t in sys.deps.queue.tasks]
    assert "neighbor_called" in sent_keys(sys)


def test_the_neighbour_is_queued_before_the_family_is_told_and_a_blocked_call_is_never_announced(sys):
    request_check(sys)
    call = only_new_call(sys)
    seen = []
    send = sys.deps.notifier.send

    def notice(owner_uid, key, params, urgent):
        seen.append((key, len([c for c in real_calls(sys) if c.target == Target.NEIGHBOR])))
        return send(owner_uid, key, params, urgent)

    sys.deps.notifier.send = notice
    complete(sys, call, Answer.NEEDS_HELP)
    assert seen[0] == ("call_unwell", 1)  # the neighbour's call was queued before the first notice

    # The same, with calls stopped: the family is never told that a neighbour is being called.
    sys2 = build_sys()
    request_check(sys2)
    sys2.deps.store.runtime.kill_switch = True
    complete(sys2, only_new_call(sys2), Answer.NEEDS_HELP)
    assert "neighbor_called" not in sent_keys(sys2) and "call_blocked" in sent_keys(sys2)


def test_a_malformed_event_is_dropped_and_changes_nothing(sys):
    ep_id = request_check(sys)
    call = only_new_call(sys)
    handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.OK) | {"answer": "maybe"})
    assert sys.deps.store.get_episode(ep_id).state == EpisodeState.CALLING_PERSON


def test_a_night_introduction_whose_timer_failed_gets_it_when_the_request_comes_again(sys):
    # 23:30 in Belgrade: the introduction waits for the morning, and its timer fails to queue once.
    sys.clock.advance(int(13.5 * 3600))
    original, failed = sys.deps.queue.enqueue_due, []

    def fail_once(episode_id, at):
        if not failed:
            failed.append(episode_id)
            raise RuntimeError("queue unavailable")
        return original(episode_id, at)

    sys.deps.queue.enqueue_due = fail_once
    with pytest.raises(RuntimeError):
        handle_event(sys.deps, "m-1", "intro.requested", {"neighbor_id": "n1"})
    handle_event(sys.deps, "m-1", "intro.requested", {"neighbor_id": "n1"})
    assert [t.kind for t in sys.deps.queue.tasks] == ["due"]



# --- audit 17 Nov, third round --------------------------------------------------------------


def test_a_new_event_waits_for_an_unfinished_step_and_never_wipes_it(sys):
    # The family's urgent "she is not well" was still unsent when the neighbour's answer arrived.
    request_check(sys)
    call = only_new_call(sys)
    sys.deps.store.complete_call(call.id, Answer.NEEDS_HELP, None, None)
    send, failed = sys.deps.notifier.send, []

    def fails_once(*args):
        if not failed:
            failed.append(args)
            raise RuntimeError("database hiccup while telling the family")
        return send(*args)

    sys.deps.notifier.send = fails_once
    with pytest.raises(RuntimeError):
        handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    ask = only_new_call(sys)
    sys.deps.store.complete_call(ask.id, Answer.YES, None, None)
    with pytest.raises(StepInProgress):
        handle_event(sys.deps, "m-2", "call.completed", completion(ask, Answer.YES))
    sys.clock.advance(120)
    handle_event(sys.deps, "m-2", "call.completed", completion(ask, Answer.YES))
    assert "call_unwell" in sent_keys(sys) and "neighbor_going" in sent_keys(sys)


def test_a_step_carried_out_again_never_resets_a_call_voice_has_moved_on(sys):
    request_check(sys)
    call = only_new_call(sys)
    sys.deps.store.complete_call(call.id, Answer.NEEDS_HELP, None, None)
    original, failed = sys.deps.queue.enqueue_call, []

    def fail_once(call_id, delay_s, sim=False):
        if not failed:
            failed.append(call_id)
            raise RuntimeError("deadline exceeded")
        return original(call_id, delay_s, sim)

    sys.deps.queue.enqueue_call = fail_once
    with pytest.raises(RuntimeError):
        handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    (ask,) = [c for c in real_calls(sys) if c.target == Target.NEIGHBOR]
    sys.deps.store.patch_call(ask.id, status="placed")  # voice dialled it meanwhile
    sys.clock.advance(120)
    handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    assert sys.deps.store.get_call(ask.id).status == "placed"
    assert sys.deps.store.create_call(sys.deps.store.get_call(ask.id)) is False  # a call is created once


def test_come_back_later_is_a_quiet_503_not_a_server_error(sys):
    request_check(sys)
    call = only_new_call(sys)
    sys.deps.store.complete_call(call.id, Answer.NEEDS_HELP, None, None)
    sys.deps.queue.enqueue_call = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    with pytest.raises(RuntimeError):
        handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    reply = TestClient(sys.engine).post("/events", json=envelope("call.completed", completion(call, Answer.NEEDS_HELP), "m-9"))
    assert reply.status_code == 503


def test_every_action_survives_being_saved_and_read_back():
    from kuckuc.engine.app import _dump, _load
    from kuckuc.engine.machine import Close, Notify, PlaceCall, SetStatus

    actions = [
        PlaceCall(Target.NEIGHBOR, 1, ScriptKind.NEIGHBOR_FOLLOWUP, 1200),
        Notify("needs_help_194", urgent=True),
        Notify("call_ok"),
        SetStatus(Target.PERSON, PersonStatus.ACTIVE),
        Close(Result.NEEDS_HELP),
    ]
    assert [_load(_dump(a)) for a in actions] == actions



# --- audit 20 Nov, fourth round -------------------------------------------------------------


def test_she_is_unwell_reaches_the_family_even_while_the_neighbours_call_cannot_be_queued(sys):
    request_check(sys)
    call = only_new_call(sys)
    sys.deps.store.complete_call(call.id, Answer.NEEDS_HELP, None, None)
    original = sys.deps.queue.enqueue_call
    sys.deps.queue.enqueue_call = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Cloud Tasks down"))
    with pytest.raises(RuntimeError):
        handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    assert sent_keys(sys).count("call_unwell") == 1  # told at once, not when the queue comes back
    assert "neighbor_called" not in sent_keys(sys)
    sys.deps.queue.enqueue_call = original
    sys.clock.advance(120)
    handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))  # Pub/Sub again
    assert sent_keys(sys).count("call_unwell") == 1  # and only once
    assert sent_keys(sys).count("neighbor_called") == 1


def test_a_planned_check_that_meets_stopped_calls_tells_the_family_as_it_would_in_the_cloud(sys):
    # The blocked call's event arrives while the timer's own step is still under way. Pub/Sub brings it
    # back a little later; the local bus must too, or no test can see this path.
    from datetime import timedelta

    from factories import T0

    due = T0 + timedelta(hours=1)
    sys.deps.store.create_episode(make_episode(id="p1:planned", state=EpisodeState.SCHEDULED, due_at=due))
    sys.deps.queue.enqueue_due("p1:planned", due)
    sys.deps.store.runtime.kill_switch = True
    sys.run_tasks(3600)
    assert "call_blocked" in sent_keys(sys)
    assert sys.deps.store.get_episode("p1:planned").state == EpisodeState.CLOSED


def test_a_demo_step_that_kept_failing_for_hours_never_rings_the_visitor_late(sys):
    sys.deps.store.put_person(make_person(id="demo-1", name="there", lang="en", phone="+15005550009", demo=True, owner_uid=""))
    sys.deps.bus.publish("check.requested", {"person_id": "demo-1", "kind": "demo", "hazard": "heat", "episode_id": "demo-d1"})
    (first,) = [c for c in real_calls(sys) if c.episode_id == "demo-d1"]
    sys.deps.store.complete_call(first.id, Answer.NEEDS_HELP, None, None)
    original = sys.deps.queue.enqueue_call
    sys.deps.queue.enqueue_call = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Cloud Tasks down"))
    with pytest.raises(RuntimeError):
        handle_event(sys.deps, "m-1", "call.completed", completion(first, Answer.NEEDS_HELP))
    sys.deps.queue.enqueue_call = original
    sys.deps.queue.tasks.clear()
    sys.clock.advance(3 * 3600)  # Pub/Sub kept bringing the event back; hours later the queue works again
    handle_event(sys.deps, "m-1", "call.completed", completion(first, Answer.NEEDS_HELP))
    assert [t for t in sys.deps.queue.tasks if t.kind == "call"] == []
    ep = sys.deps.store.get_episode("demo-d1")
    assert (ep.state, ep.result) == (EpisodeState.CLOSED, Result.NOT_DELIVERED)
    assert "episode_closed" in [e.type for e in sys.deps.store.list_timeline("demo-d1")]


def test_a_real_check_step_that_failed_for_a_while_still_calls_the_neighbour(sys):
    request_check(sys)
    call = only_new_call(sys)
    sys.deps.store.complete_call(call.id, Answer.NEEDS_HELP, None, None)
    original = sys.deps.queue.enqueue_call
    sys.deps.queue.enqueue_call = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Cloud Tasks down"))
    with pytest.raises(RuntimeError):
        handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    sys.deps.queue.enqueue_call = original
    sys.deps.queue.tasks.clear()  # her own call ran long ago
    sys.clock.advance(35 * 60)
    handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    assert [t.kind for t in sys.deps.queue.tasks] == ["call"]  # a late call about a real person is still made



def test_an_event_that_races_a_step_saved_meanwhile_comes_back_later_and_leaves_that_step(sys):
    ep_id = request_check(sys)
    call = only_new_call(sys)
    sys.deps.store.complete_call(call.id, Answer.OK, None, None)
    saved = [{"type": "Notify", "key": "call_unwell", "urgent": True}]
    sys.deps.store.update_episode(ep_id, lambda e: e.model_copy(update={"pending": saved}))
    read = sys.deps.store.get_episode
    # This delivery read the episode a moment before another one saved its step.
    sys.deps.store.get_episode = lambda id: read(id).model_copy(update={"pending": None})
    with pytest.raises(StepInProgress):
        handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.OK))
    sys.deps.store.get_episode = read
    assert sys.deps.store.get_episode(ep_id).pending == saved


def test_a_step_carried_out_again_never_queues_a_call_that_was_stopped_meanwhile(sys):
    request_check(sys)
    call = only_new_call(sys)
    sys.deps.store.complete_call(call.id, Answer.NEEDS_HELP, None, None)
    send, failed = sys.deps.notifier.send, []

    def fails_once(*args):
        if not failed:
            failed.append(args)
            raise RuntimeError("Telegram hiccup")
        return send(*args)

    sys.deps.notifier.send = fails_once
    with pytest.raises(RuntimeError):
        handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    (ask,) = neighbour_calls(sys)
    sys.deps.store.patch_call(ask.id, status="blocked")  # voice ran its task and stopped it (calls switched off)
    sys.deps.queue.tasks.clear()
    sys.clock.advance(120)
    handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    assert [t for t in sys.deps.queue.tasks if t.kind == "call"] == []
    assert "neighbor_called" not in sent_keys(sys) and "call_unwell" in sent_keys(sys)


def test_a_finished_step_never_clears_the_step_saved_after_it(sys):
    from kuckuc.engine.app import _step_done

    ep_id = request_check(sys)
    finished = sys.deps.store.get_episode(ep_id)
    newer = [{"type": "Notify", "key": "call_unwell", "urgent": True}]
    sys.deps.store.update_episode(ep_id, lambda e: e.model_copy(update={"steps": e.steps + 1, "pending": newer}))
    _step_done(sys.deps, finished)
    assert sys.deps.store.get_episode(ep_id).pending == newer


def test_a_call_another_run_created_a_moment_ago_is_neither_created_nor_queued_again(sys):
    request_check(sys)
    call = only_new_call(sys)
    sys.deps.store.complete_call(call.id, Answer.NEEDS_HELP, None, None)
    create = sys.deps.store.create_call

    def created_meanwhile(c):
        create(c)  # the other run, a moment earlier; voice has stopped it since
        sys.deps.store.patch_call(c.id, status="blocked")
        return create(c)

    sys.deps.store.create_call = created_meanwhile
    sys.deps.queue.tasks.clear()
    handle_event(sys.deps, "m-1", "call.completed", completion(call, Answer.NEEDS_HELP))
    assert [t for t in sys.deps.queue.tasks if t.kind == "call"] == []
    assert "neighbor_called" not in sent_keys(sys)


def test_a_call_and_an_urgent_message_name_the_emergency_number_of_the_persons_country(sys):
    sys.deps.store.put_person(make_person(phone="+49000000"))  # speaks Serbian, lives in Germany
    request_check(sys)
    call = only_new_call(sys)
    assert call.params["emergency"] == "112"
    complete(sys, call, Answer.NEEDS_HELP)
    unwell = [n for n in sys.deps.notifier.sent if n.key == "call_unwell"]
    assert unwell and unwell[-1].params["emergency"] == "112"


def test_serbian_urgent_messages_name_the_number_they_were_given():
    p = {"name": "baba Milena", "neighbor": "Dragan"}
    assert render_message("needs_help_194", "sr", p | {"emergency": "112"}) == (
        "HITNO: Dragan javlja da baba Milena treba pomoć. Pozovi hitnu pomoć 112."
    )
    assert render_message("needs_help_194", "sr", p | {"emergency": ""}).endswith("Pozovi hitnu pomoć.")
    assert render_message("nobody_reachable_unwell", "sr", p | {"emergency": "112"}).endswith(
        "Pozovi odmah nekoga ko je blizu ili hitnu pomoć 112."
    )
    # A message saved before the number was stored with it: every person then lived in Serbia.
    assert render_message("needs_help_194", "sr", p).endswith("Pozovi hitnu pomoć 194.")


# --- call budgets --------------------------------------------------------------------------------


def _payer(sys, **over):
    from kuckuc.models import Payer

    payer = Payer(id="own", name="Kuc-kuc", month_cap_usd=100.0, tz="Europe/Belgrade").model_copy(update=over)
    sys.deps.store.put_payer(payer)
    return payer


def test_a_check_holds_its_worst_case_before_its_first_call_and_only_then(sys):
    from kuckuc.budget import worst_case_usd

    _payer(sys)
    ep_id = request_check(sys)
    (hold,) = sys.deps.store.list_open_holds()
    phones = [n.phone for n in sys.deps.store.list_neighbors("p1")]
    expected = worst_case_usd(EpisodeKind.MANUAL, make_person().phone, phones, sys.deps.settings.call_price_per_min_usd)
    assert (hold.episode_id, hold.payer_id, hold.usd) == (ep_id, "own", round(expected, 4))
    assert sys.deps.store.get_budget("own:month:2026-11")["reserved"] == round(expected, 4)


def test_a_started_check_finishes_even_when_the_money_has_run_out_since(sys):
    _payer(sys)
    request_check(sys)
    call = only_new_call(sys)
    _payer(sys, month_cap_usd=0.0)  # the budget was cut while she was being called
    complete(sys, call, Answer.NEEDS_HELP)
    assert [c.target for c in real_calls(sys)][-1] == Target.NEIGHBOR  # the neighbour is still called
    assert "budget_paused" not in sent_keys(sys)


def test_a_check_that_does_not_fit_the_budget_never_rings_and_the_family_is_told(sys):
    _payer(sys, month_cap_usd=0.05, staff_uids=["staff-1"])  # the test numbers are American: $0.014 a minute
    ep_id = request_check(sys)
    assert real_calls(sys) == []
    assert sys.deps.store.get_episode(ep_id).result == Result.BLOCKED
    sent = [(n.owner_uid, n.key, n.urgent) for n in sys.deps.notifier.sent]
    assert ("u1", "budget_paused", True) in sent and ("staff-1", "budget_paused_staff", True) in sent
    request_check(sys)  # a second one the same day: the family is told again, the staff only once
    keys = [(n.owner_uid, n.key) for n in sys.deps.notifier.sent]
    assert keys.count(("u1", "budget_paused")) == 2 and keys.count(("staff-1", "budget_paused_staff")) == 1


def test_a_person_whose_payer_has_no_budget_is_never_called(sys):
    sys.deps.store.put_person(make_person(payer_id="nobody"))
    request_check(sys)
    assert real_calls(sys) == [] and "budget_paused" in sent_keys(sys)


def test_a_busy_budget_sends_the_event_back_instead_of_blocking_the_check(sys):
    _payer(sys)
    sys.deps.store.reserve_budget = lambda hold, caps: "busy"
    with pytest.raises(RuntimeError):  # the event is delivered again later
        handle_event(sys.deps, "m-busy", "check.requested", {"person_id": "p1", "kind": "manual"})
    assert real_calls(sys) == [] and "budget_paused" not in sent_keys(sys)


def test_budget_messages_in_both_languages():
    p = {"name": "baba Milena", "payer": "Org"}
    assert render_message("budget_paused", "sr", p) == (
        "HITNO: pozivi su pauzirani jer je budžet za pozive potrošen, pa provera za baba Milena nije obavljena. "
        "Proveri odmah: pozovi telefonom ili zamoli nekoga ko je blizu."
    )
    assert render_message("budget_paused_staff", "en", p) == (
        "URGENT: the call budget is used up (Org). No new checks start until the budget is raised."
    )
    assert render_message("budget_paused", "en", p).startswith("URGENT: ")
    assert render_message("budget_paused_staff", "sr", p).startswith("HITNO: ")


def test_staff_who_were_not_reached_are_tried_again_on_the_next_refused_check(sys):
    _payer(sys, month_cap_usd=0.05, staff_uids=["staff-1"])
    send, tries = sys.deps.notifier.send, []

    def telegram_down_once(uid, key, params, urgent):
        if key == "budget_paused_staff":
            tries.append(uid)
            if len(tries) == 1:
                return False
        return send(uid, key, params, urgent)

    sys.deps.notifier.send = telegram_down_once
    request_check(sys)
    request_check(sys)
    request_check(sys)
    assert tries == ["staff-1", "staff-1"]  # not reached the first time, reached the second, then quiet


def test_outside_its_season_a_payers_check_does_not_start_and_staff_are_not_told_to_raise_money(sys):
    from datetime import date

    _payer(sys, month_cap_usd=None, season_cap_usd=500.0, season_start=date(2027, 6, 1),
           season_end=date(2027, 9, 30), staff_uids=["staff-1"])  # fmt: skip
    request_check(sys)
    assert real_calls(sys) == []
    keys = [(n.owner_uid, n.key) for n in sys.deps.notifier.sent]
    assert ("u1", "budget_off_season") in keys and not any(uid == "staff-1" for uid, _ in keys)


def test_a_check_that_holds_its_money_is_never_refused_when_its_first_call_is_carried_out_again(sys):
    from datetime import date

    from kuckuc.engine.app import _hold_budget

    _payer(sys)
    ep_id = request_check(sys)
    # The season ends between the first try and the step being carried out again (a minute later).
    _payer(sys, month_cap_usd=None, season_cap_usd=5.0, season_start=date(2026, 6, 1), season_end=date(2026, 9, 30))
    assert _hold_budget(sys.deps, sys.deps.store.get_episode(ep_id), sys.deps.store.get_person("p1")) is None


def test_off_season_messages_in_both_languages():
    p = {"name": "baba Milena"}
    assert render_message("budget_off_season", "sr", p).startswith("HITNO: pozivi su pauzirani jer ugovor za pozive")
    assert render_message("budget_off_season", "en", p).startswith("URGENT: calls are paused because the call contract")
