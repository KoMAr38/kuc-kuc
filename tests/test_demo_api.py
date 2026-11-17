from datetime import timedelta

import pytest
from factories import TODAY
from fastapi.testclient import TestClient
from helpers import build_sys

from kuckuc.models import Answer, EpisodeKind, PersonStatus, Target

PHONE = "(415) 555-0100"
E164 = "+14155550100"


@pytest.fixture
def web():
    sys = build_sys()
    client = TestClient(sys.web)
    client.deps = sys.deps
    client.sys = sys
    return client


def verified_token(web, phone=PHONE):
    assert web.post("/api/demo/verify/start", json={"phone": phone, "channel": "sms"}).status_code == 200
    return web.post("/api/demo/verify/check", json={"phone": phone, "code": "123456"}).json()["token"]


def start_demo(web, scenario="heat"):
    token = verified_token(web)
    ep = web.post("/api/demo/run", json={"token": token, "scenario": scenario}).json()["episode_id"]
    return ep, token


def test_happy_path_starts_demo_episode(web):
    assert web.post("/api/demo/verify/start", json={"phone": "(415) 555-0100", "channel": "sms"}).status_code == 200
    token = web.post("/api/demo/verify/check", json={"phone": "(415) 555-0100", "code": "123456"}).json()["token"]
    ep = web.post("/api/demo/run", json={"token": token, "scenario": "heat"}).json()["episode_id"]
    tl = web.get(f"/api/demo/timeline/{ep}", params={"token": token, "after": 0}).json()
    assert tl["events"][0]["type"] == "episode_started"
    assert tl["closed"] is False
    assert set(tl["events"][0]) == {"seq", "at", "type", "public"}


def test_verification_goes_to_the_normalised_number_by_the_chosen_channel(web):
    web.post("/api/demo/verify/start", json={"phone": PHONE, "channel": "call"})
    assert web.deps.verify.started == [(E164, "call")]


def test_wrong_code(web):
    web.post("/api/demo/verify/start", json={"phone": "+14155550100", "channel": "sms"})
    r = web.post("/api/demo/verify/check", json={"phone": "+14155550100", "code": "000000"})
    assert r.status_code == 400 and r.json() == {"code": "wrong_code"}


def test_serbian_number_refused_before_any_verification_is_sent(web):
    r = web.post("/api/demo/verify/start", json={"phone": "+381111234567", "channel": "sms"})
    assert r.status_code == 400 and r.json()["code"] == "country" and web.deps.verify.started == []


def test_premium_number_is_never_verified_or_dialled(web):
    r = web.post("/api/demo/verify/start", json={"phone": "+19005550100", "channel": "call"})
    assert r.status_code == 400 and r.json()["code"] == "number_type" and web.deps.verify.started == []


def test_the_demo_person_is_english_temporary_and_called_on_the_verified_number(web):
    ep, _ = start_demo(web)
    episode = web.deps.store.get_episode(ep)
    person = web.deps.store.get_person(episode.person_id)
    assert episode.kind == EpisodeKind.DEMO and ep.startswith("demo-") and person.id.startswith("demo-")
    assert (person.phone, person.lang, person.demo, person.status) == (E164, "en", True, PersonStatus.ACTIVE)
    (call,) = [c for c in web.deps.store.all_calls() if c.episode_id == ep]
    assert (call.to, call.target, call.lang) == (E164, Target.PERSON, "en")


def test_scenario_sets_the_hazard_and_the_temperature_in_fahrenheit(web):
    token = verified_token(web)
    hot = web.post("/api/demo/run", json={"token": token, "scenario": "heat"}).json()["episode_id"]
    cold = web.post("/api/demo/run", json={"token": token, "scenario": "cold"}).json()["episode_id"]
    assert (web.deps.store.get_episode(hot).hazard, web.deps.store.get_episode(hot).value) == ("heat", 104)
    assert (web.deps.store.get_episode(cold).hazard, web.deps.store.get_episode(cold).value) == ("cold", 5)


def test_unknown_scenario_is_rejected(web):
    token = verified_token(web)
    assert web.post("/api/demo/run", json={"token": token, "scenario": "tornado"}).status_code == 422


def test_run_needs_a_valid_token(web):
    r = web.post("/api/demo/run", json={"token": "made-up", "scenario": "heat"})
    assert r.status_code == 401 and r.json()["code"] == "session"
    assert web.deps.bus.published == []


def test_token_stops_working_after_a_day(web):
    token = verified_token(web)
    web.sys.clock.advance(int(timedelta(hours=24, minutes=1).total_seconds()))
    assert web.post("/api/demo/run", json={"token": token, "scenario": "heat"}).status_code == 401


def test_timeline_needs_owning_token(web):
    ep, token = start_demo(web)
    assert web.get(f"/api/demo/timeline/{ep}", params={"token": "other", "after": 0}).status_code == 403


def test_another_visitors_token_cannot_read_my_timeline(web):
    ep, _ = start_demo(web)
    other = verified_token(web, phone="+1 604 555 0100")
    assert web.get(f"/api/demo/timeline/{ep}", params={"token": other, "after": 0}).status_code == 403


def test_timeline_of_a_real_persons_episode_is_never_served(web):
    web.deps.bus.publish("check.requested", {"person_id": "p1", "kind": "manual", "episode_id": "real-1"})
    token = verified_token(web)
    assert web.get("/api/demo/timeline/real-1", params={"token": token, "after": 0}).status_code == 403


