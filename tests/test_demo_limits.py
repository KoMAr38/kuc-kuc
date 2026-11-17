from datetime import date

import pytest
from factories import T0

from kuckuc.ports import FixedClock
from kuckuc.telephony.fake import FakeVerify
from kuckuc.web.demo_limits import (
    CachedBalance,
    DemoRefused,
    check_can_start,
    daily_cap_usd,
    hash_value,
    normalise_phone,
    run_cost_usd,
)

D = date(2026, 11, 21)  # an ordinary demo day: after launch day, before the end
COUNTRIES = ("US", "CA", "GB", "NO")


def record_run(store, phone_hash, ip_hash, day):
    """A run that already happened that day."""
    assert store.reserve_demo(day, [(f"run:{phone_hash}", 99, "x"), (f"run-ip:{ip_hash}", 99, "x")], 0.0, 1e9) is None


@pytest.fixture
def verify():
    return FakeVerify()


# --- phone numbers ---------------------------------------------------------------------------


def test_normalises_national_format():
    assert normalise_phone("(415) 555-0100", countries=COUNTRIES) == ("+14155550100", "US")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+1 415-555-0100", ("+14155550100", "US")),
        ("  +14155550100 ", ("+14155550100", "US")),
        ("+1 604 555 0100", ("+16045550100", "CA")),
        ("+44 20 7946 0958", ("+442079460958", "GB")),
        ("+44 7400 123456", ("+447400123456", "GB")),
    ],
)
def test_accepted_numbers(raw, expected):
    assert normalise_phone(raw, countries=COUNTRIES) == expected


@pytest.mark.parametrize(
    "raw,code",
    [
        ("+381111234567", "country"),  # Serbia: not a demo country
        ("+18765550100", "country"),  # Jamaica shares +1 with the US but is not the US
        ("+19005550100", "number_type"),  # premium rate
        ("+18005550100", "number_type"),  # toll-free
        ("12345", "bad_number"),
        ("", "bad_number"),
        ("call me maybe", "bad_number"),
        ("+1415555", "bad_number"),
        (None, "bad_number"),
    ],
)
def test_refusals(raw, code):
    with pytest.raises(DemoRefused) as e:
        normalise_phone(raw, countries=COUNTRIES)
    assert e.value.code == code


def test_norway_allowed():
    assert normalise_phone("+47 412 34 567", countries=COUNTRIES)[1] == "NO"


def test_country_list_comes_from_the_caller():
    with pytest.raises(DemoRefused) as e:
        normalise_phone("+47 412 34 567", countries=("US",))
    assert e.value.code == "country"


# --- money -----------------------------------------------------------------------------------


def test_caps_by_day(settings):
    assert daily_cap_usd(date(2026, 11, 20), settings) == 15.0 and daily_cap_usd(date(2026, 11, 21), settings) == 5.0


def test_run_cost_is_the_verification_plus_four_minutes(settings):
    settings.verify_fee_usd = 0.05
    settings.demo_price_per_min_usd = {"US": 0.02, "NO": 0.10}
    assert run_cost_usd("US", settings) == pytest.approx(0.13)
    assert run_cost_usd("NO", settings) == pytest.approx(0.45)


def test_unpriced_country_costs_as_much_as_the_dearest_one(settings):
    settings.verify_fee_usd = 0.05
    settings.demo_price_per_min_usd = {"US": 0.02, "NO": 0.10}
    assert run_cost_usd("GB", settings) == pytest.approx(0.45)


# --- may this run start? ---------------------------------------------------------------------


def test_fresh_visitor_may_start(store, verify, settings):
    check_can_start(store, verify, settings, "ph", "ip", "US", D)


def test_refused_after_end_day(store, verify, settings):
    with pytest.raises(DemoRefused) as e:
        check_can_start(store, verify, settings, "ph", "ip", "US", date(2026, 12, 5))
    assert e.value.code == "ended"
    check_can_start(store, verify, settings, "ph", "ip", "US", date(2026, 12, 4))  # the last day still works


def test_third_run_same_number_refused(store, verify, settings):
    for _ in range(2):
        record_run(store, "ph", "ip", D)
    with pytest.raises(DemoRefused) as e:
        check_can_start(store, verify, settings, "ph", "ip2", "US", D)
    assert e.value.code == "phone_limit"
    check_can_start(store, verify, settings, "ph", "ip2", "US", date(2026, 11, 22))  # a new day starts fresh


def test_seventh_run_from_one_address_refused(store, verify, settings):
    for i in range(6):
        record_run(store, f"ph{i}", "ip", D)
    with pytest.raises(DemoRefused) as e:
        check_can_start(store, verify, settings, "new", "ip", "US", D)
    assert e.value.code == "ip_limit"


