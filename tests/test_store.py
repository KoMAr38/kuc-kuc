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
    assert store.alert_exists(alert.id) is False and store.get_alert(alert.id) is None
    assert store.create_alert(alert) is True
    assert store.create_alert(alert.model_copy(update={"value": -20.0})) is False
    assert store.alert_exists(alert.id) is True
    assert store.get_alert(alert.id) == alert


def test_complete_call_only_first_time(store):
    store.put_call(make_call(id="c1"))
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
    store.put_call(make_call(id="c1", target=Target.PERSON, episode_id="e1"))
    store.put_call(make_call(id="c2", target=Target.NEIGHBOR, episode_id="e1"))
    assert store.count_person_calls("p1", TODAY) == 1


def test_count_person_calls_ignores_other_people_other_days_and_blocked_calls(store):
    store.create_episode(make_episode(id="e1", person_id="p1"))
    store.create_episode(make_episode(id="e2", person_id="p2"))
    store.put_call(make_call(id="c1", episode_id="e1"))
    store.put_call(make_call(id="c2", episode_id="e2"))
    store.put_call(make_call(id="c3", episode_id="e1", created_at=T0 - timedelta(days=1)))
    store.put_call(make_call(id="c4", episode_id="e1", status="blocked"))
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


def test_stats_count_real_calls_and_real_people():
    s = MemoryStore(now=lambda: T0)
    s.put_person(make_person(id="p1"))
    s.put_person(make_person(id="p4", demo=True))
    s.put_call(make_call(id="c1", status="completed"))
    s.put_call(make_call(id="c2", status="placed", created_at=T0 - timedelta(days=3)))
    s.put_call(make_call(id="c3", status="blocked"))
    s.put_call(make_call(id="c4", status="completed", provider="simulated"))
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


def test_demo_runs_counted_per_phone_and_per_ip(store):
    store.record_demo_run("ph1", "ip1", TODAY)
    store.record_demo_run("ph2", "ip1", TODAY)
    assert store.count_demo_runs("ph1", TODAY) == 1
    assert store.count_ip_runs("ip1", TODAY) == 2
    assert store.count_demo_runs("ph1", TODAY + timedelta(days=1)) == 0


def test_settings_defaults_match_the_safety_limits():
    s = Settings(_env_file=None)
    assert (s.max_person_calls_per_day, s.quiet_start_hour, s.quiet_end_hour) == (6, 8, 20)
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
    store.put_call(make_call(id="late", episode_id="e1", created_at=T0 + timedelta(minutes=10)))
    store.put_call(make_call(id="early", episode_id="e1", created_at=T0))
    store.put_call(make_call(id="other", episode_id="e2"))
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
