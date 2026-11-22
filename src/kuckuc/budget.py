"""Call budgets: what a check may cost at worst, what it did cost, and which caps it counts against.

Every call ends at 60 seconds, and providers bill by the started minute, so a call that connects costs
one minute and one that only rings costs nothing. A check reserves its worst case before its first call
(so an escalation that has started can always finish) and is settled at what it spent once it closes.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import phonenumbers
from phonenumbers import PhoneNumberType

from kuckuc.engine.machine import (
    FOLLOWUP_MAX_ATTEMPTS,
    HEADSUP_MAX_ATTEMPTS,
    NEIGHBOR_MAX_ATTEMPTS,
    PERSON_MAX_ATTEMPTS,
)
from kuckuc.models import Answer, CallRecord, EpisodeKind, Payer


def line_price_usd(phone: str, prices: dict[str, float]) -> float:
    """A minute to this number: by country and line type, else by country, else the dearest price."""
    dearest = max(prices.values(), default=0.0)
    try:
        number = phonenumbers.parse(phone)
    except phonenumbers.NumberParseException:
        return dearest
    country = phonenumbers.region_code_for_number(number) or ""
    line = "fixed" if phonenumbers.number_type(number) == PhoneNumberType.FIXED_LINE else "mobile"
    return prices.get(f"{country}:{line}", prices.get(country, dearest))


def worst_case_usd(kind: EpisodeKind, person_phone: str, neighbor_phones: list[str], prices: dict[str, float]) -> float:
    """Every call the check could still make, a minute each. ``neighbor_phones``: the neighbours it may
    call, in order (for a neighbour's introduction, that one neighbour)."""
    person = line_price_usd(person_phone, prices)
    neighbors = [line_price_usd(phone, prices) for phone in neighbor_phones]
    if kind == EpisodeKind.INTRO:
        return PERSON_MAX_ATTEMPTS * person
    if kind == EpisodeKind.INTRO_NEIGHBOR:
        return PERSON_MAX_ATTEMPTS * max(neighbors, default=0.0)
    tries = HEADSUP_MAX_ATTEMPTS if kind == EpisodeKind.HEADSUP else PERSON_MAX_ATTEMPTS
    asks = NEIGHBOR_MAX_ATTEMPTS * sum(neighbors)
    followups = FOLLOWUP_MAX_ATTEMPTS * max(neighbors, default=0.0)
    return tries * person + asks + followups


def spent_usd(calls: list[CallRecord], prices: dict[str, float]) -> float:
    """A minute for every call that connected (a person, a voicemail or a machine answered), for a call
    that was placed but never reported how it ended, and for one still queued (it may ring after its check
    was settled); nothing for one that only rang out or was blocked."""
    total = 0.0
    for call in calls:
        connected = call.status == "completed" and call.answer not in (None, Answer.NO_ANSWER)
        if connected or call.status in ("queued", "dialing", "placed"):
            total += line_price_usd(call.to, prices)
    return round(total, 4)


def off_season(payer: Payer, now: datetime) -> bool:
    """A seasonal payer outside its season: it does not pay for calls now."""
    if payer.season_start is None or payer.season_end is None:
        return False
    today = now.astimezone(ZoneInfo(payer.tz)).date()
    return not (payer.season_start <= today <= payer.season_end)


def budget_caps(payer: Payer, now: datetime) -> list[tuple[str, float]]:
    """``(budget key, cap)`` for every cap a check started now counts against. Empty means no money."""
    today = now.astimezone(ZoneInfo(payer.tz)).date()
    seasonal = payer.season_start is not None and payer.season_end is not None
    if seasonal and not (payer.season_start <= today <= payer.season_end):
        return []  # a seasonal payer pays for calls inside its season only
    caps = []
    if payer.month_cap_usd is not None:
        caps.append((f"{payer.id}:month:{today:%Y-%m}", payer.month_cap_usd))
    if seasonal and payer.season_cap_usd is not None:
        caps.append((f"{payer.id}:season:{payer.season_start.isoformat()}", payer.season_cap_usd))
    return caps
