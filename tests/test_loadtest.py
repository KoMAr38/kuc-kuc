"""The load test: thousands of simulated people, the real engine and queues, no telephony."""

from kuckuc.deps import build_memory_deps
from kuckuc.jobs.loadtest import plan_population, run_loadtest

SILENT = ["no_answer"] * 3 + ["yes", "ok"]
UNWELL = ["needs_help", "yes", "ok"]


def test_population_mix_is_deterministic():
    pop = plan_population(1000, seed=1)
    assert pop == plan_population(1000, seed=1)
    assert 820 <= sum(1 for a in pop if a == ["ok"]) <= 880


def test_population_has_people_who_stay_silent_and_people_who_are_unwell():
    pop = plan_population(1000, seed=1)
    assert len(pop) == 1000 and all(a in (["ok"], SILENT, UNWELL) for a in pop)
    assert 70 <= sum(1 for a in pop if a == SILENT) <= 130
    assert 30 <= sum(1 for a in pop if a == UNWELL) <= 70
    assert pop != plan_population(1000, seed=2)


def test_small_run_closes_every_episode_in_memory(sys):
    out = run_loadtest(sys.deps_with_auto_tasks(), n=50)
    assert out["n"] == 50 and sum(out["by_result"].values()) == 50 and out["calls"] >= 50
    assert sys.deps.store.get_stat_doc("loadtest_latest")["n"] == 50
    assert [p.id for p in sys.deps.store.list_active_persons()] == ["p1"]  # only the real person is under watch


def test_the_numbers_come_from_what_really_happened(sys):
    pop = plan_population(50, seed=1)
    out = run_loadtest(sys.deps_with_auto_tasks(), n=50)
    assert out["calls"] == sum(len(answers) for answers in pop)  # one call per prepared answer
    assert out["calls"] == len([c for c in sys.deps.store.all_calls() if c.provider == "simulated"])
    assert out["by_result"] == {"ok": pop.count(["ok"]), "ok_by_neighbor": 50 - pop.count(["ok"])}
    assert out["unfinished"] == 0 and out["seconds"] >= 0
    assert sys.deps.telephony.placed == []  # nobody's phone rang
    assert sys.deps.store.get_stat_doc("loadtest_latest") == out


def test_simulated_people_stay_out_of_the_public_numbers_and_carry_ids_that_expire(sys):
    before = sys.deps.store.stats()
    run_loadtest(sys.deps_with_auto_tasks(), n=20)
    assert sys.deps.store.stats() == before
    calls = [c for c in sys.deps.store.all_calls() if c.provider == "simulated"]
    assert calls and all(c.episode_id.startswith("sim-") for c in calls)  # the store deletes "sim-" data by itself


def test_a_run_that_does_not_finish_in_time_reports_it_and_is_not_published():
    deps = build_memory_deps()  # no engine is listening, so nothing ever closes
    clock = {"t": 0.0}

    def sleep(seconds):
        clock["t"] += seconds

    out = run_loadtest(deps, n=5, limit_s=600, sleep=sleep, monotonic=lambda: clock["t"])
    assert out["unfinished"] == 5 and sum(out["by_result"].values()) == 0
    assert 600 <= clock["t"] < 700
    assert deps.store.get_stat_doc("loadtest_latest") is None
