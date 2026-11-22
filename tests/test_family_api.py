import json

import pytest
from fastapi.testclient import TestClient
from helpers import build_sys, complete, real_calls

from kuckuc.models import Answer, EpisodeKind, EpisodeState, PersonStatus, ScriptKind, Target

INVITE = "TEST-INVITE"
PERSON_PHONE = "+14155550106"


def client_for(sys, email=None):
    """A browser of its own. With an email, it is signed in (the local system accepts any credential)."""
    client = TestClient(sys.web)
    client.deps = sys.deps
    client.sys = sys
    if email:
        assert client.post("/api/auth/google", json={"credential": email}).status_code == 200
    return client


@pytest.fixture
def system():
    sys = build_sys()
    sys.deps.settings.invite_codes = (INVITE,)
    return sys


@pytest.fixture
def web(system):
    return client_for(system)


@pytest.fixture
def family(system):
    return client_for(system, "petar@example.com")


@pytest.fixture
def other_user(system):
    return client_for(system, "someone.else@example.com")


def enrol_body(**over):
    body = {
        "invite_code": INVITE,
        "name": "baba Vera",
        "family_name": "vaš unuk",
        "gender": "f",
        "phone": PERSON_PHONE,
        "lang": "sr",
        "lat": 44.8125,
        "lon": 20.4612,
        "place": "Beograd",
        "neighbors": [],
    }
    return body | over


def enrol(client, **over):
    r = client.post("/api/family/persons", json=enrol_body(**over))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def activate(client, pid):
    """The person says yes on the introduction call."""
    (intro,) = [c for c in real_calls(client.sys) if c.script == ScriptKind.INTRO_PERSON and c.status == "queued"]
    complete(client.sys, intro, Answer.YES)
    assert client.deps.store.get_person(pid).status == PersonStatus.ACTIVE


# --- signing in ------------------------------------------------------------------------------


def test_unauthenticated_is_401(web):
    assert web.get("/api/family/me").status_code == 401
    assert web.post("/api/family/persons", json=enrol_body()).status_code == 401
    assert web.post("/api/family/persons/x/call-now").status_code == 401
    assert web.get("/api/family/persons/x/episodes").status_code == 401
    assert web.get("/api/family/recordings/x").status_code == 401


def test_sign_in_creates_the_user_once_and_remembers_them(web):
    assert web.post("/api/auth/google", json={"credential": "petar@example.com"}).status_code == 200
    me = web.get("/api/family/me").json()
    assert me["email"] == "petar@example.com" and me["persons"] == [] and me["telegram_linked"] is False
    web.post("/api/auth/google", json={"credential": "petar@example.com"})
    assert web.get("/api/family/me").json()["email"] == "petar@example.com"


def test_invalid_google_credential_is_401(system):
    from kuckuc.web.app import create_web_app

    def reject(credential, client_id):
        raise ValueError("bad token")

    client = TestClient(create_web_app(system.deps, verify_credential=reject))
    assert client.post("/api/auth/google", json={"credential": "forged"}).status_code == 401
    assert client.get("/api/family/me").status_code == 401


def test_unverified_email_is_not_signed_in(system):
    from kuckuc.web.app import create_web_app

    claims = {"sub": "g-1", "email": "x@example.com", "email_verified": False}
    client = TestClient(create_web_app(system.deps, verify_credential=lambda c, cid: claims))
    assert client.post("/api/auth/google", json={"credential": "t"}).status_code == 401


def test_the_credential_is_checked_against_our_client_id(system):
    from kuckuc.web.app import create_web_app

    seen = []

    def verify(credential, client_id):
        seen.append((credential, client_id))
        return {"sub": "g-1", "email": "x@example.com", "email_verified": True}

    system.deps.settings.google_oauth_client_id = "our-client-id.apps.googleusercontent.com"
    TestClient(create_web_app(system.deps, verify_credential=verify)).post("/api/auth/google", json={"credential": "tok"})
    assert seen == [("tok", "our-client-id.apps.googleusercontent.com")]


def test_logout_ends_the_session(family):
    assert family.post("/api/auth/logout").status_code == 200
    assert family.get("/api/family/me").status_code == 401


# --- enrolling a person ----------------------------------------------------------------------


def test_enrol_requires_valid_invite(family):
    r = family.post("/api/family/persons", json=enrol_body(invite_code="nope"))
    assert r.status_code == 403
    assert family.deps.bus.published == []


def test_enrol_creates_pending_person_and_requests_intros(family):
    pid = family.post(
        "/api/family/persons", json=enrol_body(neighbors=[{"name": "Dragan", "phone": "+14155550107"}])
    ).json()["id"]
    assert family.deps.store.get_person(pid).status == PersonStatus.PENDING_CONSENT
    assert [t for t, _ in family.deps.bus.published].count("intro.requested") == 2
    assert family.deps.store.get_person(pid).tz == "Europe/Belgrade"


def test_enrolled_person_is_stored_as_entered_and_belongs_to_the_signed_in_user(family):
    pid = enrol(family, neighbors=[{"name": "Dragan", "phone": "+14155550107"}, {"name": "Rada", "phone": "+14155550108"}])
    person = family.deps.store.get_person(pid)
    me = family.deps.store.get_user(person.owner_uid)
    assert me.email == "petar@example.com"
    assert (person.name, person.family_name, person.gender, person.phone, person.lang, person.place) == (
        "baba Vera",
        "vaš unuk",
        "f",
        PERSON_PHONE,
        "sr",
        "Beograd",
    )
    assert (person.demo, person.sim) == (False, False)
    neighbours = family.deps.store.list_neighbors(pid)
    assert [(n.name, n.phone, n.order, n.status) for n in neighbours] == [
        ("Dragan", "+14155550107", 0, PersonStatus.PENDING_CONSENT),
        ("Rada", "+14155550108", 1, PersonStatus.PENDING_CONSENT),
    ]