def test_timeline_returns_only_new_rows_and_says_when_it_is_over(web):
    ep, token = start_demo(web)
    first = web.get(f"/api/demo/timeline/{ep}", params={"token": token, "after": 0}).json()
    last_seq = first["events"][-1]["seq"]
    web.sys.deps.interpreter.answers = [Answer.OK]
    web.sys.answer_next_call()
    later = web.get(f"/api/demo/timeline/{ep}", params={"token": token, "after": last_seq}).json()
    assert all(e["seq"] > last_seq for e in later["events"])
    assert later["events"][-1]["type"] == "episode_closed" and later["closed"] is True


def test_run_adds_spend_and_third_run_is_refused(web):
    ep, token = start_demo(web)
    web.post("/api/demo/run", json={"token": token, "scenario": "cold"})
    assert web.deps.store.get_demo_spend(TODAY) > 0
    r = web.post("/api/demo/run", json={"token": token, "scenario": "cold"})
    assert r.json()["code"] == "phone_limit" and r.status_code == 429
    assert web.deps.store.get_demo_spend(TODAY) == pytest.approx(0.06 + 2 * (2 * 0.014))  # one code, two runs of calls


def test_spent_budget_refuses_before_a_code_is_sent(web):
    web.deps.store.add_demo_spend(TODAY, 14.99)  # TODAY is launch day: the cap is 15
    r = web.post("/api/demo/verify/start", json={"phone": PHONE, "channel": "sms"})
    assert r.status_code == 503 and r.json()["code"] == "budget" and web.deps.verify.started == []


def test_low_account_balance_closes_the_demo(web):
    web.deps.verify.balance = 9.0
    r = web.post("/api/demo/verify/start", json={"phone": PHONE, "channel": "sms"})
    assert r.status_code == 503 and r.json()["code"] == "low_balance"


def test_one_address_cannot_send_codes_without_end(web):
    headers = {"X-Forwarded-For": "203.0.113.9"}
    for i in range(10):
        r = web.post("/api/demo/verify/start", json={"phone": f"+1415555{1000 + i}", "channel": "sms"}, headers=headers)
        assert r.status_code == 200
    r = web.post("/api/demo/verify/start", json={"phone": "+14155552000", "channel": "sms"}, headers=headers)
    assert r.status_code == 429 and r.json()["code"] == "ip_limit"
    assert len(web.deps.verify.started) == 10


def test_address_is_taken_from_the_entry_the_platform_adds_not_from_what_the_client_claims(web):
    spoofed = lambda n: {"X-Forwarded-For": f"10.0.0.{n}, 203.0.113.9"}  # noqa: E731
    for i in range(10):
        web.post("/api/demo/verify/start", json={"phone": f"+1415555{1000 + i}", "channel": "sms"}, headers=spoofed(i))
    r = web.post("/api/demo/verify/start", json={"phone": "+14155552000", "channel": "sms"}, headers=spoofed(99))
    assert r.json().get("code") == "ip_limit"


def test_failed_code_delivery_is_reported_without_details(web):
    def broken(phone, channel):
        raise RuntimeError(f"cannot text {phone}")

    web.deps.verify.start = broken
    r = web.post("/api/demo/verify/start", json={"phone": PHONE, "channel": "sms"})
    assert r.status_code == 502 and r.json() == {"code": "verify_failed"}


def test_timeline_never_contains_the_phone_number(web):
    ep, token = start_demo(web)
    assert "4155550100" not in web.get(f"/api/demo/timeline/{ep}", params={"token": token, "after": 0}).text


def test_the_session_does_not_keep_the_number_itself(web):
    token = verified_token(web)
    session = web.deps.store.get_demo_session_by_token(token)
    assert session.phone == "" and "4155550100" not in session.phone_hash and session.country == "US"


def test_the_services_own_number_is_refused_with_a_clear_reason(web):
    # People copy the number that just called them. No code is sent to it and nothing is dialled.
    web.deps.settings.twilio_from_number = "+14155550199"
    r = web.post("/api/demo/verify/start", json={"phone": "(415) 555-0199", "channel": "sms"})
    assert r.status_code == 400 and r.json() == {"code": "own_number"}
    assert web.deps.verify.started == []
    r = web.post("/api/demo/verify/check", json={"phone": "+1 415 555 0199", "code": "123456"})
    assert r.status_code == 400 and r.json() == {"code": "own_number"}


def test_one_number_gets_at_most_three_codes_a_day_from_any_number_of_addresses(web):
    # Otherwise anyone could have our account ring or text a stranger again and again.
    for n in range(3):
        r = web.post(
            "/api/demo/verify/start", json={"phone": PHONE, "channel": "call"}, headers={"X-Forwarded-For": f"203.0.113.{n}"}
        )
        assert r.status_code == 200
    r = web.post("/api/demo/verify/start", json={"phone": PHONE, "channel": "call"}, headers={"X-Forwarded-For": "203.0.113.99"})
    assert r.status_code == 429 and r.json()["code"] == "phone_limit"
    assert len(web.deps.verify.started) == 3


def test_every_code_sent_counts_against_the_days_budget(web):
    web.post("/api/demo/verify/start", json={"phone": PHONE, "channel": "sms"})
    assert web.deps.store.get_demo_spend(TODAY) == pytest.approx(0.06)
    web.post("/api/demo/verify/start", json={"phone": "+14155550101", "channel": "call"})
    assert web.deps.store.get_demo_spend(TODAY) == pytest.approx(0.06 + 0.06 + 0.014)  # a code by call adds a minute


def test_a_code_that_could_not_be_sent_costs_nothing(web):
    def down(phone, channel):
        raise RuntimeError("provider down")

    web.deps.verify.start = down
    assert web.post("/api/demo/verify/start", json={"phone": PHONE, "channel": "sms"}).status_code == 502
    assert web.deps.store.get_demo_spend(TODAY) == 0
