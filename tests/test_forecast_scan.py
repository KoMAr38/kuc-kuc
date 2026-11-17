import json
from datetime import timedelta
from pathlib import Path

import pytest
from factories import T0, TODAY, TOMORROW, make_alert, make_person
from helpers import real_calls

from kuckuc.deps import build_memory_deps
from kuckuc.jobs.forecast_scan import run_scan
from kuckuc.models import EpisodeKind, EpisodeState, PersonStatus, Result, ScriptKind

FIXTURES = Path(__file__).parent / "fixtures"
BG = "Europe/Belgrade"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def deps():
    return build_memory_deps(now=T0)  # 10:00 in Belgrade on 2026-11-20


def add_person(deps, **over):
    n = len(deps.store.list_persons_by_owner("u1")) + 1
    person = make_person(**({"id": f"q{n}", "lat": 44.81, "lon": 20.46, "tz": BG} | over))
    deps.store.put_person(person)
    return person


def add_active_person(deps, lat=44.81, lon=20.46, tz=BG):
    return add_person(deps, lat=lat, lon=lon, tz=tz)


def pause(sys, person_id):
    person = sys.deps.store.get_person(person_id)
    sys.deps.store.put_person(person.model_copy(update={"status": PersonStatus.PAUSED}))


def episodes(sys, kind=None):
    return [e for e in sys.deps.store.list_episodes("p1", limit=100) if kind is None or e.kind == kind]


def announce(sys, hazard="cold", day=TOMORROW, value=-12.4):
    alert = make_alert(person_id="p1", hazard=hazard, day=day, value=value)
    sys.deps.store.create_alert(alert)
    sys.deps.bus.publish("alert.created", {"alert_id": alert.id})
    return alert


# --- the scan --------------------------------------------------------------------------------


def test_scan_creates_one_alert_and_is_idempotent(deps):
    person = add_active_person(deps, lat=44.81, lon=20.46, tz=BG)
    get = lambda url, params: load("openmeteo_cold.json")  # noqa: E731
    assert run_scan(deps, get) == 1 and run_scan(deps, get) == 0
    # Announced on every scan: the engine's episode ids make a repeat harmless, and a lost one comes back.
    assert [t for t, _ in deps.bus.published].count("alert.created") == 2
    alert = deps.store.get_alert(f"{person.id}:cold:{TOMORROW}")
    assert (alert.person_id, alert.hazard, alert.day, alert.value, alert.created_at) == (
        person.id,
        "cold",
        TOMORROW,
        -12.4,
        T0,
    )
    assert deps.bus.published == [("alert.created", {"alert_id": alert.id})] * 2


def test_scan_skips_paused_and_demo_and_shares_cells(deps):
    add_active_person(deps, lat=44.81, lon=20.46)
    add_active_person(deps, lat=44.79, lon=20.54)
    add_person(deps, status=PersonStatus.PAUSED)
    add_person(deps, demo=True)
    calls = []
    run_scan(deps, lambda u, p: calls.append(p) or load("openmeteo_mild.json"))
    assert len(calls) == 1
    assert (calls[0]["latitude"], calls[0]["longitude"]) == (44.8, 20.5)


def test_scan_asks_once_per_cell_and_alerts_everyone_in_it(deps):
    add_active_person(deps, lat=44.81, lon=20.46)
    add_active_person(deps, lat=44.79, lon=20.54)
    add_active_person(deps, lat=45.25, lon=19.85)
    calls = []
    created = run_scan(deps, lambda u, p: calls.append(p) or load("openmeteo_cold.json"))
    assert len(calls) == 2 and created == 3


def test_one_cell_failing_does_not_stop_the_others(deps):
    add_active_person(deps, lat=44.81, lon=20.46)
    far = add_active_person(deps, lat=45.25, lon=19.85)

    def flaky(url, params):
        if params["latitude"] == 44.8:
            raise RuntimeError("forecast service down")
        return load("openmeteo_cold.json")

    assert run_scan(deps, flaky) == 1
    assert deps.store.get_alert(f"{far.id}:cold:{TOMORROW}") is not None


def test_scan_only_looks_at_today_and_tomorrow_where_the_person_lives(deps):
    add_active_person(deps)
    payload = load("openmeteo_cold.json")
    hourly = payload["hourly"]
    for i in range(24, 48):  # make tomorrow mild again
        hourly["temperature_2m"][i] = 5.0
    for i in range(48, 72):  # and the day after tomorrow freezing
        hourly["temperature_2m"][i] = -15.0
    assert run_scan(deps, lambda u, p: payload) == 0


def test_scan_with_nobody_under_watch_asks_for_nothing(deps):
    calls = []
    assert run_scan(deps, lambda u, p: calls.append(p) or {}) == 0
    assert calls == []


# --- from alert to scheduled calls -----------------------------------------------------------


def test_alert_schedules_a_headsup_today_and_a_check_tomorrow(sys):
    announce(sys)
    planned = {e.kind: e for e in episodes(sys)}
    assert set(planned) == {EpisodeKind.HEADSUP, EpisodeKind.CHECK}
    assert planned[EpisodeKind.CHECK].id == f"p1:{TOMORROW}:check"
    assert planned[EpisodeKind.HEADSUP].id == f"p1:{TOMORROW}:headsup"
    assert all(e.state == EpisodeState.SCHEDULED and e.hazard == "cold" and e.value == -12.4 for e in planned.values())
    # T0 is 10:00 in Belgrade: the heads-up is 7 hours away, the check 24.
    assert sorted(sys.pending_task_delays()) == [7 * 3600, 24 * 3600]
    assert real_calls(sys) == []