def test_location_is_kept_only_roughly(family):
    pid = enrol(family, lat=44.812534, lon=20.461287)
    person = family.deps.store.get_person(pid)
    assert (person.lat, person.lon) == (44.81, 20.46)


def test_enrol_rejects_street_address_fields(family):
    assert family.post("/api/family/persons", json={**enrol_body(), "address": "Glavna 1"}).status_code == 422


def test_a_local_number_is_understood_in_the_country_where_the_person_lives(family):
    pid = enrol(family, phone="018 4 123 456", neighbors=[{"name": "Dragan", "phone": "0184123457"}])
    assert family.deps.store.get_person(pid).phone == "+381184123456"
    assert family.deps.store.list_neighbors(pid)[0].phone == "+381184123457"


@pytest.mark.parametrize("phone", ["12345", "", "not a number"])
def test_enrol_refuses_a_number_that_cannot_be_dialled(family, phone):
    r = family.post("/api/family/persons", json=enrol_body(phone=phone))
    assert r.status_code == 422 and r.json()["code"] == "bad_number"
    assert family.deps.bus.published == []


def test_at_most_three_neighbours(family):
    four = [{"name": f"N{i}", "phone": f"+1500555001{i}"} for i in range(4)]
    assert family.post("/api/family/persons", json=enrol_body(neighbors=four)).status_code == 422


def test_intro_calls_go_to_the_person_and_to_each_neighbour(family):
    pid = enrol(family, neighbors=[{"name": "Dragan", "phone": "+14155550107"}])
    calls = {c.script: c for c in real_calls(family.sys)}
    assert calls[ScriptKind.INTRO_PERSON].to == PERSON_PHONE
    assert calls[ScriptKind.INTRO_PERSON].params["family"] == "vaš unuk"
    assert calls[ScriptKind.INTRO_NEIGHBOR].to == "+14155550107"
    assert calls[ScriptKind.INTRO_NEIGHBOR].params["name"] == "baba Vera"
    assert family.deps.store.get_person(pid).status == PersonStatus.PENDING_CONSENT


# --- what the family sees --------------------------------------------------------------------


def test_me_masks_phone(family):
    enrol(family, neighbors=[{"name": "Dragan", "phone": "+14155550107"}])
    text = family.get("/api/family/me").text
    assert "4155550106" not in text and "4155550107" not in text and "5550106" not in text
    person = family.get("/api/family/me").json()["persons"][0]
    assert person["phone"].endswith("106") and person["neighbors"][0]["phone"].endswith("107")
    assert (person["name"], person["status"], person["place"]) == ("baba Vera", "pending_consent", "Beograd")
    assert person["neighbors"][0]["name"] == "Dragan" and person["neighbors"][0]["status"] == "pending_consent"


def test_me_lists_only_my_people(family, other_user):
    enrol(family)
    assert other_user.get("/api/family/me").json()["persons"] == []


def test_me_offers_a_telegram_link_once_a_bot_is_configured(family):
    assert family.get("/api/family/me").json()["telegram_link_url"] is None
    family.deps.settings.telegram_bot_name = "kuckuc_test_bot"
    url = family.get("/api/family/me").json()["telegram_link_url"]
    assert url.startswith("https://t.me/kuckuc_test_bot?start=") and len(url.split("start=")[1]) >= 16


def test_episodes_show_the_timeline_and_what_was_heard(family):
    pid = enrol(family)
    activate(family, pid)
    ep = family.post(f"/api/family/persons/{pid}/call-now").json()["episode_id"]
    family.sys.deps.interpreter.answers = [Answer.OK]
    family.sys.answer_next_call()
    episodes = family.get(f"/api/family/persons/{pid}/episodes").json()
    newest = episodes[0]
    assert (newest["id"], newest["kind"], newest["result"]) == (ep, "manual", "ok")
    assert [e["type"] for e in newest["events"]][-1] == "episode_closed"
    (call,) = newest["calls"]
    assert (call["role"], call["answer"], call["has_recording"]) == ("person", "ok", True)
    assert {e["kind"] for e in episodes} == {"manual", "intro"}
    assert "4155550106" not in json.dumps(episodes)


# --- acting ----------------------------------------------------------------------------------


def test_call_now_only_when_active(family):
    pid = enrol(family)
    assert family.post(f"/api/family/persons/{pid}/call-now").status_code == 409
    activate(family, pid)
    assert family.post(f"/api/family/persons/{pid}/call-now").status_code == 202


def test_call_now_starts_a_manual_check_on_that_persons_phone(family):
    pid = enrol(family)
    activate(family, pid)
    ep_id = family.post(f"/api/family/persons/{pid}/call-now").json()["episode_id"]
    episode = family.deps.store.get_episode(ep_id)
    assert (episode.kind, episode.person_id) == (EpisodeKind.MANUAL, pid)
    (call,) = [c for c in real_calls(family.sys) if c.episode_id == ep_id]
    assert (call.script, call.target, call.to) == (ScriptKind.CHECK_MANUAL, Target.PERSON, PERSON_PHONE)


def test_call_now_is_refused_while_a_check_is_already_running(family):
    pid = enrol(family)
    activate(family, pid)
    assert family.post(f"/api/family/persons/{pid}/call-now").status_code == 202
    r = family.post(f"/api/family/persons/{pid}/call-now")
    assert r.status_code == 409 and r.json()["code"] == "busy"


