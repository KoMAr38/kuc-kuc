from datetime import timedelta

import pytest

from factories import T0, TODAY, make_alert, make_call, make_episode, make_neighbor, make_person

from kuckuc.config import Settings
from kuckuc.deps import build_memory_deps
from kuckuc.models import Answer, EpisodeState, PersonStatus, Result, Target
from kuckuc.store import MemoryStore


def test_create_episode_is_idempotent(store):
    e = make_episode(id="p1:2026-11-20:check")
    assert store.create_episode(e) is True
    assert store.create_episode(e) is False


def test_alert_is_created_once_and_can_be_read_back(store):
    alert = make_alert(person_id="p1", hazard="cold", day=TODAY, value=-12.4)
    assert store.get_alert(alert.id) is None
    assert store.create_alert(alert) is True
    assert store.create_alert(alert.model_copy(update={"value": -20.0})) is False
    assert store.get_alert(alert.id) == alert


def test_complete_call_only_first_time(store):
    store.create_call(make_call(id="c1"))
    assert store.complete_call("c1", Answer.OK, "dobro sam", None) is True
    assert store.complete_call("c1", Answer.NEEDS_HELP, None, None) is False
    assert store.get_call("c1").answer == Answer.OK
    assert store.get_call("c1").status == "completed"


def test_timeline_after_seq(store):
    for t in ("a", "b", "c"):
        store.add_timeline("e1", t, {})
    assert [e.type for e in store.list_timeline("e1", after_seq=1)] == ["b", "c"]


def test_count_person_calls_ignores_neighbour_calls(store):
    store.create_episode(make_episode(id="e1", person_id="p1"))
    store.create_call(make_call(id="c1", target=Target.PERSON, episode_id="e1"))
    store.create_call(make_call(id="c2", target=Target.NEIGHBOR, episode_id="e1"))
    assert store.count_person_calls("p1", TODAY) == 1


def test_count_person_calls_ignores_other_people_other_days_and_blocked_calls(store):
    store.create_episode(make_episode(id="e1", person_id="p1"))
    store.create_episode(make_episode(id="e2", person_id="p2"))
    store.create_call(make_call(id="c1", episode_id="e1"))
    store.create_call(make_call(id="c2", episode_id="e2"))
    store.create_call(make_call(id="c3", episode_id="e1", created_at=T0 - timedelta(days=1)))
    store.create_call(make_call(id="c4", episode_id="e1", status="blocked"))
    assert store.count_person_calls("p1", TODAY) == 1


def test_update_episode_abort_keeps_original(store):
    store.create_episode(make_episode(id="e1"))
    assert store.update_episode("e1", lambda e: None) is None
    assert store.get_episode("e1").state == EpisodeState.SCHEDULED


def test_update_episode_stores_the_returned_copy(store):
    store.create_episode(make_episode(id="e1"))
    out = store.update_episode("e1", lambda e: e.model_copy(update={"state": EpisodeState.CLOSED}))
    assert out.state == EpisodeState.CLOSED
    assert store.get_episode("e1").state == EpisodeState.CLOSED


def test_stored_objects_are_not_shared_with_the_caller(store):
    p = make_person()
    store.put_person(p)
    p.name = "changed after put"
    assert store.get_person("p1").name == "baba Milena"


def test_list_active_persons_is_only_real_people_under_watch(store):
    store.put_person(make_person(id="p1"))
    store.put_person(make_person(id="p2", status=PersonStatus.PAUSED))
    store.put_person(make_person(id="p3", status=PersonStatus.PENDING_CONSENT))
    store.put_person(make_person(id="p4", demo=True))
    store.put_person(make_person(id="p5", sim=True))
    assert [p.id for p in store.list_active_persons()] == ["p1"]


def test_stats_count_calls_on_the_day_they_rang_as_the_real_store_does():
    clock = {"t": T0 - timedelta(days=3)}
    s = MemoryStore(now=lambda: clock["t"])
    s.put_person(make_person(id="p1"))
    s.put_person(make_person(id="p4", demo=True))

    def ring(id, **over):
        s.create_call(make_call(id=id, **over))
        s.start_dialing(id, clock["t"])
        assert s.mark_placed(id, "CA" + id)

    ring("three-days-ago")
    clock["t"] = T0
    ring("queued-yesterday", created_at=T0 - timedelta(days=1))  # queued before midnight, rang today
    s.create_call(make_call(id="never-rang"))
    s.complete_call("never-rang", Answer.NO_ANSWER, None, None)  # the network failed before it rang
    s.create_call(make_call(id="sim", provider="simulated", status="completed"))
    assert s.stats() == {"calls_today": 1, "calls_total": 2, "persons_active": 1}