def test_budget_refuses_when_next_run_would_exceed_cap(store, verify, settings):
    store.add_demo_spend(D, 4.99)
    with pytest.raises(DemoRefused) as e:
        check_can_start(store, verify, settings, "ph", "ip", "US", D)
    assert e.value.code == "budget"


def test_launch_day_has_the_larger_budget(store, verify, settings):
    launch = date(2026, 11, 20)
    store.add_demo_spend(launch, 4.99)
    check_can_start(store, verify, settings, "ph", "ip", "US", launch)
    store.add_demo_spend(launch, 10.0)
    with pytest.raises(DemoRefused) as e:
        check_can_start(store, verify, settings, "ph", "ip", "US", launch)
    assert e.value.code == "budget"


def test_low_balance_turns_demo_off(store, settings):
    with pytest.raises(DemoRefused) as e:
        check_can_start(store, FakeVerify(balance=9.99), settings, "ph", "ip", "US", D)
    assert e.value.code == "low_balance"
    check_can_start(store, FakeVerify(balance=10.0), settings, "ph", "ip", "US", D)


def test_unreadable_balance_turns_demo_off(store, settings):
    class Broken(FakeVerify):
        def balance_usd(self):
            raise RuntimeError("provider down")

    with pytest.raises(DemoRefused) as e:
        check_can_start(store, Broken(), settings, "ph", "ip", "US", D)
    assert e.value.code == "low_balance"


# --- helpers ---------------------------------------------------------------------------------


def test_balance_is_asked_for_once_per_five_minutes():
    asked = []

    class Counting(FakeVerify):
        def balance_usd(self):
            asked.append(1)
            return 42.0

    clock = FixedClock(T0)
    cached = CachedBalance(Counting(), clock)
    assert cached.balance_usd() == 42.0 and cached.balance_usd() == 42.0
    assert len(asked) == 1
    clock.advance(299)
    cached.balance_usd()
    assert len(asked) == 1
    clock.advance(1)
    cached.balance_usd()
    assert len(asked) == 2


def test_cached_balance_passes_verification_through():
    inner = FakeVerify()
    cached = CachedBalance(inner, FixedClock(T0))
    cached.start("+14155550100", "sms")
    assert inner.started == [("+14155550100", "sms")]
    assert cached.check("+14155550100", "123456") is True and cached.check("+14155550100", "000000") is False


def test_hash_depends_on_value_and_salt_and_hides_the_value():
    h = hash_value("+14155550100", "salt-a")
    assert h == hash_value("+14155550100", "salt-a")
    assert h != hash_value("+14155550100", "salt-b") and h != hash_value("+14155550101", "salt-a")
    assert "4155550100" not in h and len(h) == 64


def test_a_demo_run_is_counted_as_four_minutes_of_calls(settings):
    # The person, the neighbour, and up to two "how is she?" calls all ring the visitor's own phone.
    from kuckuc.web.demo_limits import call_cost_usd

    assert call_cost_usd("GB", settings) == pytest.approx(4 * 0.473)


def test_addresses_in_one_ipv6_network_count_as_one_address():
    # A machine with an IPv6 network can use a new address for every request.
    from kuckuc.web.demo_limits import address_key

    assert address_key("2001:db8:1:2::1") == address_key("2001:db8:1:2:ffff::9") != address_key("2001:db8:1:3::1")
    assert address_key("203.0.113.7") == "203.0.113.7" and address_key("") == ""


def test_the_demo_day_follows_california():
    from datetime import UTC, datetime

    from kuckuc.web.demo_limits import demo_day

    assert demo_day(datetime(2026, 11, 21, 3, 0, tzinfo=UTC)) == date(2026, 11, 20)
    assert demo_day(datetime(2026, 11, 20, 9, 0, tzinfo=UTC)) == date(2026, 11, 20)



def test_a_failed_balance_read_is_remembered_for_a_minute():
    # Asking again on every request made each one wait up to 10 s while the provider was down.
    from datetime import timedelta

    from kuckuc.web.demo_limits import CachedBalance

    asked = []

    class Down:
        def balance_usd(self):
            asked.append(1)
            raise RuntimeError("provider down")

    clock = FixedClock(T0)
    balance = CachedBalance(Down(), clock)
    assert balance.balance_usd() == 0.0 and balance.balance_usd() == 0.0 and len(asked) == 1
    clock.advance(61)
    balance.balance_usd()
    assert len(asked) == 2