def test_pause_and_resume(family):
    pid = enrol(family)
    activate(family, pid)
    assert family.post(f"/api/family/persons/{pid}/pause", json={"paused": True}).status_code == 200
    assert family.deps.store.get_person(pid).status == PersonStatus.PAUSED
    assert family.post(f"/api/family/persons/{pid}/call-now").status_code == 409
    family.post(f"/api/family/persons/{pid}/pause", json={"paused": False})
    assert family.deps.store.get_person(pid).status == PersonStatus.ACTIVE


def test_pausing_cannot_replace_consent(family):
    pid = enrol(family)
    r = family.post(f"/api/family/persons/{pid}/pause", json={"paused": False})
    assert r.status_code == 409
    assert family.deps.store.get_person(pid).status == PersonStatus.PENDING_CONSENT


def test_intro_can_be_asked_again_only_while_consent_is_missing(family):
    pid = enrol(family)
    (intro,) = real_calls(family.sys)
    for _ in range(3):  # three unanswered tries close the first introduction
        (pending,) = [c for c in real_calls(family.sys) if c.status == "queued"]
        complete(family.sys, pending, Answer.NO_ANSWER)
    assert family.post(f"/api/family/persons/{pid}/intro").status_code == 202
    assert len([c for c in real_calls(family.sys) if c.status == "queued"]) == 1
    assert family.post(f"/api/family/persons/{pid}/intro").status_code == 409  # one is already under way
    activate(family, pid)
    assert family.post(f"/api/family/persons/{pid}/intro").status_code == 409


# --- other people's data ---------------------------------------------------------------------


def test_other_users_cannot_see_or_call(family, other_user):
    pid = enrol(family)
    assert other_user.get(f"/api/family/persons/{pid}/episodes").status_code == 404
    assert other_user.post(f"/api/family/persons/{pid}/call-now").status_code == 404
    assert other_user.post(f"/api/family/persons/{pid}/pause", json={"paused": True}).status_code == 404
    assert other_user.post(f"/api/family/persons/{pid}/intro").status_code == 404


def test_demo_and_unknown_people_are_nobodys(family):
    assert family.get("/api/family/persons/p1/episodes").status_code == 404  # p1 belongs to "u1" in the fixtures
    assert family.get("/api/family/persons/ghost/episodes").status_code == 404


def test_recording_is_played_only_to_the_owner(family, other_user):
    pid = enrol(family)
    activate(family, pid)
    family.post(f"/api/family/persons/{pid}/call-now")
    family.sys.deps.interpreter.answers = [Answer.OK]
    family.sys.answer_next_call()
    call_id = family.get(f"/api/family/persons/{pid}/episodes").json()[0]["calls"][0]["id"]
    mine = family.get(f"/api/family/recordings/{call_id}")
    assert mine.status_code == 200 and mine.headers["content-type"] == "audio/wav"
    assert mine.content == family.sys.deps.telephony.recording
    assert other_user.get(f"/api/family/recordings/{call_id}").status_code == 404


def test_recording_that_has_been_deleted_is_404(family):
    pid = enrol(family)
    activate(family, pid)
    family.post(f"/api/family/persons/{pid}/call-now")
    family.sys.deps.interpreter.answers = [Answer.OK]
    family.sys.answer_next_call()
    call_id = family.get(f"/api/family/persons/{pid}/episodes").json()[0]["calls"][0]["id"]
    family.sys.deps.blobs.items.clear()  # what the 7-day rule does
    assert family.get(f"/api/family/recordings/{call_id}").status_code == 404
    assert family.get("/api/family/recordings/ghost").status_code == 404


# --- the page and its helpers ----------------------------------------------------------------


def test_family_page_is_served_and_names_no_one(web):
    html = web.get("/family").text
    assert web.get("/family").status_code == 200
    assert 'id="family-app"' in html and '<script src="/static/family.js?v=' in html
    assert "petar@example.com" not in html and "baba" not in html


def test_family_page_uses_google_sign_in_only_when_a_client_id_is_set(web):
    local = web.get("/family")
    assert "accounts.google.com" not in local.text and "accounts.google.com" not in local.headers["content-security-policy"]
    assert 'id="local-sign-in"' in local.text
    web.deps.settings.google_oauth_client_id = "abc.apps.googleusercontent.com"
    cloud = web.get("/family")
    assert 'data-client-id="abc.apps.googleusercontent.com"' in cloud.text
    assert "https://accounts.google.com/gsi/client" in cloud.text
    assert "https://accounts.google.com/gsi/client" in cloud.headers["content-security-policy"]
    assert 'id="local-sign-in"' not in cloud.text


def test_other_pages_keep_the_strict_policy_even_with_google_sign_in_configured(web):
    web.deps.settings.google_oauth_client_id = "abc.apps.googleusercontent.com"
    assert "accounts.google.com" not in web.get("/").headers["content-security-policy"]


def test_language_can_be_changed_and_is_remembered(family):
    assert family.post("/api/family/lang", json={"lang": "sr"}).status_code == 200
    assert family.get("/api/family/me").json()["lang"] == "sr"
    assert family.post("/api/family/lang", json={"lang": "de"}).status_code == 422