def test_timeline_events_are_stamped_and_numbered_from_one():
    s = MemoryStore(now=lambda: T0)
    s.add_timeline("e1", "episode_started", {"kind": "check"})
    (ev,) = s.list_timeline("e1")
    assert (ev.seq, ev.at, ev.type, ev.public) == (1, T0, "episode_started", {"kind": "check"})


def test_neighbors_come_back_in_order(store):
    store.put_neighbor(make_neighbor(id="n2", order=1))
    store.put_neighbor(make_neighbor(id="n1", order=0))
    store.put_neighbor(make_neighbor(id="nx", person_id="other", order=0))
    assert [n.id for n in store.list_neighbors("p1")] == ["n1", "n2"]


def test_list_episodes_newest_first(store):
    store.create_episode(make_episode(id="old", created_at=T0 - timedelta(hours=2)))
    store.create_episode(make_episode(id="new", created_at=T0))
    assert [e.id for e in store.list_episodes("p1")] == ["new", "old"]


def test_demo_spend_accumulates_per_day(store):
    assert store.add_demo_spend(TODAY, 1.5) == 1.5
    assert store.add_demo_spend(TODAY, 0.25) == 1.75
    assert store.get_demo_spend(TODAY) == 1.75
    assert store.get_demo_spend(TODAY + timedelta(days=1)) == 0.0


def test_settings_defaults_match_the_safety_limits():
    s = Settings(_env_file=None)
    assert (s.max_person_calls_per_day, s.quiet_start_hour, s.quiet_end_hour, s.intro_end_hour) == (6, 8, 20, 23)
    assert (s.heat_apparent_c, s.cold_temp_c, s.wind_gust_kmh, s.snow_cm) == (35.0, -10.0, 80.0, 20.0)
    assert s.demo_countries == ("US", "CA", "GB", "NO")


def test_credentials_pasted_with_a_trailing_newline_still_work():
    s = Settings(_env_file=None, twilio_auth_token="  abc123\r\n", twilio_account_sid="ACx\n")
    assert (s.twilio_auth_token, s.twilio_account_sid) == ("abc123", "ACx")


def test_memory_deps_clock_is_fixed_and_utc():
    deps = build_memory_deps(now=T0)
    assert deps.clock.now() == T0
    assert deps.bus.publish("x.y", {"a": 1}) and deps.bus.published == [("x.y", {"a": 1})]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('["forecast","family"]', {"forecast", "family"}),
        ("[forecast]", {"forecast"}),  # what a shell leaves after eating the quotes
        ("forecast, family", {"forecast", "family"}),
        ("forecast", {"forecast"}),
        ("[]", set()),
        ("", set()),
    ],
)
def test_feature_list_is_read_however_it_was_written(monkeypatch, raw, expected):
    # A service must not refuse to start because a list in its environment lost its quotes.
    monkeypatch.setenv("KUCKUC_FEATURES", raw)
    assert Settings(_env_file=None).features == expected


def test_invite_codes_are_read_from_a_plain_list(monkeypatch):
    monkeypatch.setenv("KUCKUC_INVITE_CODES", "alfa-1, beta-2")
    assert Settings(_env_file=None).invite_codes == ("alfa-1", "beta-2")


def test_calls_of_an_episode_come_back_oldest_first(store):
    store.create_episode(make_episode(id="e1"))
    store.create_call(make_call(id="late", episode_id="e1", created_at=T0 + timedelta(minutes=10)))
    store.create_call(make_call(id="early", episode_id="e1", created_at=T0))
    store.create_call(make_call(id="other", episode_id="e2"))
    assert [c.id for c in store.list_calls_for_episode("e1")] == ["early", "late"]
    assert store.list_calls_for_episode("none") == []


def test_episodes_created_in_the_same_instant_still_come_newest_first():
    s = MemoryStore()
    s.create_episode(make_episode(id="first"))
    s.create_episode(make_episode(id="second"))
    assert [e.id for e in s.list_episodes("p1")] == ["second", "first"]


