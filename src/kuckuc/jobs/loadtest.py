"""Load test: thousands of simulated people in a heatwave at the same moment.

Everything is real except the telephone: the events go through Pub/Sub, the engine decides,
Cloud Tasks hands every call to the voice service, and the voice service answers with the
prepared answer instead of dialling. Nobody's phone rings and no speech is made.

    python -m kuckuc.jobs.loadtest --n=2000
    python -m kuckuc.jobs.loadtest --peak-instances=14     # add the peak read from Cloud Monitoring

Simulated people, episodes and calls have ids that start with ``sim-``; the store gives such
records an expiry, so they delete themselves and no code here ever deletes anything.
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import time
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from kuckuc.deps import Deps
from kuckuc.models import EpisodeState, Person, PersonStatus

log = logging.getLogger(__name__)

STAT_DOC = "loadtest_latest"
OK = ["ok"]  # answers at once and is fine
SILENT = ["no_answer"] * 3 + ["yes", "ok"]  # never picks up; the neighbour goes and finds them well
UNWELL = ["needs_help", "yes", "ok"]  # says they are unwell; the neighbour goes and helps
SHARE_OK, SHARE_SILENT = 0.85, 0.10  # the remaining 5% are unwell
POLL_S = 2


def plan_population(n: int, seed: int) -> list[list[str]]:
    """For each simulated person, what will be heard on their calls, in order."""
    rng = random.Random(seed)
    people = []
    for _ in range(n):
        draw = rng.random()
        people.append(list(OK if draw < SHARE_OK else SILENT if draw < SHARE_OK + SHARE_SILENT else UNWELL))
    return people


def run_loadtest(
    deps: Deps,
    n: int,
    seed: int = 1,
    *,
    workers: int = 1,
    limit_s: int = 600,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict:
    """Create ``n`` simulated people, ask for a check on all of them at once and wait until
    every episode has closed. The summary is stored for the architecture page only when all of
    them closed; a run that hit the time limit is reported and not published."""
    population = plan_population(n, seed)
    ids = [f"sim-{uuid.uuid4().hex[:20]}" for _ in range(n)]  # random, so the writes spread over the database

    def put_person(id: str) -> None:
        deps.store.put_person(
            Person(
                id=id, owner_uid="", name="Sim", family_name="Sim", gender="f", phone="simulated", lang="en",
                lat=0.0, lon=0.0, tz="UTC", place="simulation", status=PersonStatus.ACTIVE, sim=True,
            )
        )  # fmt: skip

    def request_check(job: tuple[str, list[str]]) -> None:
        id, answers = job
        deps.bus.publish("check.requested", {"person_id": id, "kind": "sim", "episode_id": id, "sim_answers": answers})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(put_person, ids))
        started_at = deps.clock.now()
        began = monotonic()
        list(pool.map(request_check, zip(ids, population, strict=True)))
        log.info("loadtest: %d checks requested", n)

        closed = {}
        waiting = set(ids)
        while True:
            for episode in pool.map(deps.store.get_episode, sorted(waiting)):
                if episode is not None and episode.state == EpisodeState.CLOSED:
                    closed[episode.id] = episode
                    waiting.discard(episode.id)
            if not waiting or monotonic() - began >= limit_s:
                break
            log.info("loadtest: %d of %d closed", len(closed), n)
            sleep(POLL_S)

    # The clock stops when the last episode closed, not when this loop happened to notice.
    finished_at = max((e.updated_at for e in closed.values()), default=deps.clock.now())
    summary = {
        "n": n,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "seconds": max(0, math.ceil((finished_at - started_at).total_seconds())),
        "calls": sum(e.person_attempts + e.neighbor_attempts + e.followup_attempts for e in closed.values()),
        "by_result": dict(sorted(Counter(e.result.value for e in closed.values() if e.result).items())),
        "unfinished": len(waiting),
    }
    if waiting:
        log.error("loadtest: %d of %d episodes did not close within %d s; not published", len(waiting), n, limit_s)
    else:
        deps.store.put_stat_doc(STAT_DOC, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    from kuckuc.config import Settings
    from kuckuc.deps import build_cloud_deps

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, default=0, help="how many simulated people")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--peak-instances", type=int, help="add the peak instance count to the latest result")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    deps = build_cloud_deps(Settings(), "job")
    if args.peak_instances is not None:
        latest = deps.store.get_stat_doc(STAT_DOC)
        if latest is None:
            log.error("no load test result to add the peak to")
            return 1
        deps.store.put_stat_doc(STAT_DOC, latest | {"peak_instances": args.peak_instances})
        return 0
    if args.n <= 0:
        parser.error("--n must be at least 1")
    summary = run_loadtest(deps, args.n, args.seed, workers=64)
    log.info("loadtest: %s", summary)
    return 1 if summary["unfinished"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