def test_place_search_returns_names_and_coordinates(system):
    asked = []

    def http_get(url, params):
        asked.append((url, params))
        return {
            "results": [
                {"name": "Beograd", "admin1": "Central Serbia", "country": "Serbia", "latitude": 44.80401, "longitude": 20.46513},
                {"name": "Beograd", "country": "Serbia", "latitude": 43.1, "longitude": 21.9, "extra": "ignored"},
            ]
        }

    from kuckuc.web.app import create_web_app
    from kuckuc.local import _local_sign_in

    client = TestClient(create_web_app(system.deps, verify_credential=_local_sign_in, http_get=http_get))
    assert client.get("/api/family/places", params={"q": "Beograd"}).status_code == 401
    client.post("/api/auth/google", json={"credential": "petar@example.com"})
    places = client.get("/api/family/places", params={"q": " Beograd "}).json()
    assert places == [
        {"name": "Beograd", "region": "Central Serbia", "country": "Serbia", "lat": 44.80401, "lon": 20.46513},
        {"name": "Beograd", "region": "", "country": "Serbia", "lat": 43.1, "lon": 21.9},
    ]
    url, params = asked[0]
    assert url == "https://geocoding-api.open-meteo.com/v1/search" and params["name"] == "Beograd"
    assert client.get("/api/family/places", params={"q": "B"}).json() == []
    assert len(asked) == 1  # too short to search


def test_place_search_that_fails_returns_nothing_rather_than_an_error(system):
    def down(url, params):
        raise RuntimeError("geocoding down")

    from kuckuc.web.app import create_web_app
    from kuckuc.local import _local_sign_in

    client = TestClient(create_web_app(system.deps, verify_credential=_local_sign_in, http_get=down))
    client.post("/api/auth/google", json={"credential": "petar@example.com"})
    assert client.get("/api/family/places", params={"q": "Beograd"}).json() == []


def test_forecast_for_my_person_says_which_day_is_dangerous(system):
    import json as _json
    from pathlib import Path

    cold = _json.loads((Path(__file__).parent / "fixtures" / "openmeteo_cold.json").read_text(encoding="utf-8"))
    asked = []

    def http_get(url, params):
        asked.append(params)
        return cold

    from kuckuc.web.app import create_web_app
    from kuckuc.local import _local_sign_in

    app = create_web_app(system.deps, verify_credential=_local_sign_in, http_get=http_get)
    mine, theirs = TestClient(app), TestClient(app)
    mine.post("/api/auth/google", json={"credential": "petar@example.com"})
    theirs.post("/api/auth/google", json={"credential": "other@example.com"})
    pid = mine.post("/api/family/persons", json=enrol_body()).json()["id"]
    days = mine.get(f"/api/family/persons/{pid}/forecast").json()["days"]
    assert [(d["day"], d["hazard"]) for d in days] == [("2026-11-20", None), ("2026-11-21", "cold"), ("2026-11-22", None)]
    assert days[1]["temp_min_c"] == -12.4
    assert (asked[0]["latitude"], asked[0]["longitude"]) == (44.8, 20.5)  # the cell, not the stored point
    assert theirs.get(f"/api/family/persons/{pid}/forecast").status_code == 404


def test_family_script_has_both_languages_for_every_text():
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "src" / "kuckuc" / "web" / "static" / "family.js").read_text(encoding="utf-8")
    block = js[js.index("var TEXT = {") : js.index("};", js.index("var TEXT = {"))]
    keys = re.findall(r"^\s{4}(\w+): \{", block, flags=re.M)
    assert len(keys) > 25
    for key in keys:
        entry = block[block.index(f"    {key}: {{") :].split("},", 1)[0]
        assert "sr:" in entry and "en:" in entry, key


def test_the_real_site_without_a_client_id_offers_no_way_to_sign_in():
    from kuckuc.deps import build_memory_deps
    from kuckuc.web.app import create_web_app

    live = TestClient(create_web_app(build_memory_deps()))  # the real Google check, and no client id configured
    html = live.get("/family").text
    assert 'id="local-sign-in"' not in html and 'id="sign-in-off"' in html
    assert live.post("/api/auth/google", json={"credential": "me@example.com"}).status_code == 401


def test_the_google_check_refuses_everything_until_a_client_id_is_configured():
    from kuckuc.web.family import verify_google_credential

    with pytest.raises(ValueError, match="not set up"):
        verify_google_credential("any.token.at-all", "")


# --- nobody is woken by an introduction ------------------------------------------------------


def night(client):
    client.sys.clock.advance(int(13.5 * 3600))  # from 10:00 to 23:30 in Belgrade: past 23:00, introductions wait


def test_enrolling_at_night_calls_nobody_until_eight_in_the_morning_there(family):
    night(family)
    reply = family.post("/api/family/persons", json=enrol_body(neighbors=[{"name": "Dragan", "phone": "+14155550107"}]))
    assert reply.status_code == 201 and reply.json()["intro_at_local"] == "08:00"
    family.sys.run_tasks(0)
    assert real_calls(family.sys) == [] and family.deps.telephony.placed == []
    family.sys.run_tasks(advance_s=int(8.5 * 3600) - 60)  # 07:59 there
    assert real_calls(family.sys) == []
    family.sys.run_tasks(advance_s=60)  # 08:00 there
    assert sorted(c.script for c in real_calls(family.sys)) == [ScriptKind.INTRO_NEIGHBOR, ScriptKind.INTRO_PERSON]
    assert len(family.deps.telephony.placed) == 2


def test_enrolling_by_day_starts_the_introductions_at_once(family):
    reply = family.post("/api/family/persons", json=enrol_body())
    assert reply.json()["intro_at_local"] is None
    assert [c.script for c in real_calls(family.sys)] == [ScriptKind.INTRO_PERSON]


def test_asking_again_at_night_waits_for_the_morning_and_is_never_doubled(family):
    pid = enrol(family)
    for _ in range(3):  # three unanswered tries close the first introduction
        (pending,) = [c for c in real_calls(family.sys) if c.status == "queued"]
        complete(family.sys, pending, Answer.NO_ANSWER)
    night(family)
    reply = family.post(f"/api/family/persons/{pid}/intro")
    assert reply.status_code == 202 and reply.json()["intro_at_local"] == "08:00"
    assert [c for c in real_calls(family.sys) if c.status == "queued"] == []
    assert family.post(f"/api/family/persons/{pid}/intro").status_code == 409  # the morning call is already planned


