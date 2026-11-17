"""The two brakes on new calls: one switch that stops everything, and a daily cap per person."""

from __future__ import annotations

from datetime import UTC

from kuckuc.deps import Deps
from kuckuc.models import Person, Target


def call_block_reason(deps: Deps, person: Person, target: Target, first_try: bool = True) -> str | None:
    """Why a new call may not be queued, or None when it may.

    The cap counts calls to the person themself and is checked only before the first call of a
    check: an escalation that has started must be able to finish, so its retries and its calls to
    neighbours are never capped. Demo and simulated people have their own limits elsewhere.
    """
    if deps.store.get_runtime().kill_switch:
        return "kill_switch"
    if target == Target.PERSON and first_try and not (person.demo or person.sim):
        today = deps.clock.now().astimezone(UTC).date()
        if deps.store.count_person_calls(person.id, today) >= deps.settings.max_person_calls_per_day:
            return "daily_cap"
    return None
