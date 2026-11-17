import base64
import json

import pytest

from factories import make_episode, make_neighbor, make_person
from fastapi.testclient import TestClient
from helpers import complete, only_call, only_new_call, real_calls, request_check, seed_person_calls

from kuckuc.engine.app import handle_event
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