def test_the_sign_in_page_tells_google_which_site_is_asking_and_nothing_more(web):
    # Google's button checks the page's origin from the Referer header; "no-referrer" would hide it.
    assert web.get("/family").headers["referrer-policy"] == "no-referrer"
    web.deps.settings.google_oauth_client_id = "abc.apps.googleusercontent.com"
    assert web.get("/family").headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert web.get("/").headers["referrer-policy"] == "no-referrer"
    assert web.get("/architecture").headers["referrer-policy"] == "no-referrer"


# --- notices must have somewhere to go -------------------------------------------------------


def test_enrolment_waits_until_telegram_is_connected_once_a_bot_exists(family):
    family.deps.settings.telegram_bot_name = "kuckuc_test_bot"
    reply = family.post("/api/family/persons", json=enrol_body())
    assert reply.status_code == 409 and reply.json()["code"] == "telegram_first"
    user = family.deps.store.get_user("local-petar@example.com")  # the local preview's sign-in
    assert family.deps.store.list_persons_by_owner(user.uid) == []
    family.deps.store.put_user(user.model_copy(update={"telegram_chat_id": "777"}))
    assert family.post("/api/family/persons", json=enrol_body()).status_code == 201


def test_the_family_page_explains_how_to_connect_telegram_first():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "src" / "kuckuc" / "web" / "static" / "family.js").read_text(encoding="utf-8")
    assert "err_telegram_first" in js and "e_family_not_notified" in js


# --- who sees the way in ---------------------------------------------------------------------


def test_the_menu_shows_the_family_page_only_to_someone_signed_in(web, family):
    web.deps.settings.features = {"family"}
    assert 'href="/family"' not in web.get("/").text  # a visitor could not sign in anyway
    assert 'href="/family"' in family.get("/").text


def test_the_sign_in_page_says_it_is_for_invited_families(web):
    web.deps.settings.google_oauth_client_id = "abc.apps.googleusercontent.com"
    html = web.get("/family").text
    assert 'data-t="invite_only"' in html and "invited" in html


# --- one person, one enrolment ---------------------------------------------------------------


def test_the_same_number_cannot_be_enrolled_twice_by_one_family(family):
    # A double click once enrolled a grandmother twice, so every call came in pairs.
    assert family.post("/api/family/persons", json=enrol_body()).status_code == 201
    again = family.post("/api/family/persons", json=enrol_body(phone="+1 415 555 0106"))  # same number, other spelling
    assert again.status_code == 409 and again.json()["code"] == "already_enrolled"
    assert len(family.get("/api/family/me").json()["persons"]) == 1


def test_the_page_locks_the_enrol_button_while_it_waits(family):
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "src" / "kuckuc" / "web" / "static" / "family.js").read_text(encoding="utf-8")
    handler = js[js.index('$("enrol-form").addEventListener') :]
    assert "submit.disabled = true" in handler and "submit.disabled = false" in handler
    assert "err_already_enrolled" in js


# --- a neighbour who missed the introduction -------------------------------------------------


def missed_neighbour_intro(family):
    pid = enrol(family, neighbors=[{"name": "Dragan", "phone": "+14155550107"}])
    for _ in range(3):  # he does not answer three times
        (pending,) = [c for c in real_calls(family.sys) if c.status == "queued" and c.script == ScriptKind.INTRO_NEIGHBOR]
        complete(family.sys, pending, Answer.NO_ANSWER)
    (neighbor,) = family.get("/api/family/me").json()["persons"][0]["neighbors"]
    assert neighbor["status"] == "pending_consent"
    return pid, neighbor["id"]


def test_a_neighbour_who_missed_the_introduction_can_be_called_again(family):
    pid, nid = missed_neighbour_intro(family)
    reply = family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro")
    assert reply.status_code == 202 and reply.json()["intro_at_local"] is None
    queued = [c for c in real_calls(family.sys) if c.status == "queued" and c.script == ScriptKind.INTRO_NEIGHBOR]
    assert len(queued) == 1 and queued[0].neighbor_id == nid
    assert family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro").status_code == 409  # one is under way
    complete(family.sys, queued[0], Answer.YES)
    again = family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro")
    assert again.status_code == 409 and again.json()["code"] == "not_pending"


def test_nobody_else_can_call_my_neighbour(family, other_user):
    pid, nid = missed_neighbour_intro(family)
    assert other_user.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro").status_code == 404
    assert family.post(f"/api/family/persons/{pid}/neighbors/unknown/intro").status_code == 404


def test_at_night_the_family_may_still_call_a_neighbour_who_is_waiting_for_the_call(family):
    # The family has just talked to the neighbour, who now waits by the phone: the night rule
    # gives way when the family says so, as it does for "Call now".
    pid, nid = missed_neighbour_intro(family)
    night(family)
    reply = family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro", json={"now": True})
    assert reply.status_code == 202 and reply.json()["intro_at_local"] is None
    queued = [c for c in real_calls(family.sys) if c.status == "queued" and c.script == ScriptKind.INTRO_NEIGHBOR]
    assert len(queued) == 1


def test_at_night_without_asking_the_neighbours_call_waits_for_the_morning(family):
    pid, nid = missed_neighbour_intro(family)
    night(family)
    reply = family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro")
    assert reply.status_code == 202 and reply.json()["intro_at_local"] == "08:00"
    neighbour_calls = [c for c in real_calls(family.sys) if c.script == ScriptKind.INTRO_NEIGHBOR]
    assert [c for c in neighbour_calls if c.status == "queued"] == []