def test_alert_tells_the_family_once_even_if_the_event_is_delivered_twice(sys):
    alert = announce(sys)
    sys.deps.bus.publish("alert.created", {"alert_id": alert.id})
    notes = [n for n in sys.deps.notifier.sent if n.key == "alert_created"]
    assert len(notes) == 1
    assert notes[0].params == {"name": "baba Milena", "neighbor": "", "hazard": "cold"} and notes[0].urgent is False
    assert len(episodes(sys)) == 2 and len(sys.deps.queue.tasks) == 2


def test_scheduled_headsup_rings_at_17_with_the_headsup_words(sys):
    announce(sys)
    sys.run_tasks(advance_s=7 * 3600 - 1)
    assert sys.deps.telephony.placed == []
    sys.run_tasks(advance_s=1)
    (call,) = real_calls(sys)
    assert call.script == ScriptKind.HEADSUP and call.params["value"] == "-12.4"


def test_second_day_of_the_same_hazard_gets_no_headsup(sys):
    sys.deps.store.create_alert(make_alert(person_id="p1", hazard="cold", day=TODAY))
    announce(sys, day=TOMORROW)
    assert [e.kind for e in episodes(sys)] == [EpisodeKind.CHECK]


def test_two_hazards_same_day_make_one_check(sys):
    for hz in ("cold", "snow"):
        sys.deps.store.create_alert(make_alert(person_id="p1", hazard=hz, day=TOMORROW))
        sys.deps.bus.publish("alert.created", {"alert_id": f"p1:{hz}:{TOMORROW}"})
    assert len([e for e in sys.deps.store.list_episodes("p1") if e.kind == EpisodeKind.CHECK]) == 1


def test_alert_found_too_late_schedules_nothing_and_says_nothing(sys):
    sys.clock.advance(11 * 3600)  # 21:00 in Belgrade
    announce(sys, day=TODAY)
    assert episodes(sys) == [] and sys.deps.queue.tasks == [] and sys.deps.notifier.sent == []


def test_alert_for_someone_paused_or_unknown_is_ignored(sys):
    pause(sys, "p1")
    announce(sys)
    sys.deps.bus.publish("alert.created", {"alert_id": "ghost:cold:2026-11-21"})
    assert episodes(sys) == [] and sys.deps.notifier.sent == []


def test_due_for_paused_person_does_not_call(sys):
    sys.deps.store.create_alert(make_alert(person_id="p1", hazard="cold", day=TOMORROW))
    sys.deps.bus.publish("alert.created", {"alert_id": f"p1:cold:{TOMORROW}"})
    pause(sys, "p1")
    before = len(sys.deps.notifier.sent)
    sys.run_tasks(advance_s=2 * 86400)
    assert sys.deps.store.all_calls() == []
    assert all(e.result == Result.NOT_DELIVERED for e in sys.deps.store.list_episodes("p1"))
    assert len(sys.deps.notifier.sent) == before


def test_whole_day_from_forecast_to_all_clear(sys):
    from kuckuc.models import Answer

    person = sys.deps.store.get_person("p1")
    sys.deps.store.put_person(person.model_copy(update={"lat": 44.81, "lon": 20.46}))
    assert run_scan(sys.deps, lambda u, p: load("openmeteo_cold.json")) == 1
    sys.deps.interpreter.answers = [Answer.OK, Answer.OK]
    sys.run_tasks(advance_s=7 * 3600)  # 17:00: the heads-up
    sys.answer_next_call()
    sys.run_tasks(advance_s=17 * 3600)  # 10:00 next day: the check
    sys.answer_next_call()
    assert [(e.kind, e.result) for e in sorted(episodes(sys), key=lambda e: e.kind)] == [
        (EpisodeKind.CHECK, Result.OK),
        (EpisodeKind.HEADSUP, Result.OK),
    ]
    assert [n.key for n in sys.deps.notifier.sent] == ["alert_created", "headsup_ok", "call_ok"]
    assert [c.script for c in real_calls(sys)] == [ScriptKind.HEADSUP, ScriptKind.CHECK]
    assert timedelta(hours=24) == sys.clock.now() - T0


def test_an_alert_whose_announcement_was_lost_is_announced_again_on_the_next_scan(deps):
    add_active_person(deps, lat=44.81, lon=20.46, tz=BG)
    get = lambda url, params: load("openmeteo_cold.json")  # noqa: E731
    publish, down = deps.bus.publish, [True]

    def flaky(type, data):
        if down.pop() if down else False:
            raise RuntimeError("Pub/Sub unavailable")
        return publish(type, data)

    deps.bus.publish = flaky
    with pytest.raises(RuntimeError):
        run_scan(deps, get)
    assert deps.bus.published == []
    run_scan(deps, get)  # the job's retry, or the next scan three hours later
    assert [t for t, _ in deps.bus.published] == ["alert.created"]


def test_scheduled_episodes_remember_when_they_are_due(sys):
    alert = make_alert(hazard="cold", day=TOMORROW)
    sys.deps.store.create_alert(alert)
    sys.deps.bus.publish("alert.created", {"alert_id": alert.id})
    due = {e.kind: e.due_at for e in sys.deps.store.list_episodes("p1")}
    assert due[EpisodeKind.HEADSUP] < due[EpisodeKind.CHECK] and all(due.values())