def test_open_episodes_are_listed_and_closed_ones_are_not(store):
    store.create_episode(make_episode(id="open1", state=EpisodeState.CALLING_PERSON))
    store.create_episode(make_episode(id="open2", state=EpisodeState.SCHEDULED, due_at=T0))
    store.create_episode(make_episode(id="done", state=EpisodeState.CLOSED, result=Result.OK))
    found = {e.id: e for e in store.list_open_episodes()}
    assert sorted(found) == ["open1", "open2"] and found["open2"].due_at == T0


def test_each_recording_of_a_call_is_claimed_once(store):
    # Twilio can deliver the same recording twice, to two servers; only the first may handle it.
    assert store.claim_recording("c1", 1) is True
    assert store.claim_recording("c1", 1) is False
    assert store.claim_recording("c1", 2) is True and store.claim_recording("c2", 1) is True


def test_a_call_can_be_claimed_for_dialling_once(store):
    store.create_call(make_call(id="c1"))
    assert store.start_dialing("c1", T0) is True
    assert store.start_dialing("c1", T0) is False
    assert (store.get_call("c1").status, store.get_call("c1").placed_at) == ("dialing", T0)
    assert store.start_dialing("missing", T0) is False


def test_a_demo_reservation_counts_every_quota_and_the_money_all_or_nothing(store):
    quotas = [("run:ph", 2, "phone_limit"), ("run-ip:ip", 6, "ip_limit")]
    assert store.reserve_demo(TODAY, quotas, 1.0, 5.0) is None
    assert store.reserve_demo(TODAY, quotas, 1.0, 5.0) is None
    assert store.reserve_demo(TODAY, quotas, 1.0, 5.0) == "phone_limit"  # a third run on one number
    assert store.demo_count(TODAY, "run:ph") == 2 and store.demo_count(TODAY, "run-ip:ip") == 2
    assert store.get_demo_spend(TODAY) == 2.0
    assert store.reserve_demo(TODAY, [("run:other", 2, "phone_limit")], 3.5, 5.0) == "budget"  # 2 + 3.5 > 5
    assert store.demo_count(TODAY, "run:other") == 0 and store.get_demo_spend(TODAY) == 2.0
    assert store.demo_count(TODAY + timedelta(days=1), "run:ph") == 0


def test_a_call_is_marked_placed_only_from_dialling(store):
    store.create_call(make_call(id="c1"))
    assert store.mark_placed("c1", "CA1") is False  # still queued
    store.start_dialing("c1", T0)
    assert store.mark_placed("c1", "CA1") is True
    assert (store.get_call("c1").status, store.get_call("c1").provider_sid) == ("placed", "CA1")
    store.complete_call("c1", Answer.OK, None, None)
    assert store.mark_placed("c1", "CA1") is False and store.get_call("c1").status == "completed"



def test_a_call_counts_on_the_day_where_the_person_lives(store):
    # 23:30 UTC on the 20th is already the 21st in Belgrade: the daily cap counts it on the 21st.
    from datetime import UTC, date, datetime

    store.create_episode(make_episode(id="e1", person_id="p1"))
    late = datetime(2026, 11, 20, 23, 30, tzinfo=UTC)
    store.create_call(make_call(id="c1", episode_id="e1", created_at=late, local_day="2026-11-21"))
    assert store.count_person_calls("p1", date(2026, 11, 21)) == 1
    assert store.count_person_calls("p1", date(2026, 11, 20)) == 0


def test_parallel_reservations_never_pass_a_limit(store):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: store.reserve_demo(TODAY, [("run:ph", 2, "phone_limit")], 0.1, 100.0), range(6)))
    # Never more than the limit, never an error; the counter and the money agree with what passed.
    # (On Firestore, requests that collided too often are told "busy" rather than failing.)
    assert set(results) <= {None, "phone_limit", "busy"} and 1 <= results.count(None) <= 2
    assert store.demo_count(TODAY, "run:ph") == results.count(None)
    assert store.get_demo_spend(TODAY) == round(0.1 * results.count(None), 4)


def test_parallel_claims_dial_a_call_once(store):
    from concurrent.futures import ThreadPoolExecutor

    store.create_call(make_call(id="c1"))
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: store.start_dialing("c1", T0), range(6)))
    assert results.count(True) == 1



def test_a_call_is_created_once(store):
    assert store.create_call(make_call(id="c1")) is True
    assert store.create_call(make_call(id="c1", to="+15005550009")) is False
    assert store.get_call("c1").to != "+15005550009"