def test_the_page_offers_calling_the_neighbour_now_or_in_the_morning():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "src" / "kuckuc" / "web" / "static" / "family.js").read_text(encoding="utf-8")
    assert "neighbor_now" in js and "neighbor_morning" in js and "{ now: true }" in js


def test_an_introduction_asked_for_at_nine_in_the_evening_rings_at_once(family):
    # 20:00 was too early to stop: people are up, and the family is often just back from work.
    family.sys.clock.advance(11 * 3600)  # 21:00 in Belgrade
    reply = family.post("/api/family/persons", json=enrol_body())
    assert reply.json()["intro_at_local"] is None
    assert [c.script for c in real_calls(family.sys)] == [ScriptKind.INTRO_PERSON]


def test_the_page_knows_until_when_introductions_ring(web):
    assert 'data-intro-end="23"' in web.get("/family").text


# --- a neighbour recorded as "no" who has since agreed ---------------------------------------


def declined_neighbour(family):
    pid = enrol(family, neighbors=[{"name": "Dragan", "phone": "+14155550107"}])
    (call,) = [c for c in real_calls(family.sys) if c.status == "queued" and c.script == ScriptKind.INTRO_NEIGHBOR]
    complete(family.sys, call, Answer.NO)
    (neighbor,) = family.get("/api/family/me").json()["persons"][0]["neighbors"]
    assert neighbor["status"] == "declined"
    return pid, neighbor["id"]


def test_a_neighbour_recorded_as_no_is_called_again_only_when_the_family_says_they_agreed(family):
    # His "Da" was spoken while the voice was still talking and was not recorded; the family talked to him.
    pid, nid = declined_neighbour(family)
    first = family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro")
    assert first.status_code == 409 and first.json()["code"] == "agreed_needed"
    again = family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro", json={"agreed": True})
    assert again.status_code == 202
    (call,) = [c for c in real_calls(family.sys) if c.status == "queued" and c.script == ScriptKind.INTRO_NEIGHBOR]
    complete(family.sys, call, Answer.YES)
    assert family.get("/api/family/me").json()["persons"][0]["neighbors"][0]["status"] == "active"


def test_the_page_asks_before_calling_a_neighbour_who_said_no():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "src" / "kuckuc" / "web" / "static" / "family.js").read_text(encoding="utf-8")
    assert "neighbor_declined_confirm" in js and "agreed: true" in js


def test_every_call_button_and_question_says_whom_it_calls():
    # A "Call now" on her card was taken for the neighbour's, and she was rung instead of him.
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "src" / "kuckuc" / "web" / "static" / "family.js").read_text(encoding="utf-8")
    block = js[js.index("var TEXT = {") : js.index("};", js.index("var TEXT = {"))]
    for key in ("call_now", "yes_call", "confirm_late", "neighbor_again", "neighbor_now", "neighbor_agreed"):
        entry = re.search(rf"^\s{{4}}{key}: \{{.*?\}},?$", block, flags=re.M | re.S).group(0)
        assert entry.count("{name}") == 2, key  # in Serbian and in English
    assert "function who(" in js and 't("role_" + p.role)' not in js


# --- audit 17 Nov ---------------------------------------------------------------------------


def hold_events(client):
    """Requests reach the engine a few seconds later in the cloud; here, not at all."""
    held = []
    client.deps.bus.publish = lambda type, data: held.append((type, data))
    return held


def test_two_quick_clicks_on_call_now_start_one_check(family):
    pid = enrol(family)
    activate(family, pid)
    held = hold_events(family)
    for _ in range(2):
        assert family.post(f"/api/family/persons/{pid}/call-now").status_code == 202
    ids = [d["episode_id"] for t, d in held if t == "check.requested"]
    assert len(ids) == 2 and ids[0] == ids[1]  # the engine starts an episode id once


def test_two_quick_clicks_on_a_neighbours_introduction_start_one(family):
    pid, nid = missed_neighbour_intro(family)
    held = hold_events(family)
    for _ in range(2):
        assert family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro", json={"now": True}).status_code == 202
    ids = [d.get("episode_id") for t, d in held if t == "intro.requested"]
    assert len(ids) == 2 and ids[0] and ids[0] == ids[1]


def test_a_persons_introduction_can_be_repeated_while_a_neighbours_is_under_way(family):
    pid = enrol(family, neighbors=[{"name": "Dragan", "phone": "+14155550107"}])
    for _ in range(3):
        (pending,) = [c for c in real_calls(family.sys) if c.status == "queued" and c.script == ScriptKind.INTRO_PERSON]
        complete(family.sys, pending, Answer.NO_ANSWER)
    assert any(c.status == "queued" and c.script == ScriptKind.INTRO_NEIGHBOR for c in real_calls(family.sys))
    assert family.post(f"/api/family/persons/{pid}/intro").status_code == 202


def test_a_premium_rate_number_is_never_enrolled(family):
    # Each enrolment rings the number; a paid line would cost money on every call.
    r = family.post("/api/family/persons", json=enrol_body(phone="+381 900 123 456"))
    assert r.status_code == 422 and r.json()["code"] == "bad_number"
    neighbour = {"name": "Dragan", "phone": "+1 900 555 0100"}
    r = family.post("/api/family/persons", json=enrol_body(neighbors=[neighbour]))
    assert r.status_code == 422 and r.json()["code"] == "bad_number"



# --- audit 17 Nov, second round -------------------------------------------------------------


