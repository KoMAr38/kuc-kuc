"""What keeps the public demo from becoming someone else's problem or bill.

A visitor may only ring a number they have proved is theirs, in a short list of countries,
a few times a day, and only while the day's money and the account balance allow it. These
limits apply to the demo alone; they never stand in the way of a call to a real person.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Literal

import phonenumbers
from phonenumbers import PhoneNumberType

from kuckuc.config import Settings
from kuckuc.ports import Clock
from kuckuc.store import Store
from kuckuc.telephony.base import Verify

log = logging.getLogger(__name__)

DEFAULT_COUNTRIES = ("US", "CA", "GB", "NO")
# Ordinary home and mobile lines only: never premium-rate, toll-free, short or special numbers.
_ORDINARY = frozenset(
    {PhoneNumberType.FIXED_LINE, PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE}
)
DEMO_CALL_MINUTES = 2
BALANCE_CACHE_S = 300


class DemoRefused(Exception):
    """``code`` is one of: bad_number, country, number_type, own_number, phone_limit, ip_limit,
    budget, ended, low_balance."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalise_phone(
    raw: str | None, default_region: str = "US", countries: tuple[str, ...] = DEFAULT_COUNTRIES
) -> tuple[str, str]:
    """``(E.164 number, ISO country)`` for a number the demo may call; raises DemoRefused otherwise."""
    try:
        number = phonenumbers.parse(raw or "", default_region)
    except phonenumbers.NumberParseException:
        raise DemoRefused("bad_number") from None
    if not phonenumbers.is_valid_number(number):
        raise DemoRefused("bad_number")
    country = phonenumbers.region_code_for_number(number)
    if country not in countries:
        raise DemoRefused("country")
    if phonenumbers.number_type(number) not in _ORDINARY:
        raise DemoRefused("number_type")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164), country


def _per_minute_usd(country: str, settings: Settings) -> float:
    """A country with no listed price is counted at the dearest listed one."""
    prices = settings.demo_price_per_min_usd
    return prices.get(country, max(prices.values(), default=0.0))


def verify_cost_usd(country: str, channel: Literal["sms", "call"], settings: Settings) -> float:
    """What sending one code is counted as: the verification, plus a minute when the code is spoken."""
    return settings.verify_fee_usd + (_per_minute_usd(country, settings) if channel == "call" else 0.0)


def call_cost_usd(country: str, settings: Settings) -> float:
    """What the calls of one demo run are counted as: two minutes."""
    return DEMO_CALL_MINUTES * _per_minute_usd(country, settings)


def run_cost_usd(country: str, settings: Settings) -> float:
    """A whole demo run, code included: what must still fit in the day's budget before one starts."""
    return settings.verify_fee_usd + call_cost_usd(country, settings)


def daily_cap_usd(day: date, settings: Settings) -> float:
    return settings.demo_cap_launch_usd if day == settings.demo_launch_day else settings.demo_cap_usd


def check_can_start(
    store: Store, verify: Verify, settings: Settings, phone_hash: str, ip_hash: str, country: str, today: date
) -> None:
    """Returns quietly when a demo run may start; raises DemoRefused with the reason when not."""
    if today > settings.demo_end_day:
        raise DemoRefused("ended")
    if store.count_demo_runs(phone_hash, today) >= settings.demo_runs_per_phone:
        raise DemoRefused("phone_limit")
    if store.count_ip_runs(ip_hash, today) >= settings.demo_runs_per_ip:
        raise DemoRefused("ip_limit")
    if store.get_demo_spend(today) + run_cost_usd(country, settings) > daily_cap_usd(today, settings):
        raise DemoRefused("budget")
    try:
        balance = verify.balance_usd()
    except Exception:
        # If the balance cannot be read, assume the worst: the money is kept for real calls.
        log.warning("balance unavailable; demo refused")
        raise DemoRefused("low_balance") from None
    if balance < settings.demo_min_balance_usd:
        raise DemoRefused("low_balance")


class CachedBalance:
    """A Verify that asks the provider for the balance at most once every five minutes."""

    def __init__(self, inner: Verify, clock: Clock) -> None:
        self._inner = inner
        self._clock = clock
        self._balance: float | None = None
        self._read_at: datetime | None = None

    def start(self, phone: str, channel: Literal["sms", "call"]) -> None:
        self._inner.start(phone, channel)

    def check(self, phone: str, code: str) -> bool:
        return self._inner.check(phone, code)

    def balance_usd(self) -> float:
        now = self._clock.now()
        if self._balance is None or now - self._read_at >= timedelta(seconds=BALANCE_CACHE_S):
            self._balance = self._inner.balance_usd()
            self._read_at = now
        return self._balance


def hash_value(value: str, salt: str) -> str:
    """How phone numbers and addresses are remembered for the limits: as a salted hash, never as themselves."""
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()
