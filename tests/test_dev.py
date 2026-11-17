"""The local preview must behave like the real thing, only with a pretend phone and a fast clock."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from kuckuc.dev import create_dev_app


class Stopwatch:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def run_demo(fake):
    watch = Stopwatch()
    client = TestClient(create_dev_app(monotonic=watch))
    assert client.get(f"/?fake={fake}").status_code == 200
    phone = "+1 415 555 0100"
    assert client.post("/api/demo/verify/start", json={"phone": phone, "channel": "sms"}).status_code == 200
    token = client.post("/api/demo/verify/check", json={"phone": phone, "code": "123456"}).json()["token"]
    episode = client.post("/api/demo/run", json={"token": token, "scenario": "heat"}).json()["episode_id"]
    events, after = [], 0
    for _ in range(200):  # the page polls once a second
        watch.t += 1.0
        reply = client.get(f"/api/demo/timeline/{episode}", params={"token": token, "after": after}).json()
        events += reply["events"]
        after = events[-1]["seq"] if events else 0
        if reply["closed"]:
            break
    return events


def test_fake_ok_runs_to_a_happy_ending():
    events = run_demo("ok")
    assert [e["type"] for e in events][:4] == ["episode_started", "call_queued", "call_placed", "call_answered"]
    assert events[-1]["type"] == "episode_closed" and events[-1]["public"]["result"] == "ok"


def test_fake_silent_calls_the_visitor_back_as_the_neighbour():
    events = run_demo("silent")
    assert [e["type"] for e in events].count("call_missed") == 3
    assert {"role": "neighbor"} in [{"role": e["public"].get("role")} for e in events if e["type"] == "call_placed"]
    assert events[-1]["public"]["result"] == "ok_by_neighbor"


def test_fake_unwell_ends_with_an_urgent_message():
    events = run_demo("unwell")
    notices = [e["public"] for e in events if e["type"] == "family_notified"]
    assert {"key": "call_unwell", "urgent": "true"} in notices
    assert events[-1]["public"]["result"] == "needs_help"


def test_family_page_works_in_the_local_preview_from_enrolment_to_a_finished_check(monkeypatch):
    monkeypatch.setenv("KUCKUC_INVITE_CODES", "LOCAL")
    watch = Stopwatch()
    # 10:00 in Belgrade: at night the introduction calls would rightly wait for the morning.
    client = TestClient(create_dev_app(monotonic=watch, now=datetime(2026, 11, 20, 9, 0, tzinfo=UTC)))
    assert client.get("/family").status_code == 200
    assert client.post("/api/auth/google", json={"credential": "me@example.com"}).status_code == 200
    place = client.get("/api/family/places", params={"q": "selo"}).json()[0]
    body = {
        "invite_code": "LOCAL", "name": "baba Vera", "family_name": "vaš unuk", "gender": "f",
        "phone": "+15005550006", "lang": "sr", "lat": place["lat"], "lon": place["lon"], "place": place["name"],
        "neighbors": [{"name": "Dragan", "phone": "+15005550007"}],
    }  # fmt: skip
    pid = client.post("/api/family/persons", json=body).json()["id"]

    def poll_until(done, limit=120):
        for _ in range(limit):  # the page looks again every few seconds while something is under way
            watch.t += 1.0
            me = client.get("/api/family/me").json()
            if done(me):
                return me
        raise AssertionError("the local preview did not get there")

    me = poll_until(lambda me: me["persons"][0]["status"] == "active" and me["persons"][0]["neighbors"][0]["status"] == "active")
    assert client.get(f"/api/family/persons/{pid}/forecast").json()["days"][0]["hazard"] is None
    assert client.post(f"/api/family/persons/{pid}/call-now").status_code == 202

    def newest_result(_me):
        return client.get(f"/api/family/persons/{pid}/episodes").json()[0]["result"] == "ok"

    poll_until(newest_result)