def test_a_second_call_now_after_a_quick_check_starts_a_new_one(family):
    # She answered "fine" within the minute; the family clicks again in that same minute.
    pid = enrol(family)
    activate(family, pid)
    assert family.post(f"/api/family/persons/{pid}/call-now").status_code == 202
    (first,) = [c for c in real_calls(family.sys) if c.script == ScriptKind.CHECK_MANUAL]
    complete(family.sys, first, Answer.OK)
    assert family.post(f"/api/family/persons/{pid}/call-now").status_code == 202
    assert len([c for c in real_calls(family.sys) if c.script == ScriptKind.CHECK_MANUAL]) == 2


def test_clicks_on_both_sides_of_a_minute_start_one_check(family):
    pid = enrol(family)
    activate(family, pid)
    held = hold_events(family)
    family.sys.clock.advance(59)
    family.post(f"/api/family/persons/{pid}/call-now")
    family.sys.clock.advance(2)
    family.post(f"/api/family/persons/{pid}/call-now")
    ids = [d["episode_id"] for t, d in held if t == "check.requested"]
    assert len(ids) == 2 and ids[0] == ids[1]


def test_call_now_is_not_blocked_by_a_neighbours_introduction(family):
    # The neighbour missed his introduction and it is being tried again; she can still be checked on.
    pid = enrol(family, neighbors=[{"name": "Dragan", "phone": "+14155550107"}])
    activate(family, pid)
    assert any(c.status == "queued" and c.script == ScriptKind.INTRO_NEIGHBOR for c in real_calls(family.sys))
    assert family.post(f"/api/family/persons/{pid}/call-now").status_code == 202


def test_personal_and_satellite_numbers_are_never_enrolled(family):
    # UK 070 "personal numbers" are charged at a high rate; satellite numbers too.
    for number in ("+44 7012 345678", "+881 6 3123 4567"):
        r = family.post("/api/family/persons", json=enrol_body(phone=number))
        assert r.status_code == 422 and r.json()["code"] == "bad_number", number



# --- audit 17 Nov, third round --------------------------------------------------------------


def test_a_family_can_never_call_another_familys_neighbour(family, other_user):
    pid, nid = missed_neighbour_intro(family)
    theirs = enrol(other_user, phone="+14155550120")
    r = other_user.post(f"/api/family/persons/{theirs}/neighbors/{nid}/intro", json={"now": True})
    assert r.status_code == 404


def test_a_neighbours_introduction_planned_for_the_morning_is_not_planned_twice(family):
    pid, nid = missed_neighbour_intro(family)
    family.sys.clock.advance(int(13.5 * 3600))  # 23:30 in Belgrade: it waits for 08:00
    assert family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro", json={}).status_code == 202
    assert family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro", json={}).status_code == 409


def test_a_neighbour_with_the_persons_own_number_or_a_repeated_number_is_refused(family):
    same_as_person = [{"name": "Dragan", "phone": "+14155550106"}]
    r = family.post("/api/family/persons", json=enrol_body(neighbors=same_as_person))
    assert r.status_code == 422 and r.json()["code"] == "same_number"
    twice = [{"name": "Dragan", "phone": "+14155550107"}, {"name": "Rada", "phone": "+1 415 555 0107"}]
    r = family.post("/api/family/persons", json=enrol_body(neighbors=twice))
    assert r.status_code == 422 and r.json()["code"] == "same_number"
    family.deps.settings.twilio_from_number = "+14155550199"
    ours = [{"name": "Dragan", "phone": "+14155550199"}]
    r = family.post("/api/family/persons", json=enrol_body(neighbors=ours))
    assert r.status_code == 422 and r.json()["code"] == "same_number"


def test_a_neighbours_call_again_is_named_after_the_newest_episode_of_any_kind(family):
    # Named after the newest introduction among the 20 newest episodes, two clicks with many checks
    # in between both found none, got the same name, and the second rang nobody.
    from datetime import timedelta

    from factories import make_episode

    pid, nid = missed_neighbour_intro(family)
    held = hold_events(family)

    def many_checks(tag):
        for i in range(21):
            family.sys.clock.advance(60)
            family.deps.store.create_episode(make_episode(
                id=f"{tag}-{i}", person_id=pid, state=EpisodeState.CLOSED, created_at=family.sys.clock.now()
            ))  # fmt: skip

    many_checks("before")
    family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro", json={"now": True})
    (first,) = [d["episode_id"] for t, d in held if t == "intro.requested"]
    family.sys.clock.advance(60)
    family.deps.store.create_episode(make_episode(  # what the engine makes of that click
        id=first, person_id=pid, kind=EpisodeKind.INTRO_NEIGHBOR, neighbor_id=nid, state=EpisodeState.CLOSED,
        created_at=family.sys.clock.now(),
    ))  # fmt: skip
    many_checks("after")
    family.post(f"/api/family/persons/{pid}/neighbors/{nid}/intro", json={"now": True})
    ids = [d["episode_id"] for t, d in held if t == "intro.requested"]
    assert len(ids) == 2 and ids[0] != ids[1]


def test_a_recording_can_be_fetched_in_parts_as_iphones_ask(family):
    pid = enrol(family)
    activate(family, pid)
    (intro,) = [c for c in real_calls(family.sys) if c.script == ScriptKind.INTRO_PERSON]
    family.deps.blobs.put("recordings/x.wav", b"RIFF" + bytes(100), "audio/wav")
    family.deps.store.patch_call(intro.id, recording_key="recordings/x.wav")
    r = family.get(f"/api/family/recordings/{intro.id}", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == b"RIFF" and r.headers["content-range"] == "bytes 0-3/104"
    whole = family.get(f"/api/family/recordings/{intro.id}")
    assert whole.status_code == 200 and len(whole.content) == 104 and whole.headers["accept-ranges"] == "bytes"



# --- audit 20 Nov, fourth round -------------------------------------------------------------


def test_the_persons_number_may_not_be_the_services_own_number(family):
    family.deps.settings.twilio_from_number = "+14155550199"
    r = family.post("/api/family/persons", json=enrol_body(phone="+1 415 555 0199"))
    assert r.status_code == 422 and r.json()["code"] == "same_number"


def test_a_check_closed_long_ago_without_its_closing_row_is_not_shown_as_running(family):
    # The closing row is written a moment after the episode closes; when that write failed once, the
    # page used to look for it forever.
    from datetime import timedelta

    from factories import make_episode

    pid = enrol(family)
    clock = family.sys.clock
    episodes = {
        "closed-long-ago": dict(state=EpisodeState.CLOSED, updated_at=clock.now() - timedelta(minutes=3)),
        "closed-just-now": dict(state=EpisodeState.CLOSED, updated_at=clock.now()),
        "planned": dict(state=EpisodeState.SCHEDULED),
        "calling": dict(state=EpisodeState.CALLING_PERSON),
    }
    for id, fields in episodes.items():
        family.deps.store.create_episode(make_episode(id=id, person_id=pid, **fields))
    running = {e["id"]: e["running"] for e in family.get(f"/api/family/persons/{pid}/episodes").json() if e["id"] in episodes}
    assert running == {"closed-long-ago": False, "closed-just-now": True, "planned": False, "calling": True}
    family.deps.store.add_timeline("closed-just-now", "episode_closed", {"result": "ok"})
    running = {e["id"]: e["running"] for e in family.get(f"/api/family/persons/{pid}/episodes").json()}
    assert running["closed-just-now"] is False



def test_a_recording_asked_for_from_the_middle_past_its_end_or_beyond_it(family):
    pid = enrol(family)
    activate(family, pid)
    (intro,) = [c for c in real_calls(family.sys) if c.script == ScriptKind.INTRO_PERSON]
    family.deps.blobs.put("recordings/y.wav", b"RIFF" + bytes(100), "audio/wav")
    family.deps.store.patch_call(intro.id, recording_key="recordings/y.wav")
    url = f"/api/family/recordings/{intro.id}"
    tail = family.get(url, headers={"Range": "bytes=100-"})
    assert tail.status_code == 206 and tail.headers["content-range"] == "bytes 100-103/104" and len(tail.content) == 4
    long = family.get(url, headers={"Range": "bytes=0-99999"})
    assert long.status_code == 206 and long.headers["content-range"] == "bytes 0-103/104"
    beyond = family.get(url, headers={"Range": "bytes=500-"})
    assert beyond.status_code == 200 and len(beyond.content) == 104


def test_the_persons_introduction_asked_again_is_a_new_one_each_time(family):
    pid = enrol(family)
    for _ in range(3):  # three unanswered tries close the first introduction
        (pending,) = [c for c in real_calls(family.sys) if c.status == "queued"]
        complete(family.sys, pending, Answer.NO_ANSWER)
    intros = set()
    for _ in range(2):
        family.sys.clock.advance(24 * 3600)  # a day later each time, so the daily limit of calls is not in the way
        assert family.post(f"/api/family/persons/{pid}/intro").status_code == 202
        intros |= {e.id for e in family.deps.store.list_episodes(pid) if e.kind == EpisodeKind.INTRO}
        for _ in range(3):
            (pending,) = [c for c in real_calls(family.sys) if c.status == "queued"]
            complete(family.sys, pending, Answer.NO_ANSWER)
    assert len(intros) == 3  # the first, and one for each time it was asked again


def test_a_person_can_be_called_in_german_while_the_page_stays_in_serbian_or_english(family):
    pid = enrol(family, name="Frau Weber", family_name="Ihr Enkel Peter", lang="de")
    assert family.deps.store.get_person(pid).lang == "de"
    (intro,) = [c for c in real_calls(family.sys) if c.script == ScriptKind.INTRO_PERSON]
    assert intro.lang == "de"
    assert family.post("/api/family/lang", json={"lang": "de"}).status_code == 422



def test_the_invite_code_decides_who_pays_for_the_calls(family):
    from kuckuc.models import Payer

    family.deps.store.put_payer(Payer(id="org-1", name="Org", month_cap_usd=50, invite_code="ORG-CODE"))
    own = enrol(family)
    theirs = enrol(family, invite_code="ORG-CODE", phone="+14155550107")
    assert family.deps.store.get_person(own).payer_id == "own"
    assert family.deps.store.get_person(theirs).payer_id == "org-1"
    assert family.post("/api/family/persons", json=enrol_body(invite_code="NOT-A-CODE")).status_code == 403


def test_only_a_payers_staff_see_what_its_calls_have_cost(family, other_user):
    from kuckuc.models import BudgetHold, Payer

    staff_uid = family.get("/api/family/me").json()["uid"]
    family.deps.store.put_payer(
        Payer(id="org-1", name="Org", month_cap_usd=50, tz="Europe/Belgrade", staff_uids=[staff_uid])
    )
    hold = BudgetHold(episode_id="e1", payer_id="org-1", keys=[], usd=6.0, created_at=family.sys.clock.now())
    family.deps.store.reserve_budget(hold, [("org-1:month:2026-11", 50.0)])
    family.deps.store.settle_budget("e1", 1.5)
    family.deps.store.reserve_budget(hold.model_copy(update={"episode_id": "e2"}), [("org-1:month:2026-11", 50.0)])
    assert family.get("/api/family/me").json()["budgets"] == [
        {"payer": "Org", "period": "month", "label": "2026-11", "cap_usd": 50.0, "spent_usd": 1.5, "reserved_usd": 6.0}
    ]
    assert other_user.get("/api/family/me").json()["budgets"] == []
