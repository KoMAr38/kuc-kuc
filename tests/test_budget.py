from datetime import UTC, date, datetime

import phonenumbers
import pytest
from factories import T0, make_call
from phonenumbers import PhoneNumberType

from kuckuc.budget import budget_caps, line_price_usd, spent_usd, worst_case_usd
from kuckuc.config import Settings
from kuckuc.models import Answer, EpisodeKind, Payer

PRICES = Settings(_env_file=None).call_price_per_min_usd


def example(region: str, kind: PhoneNumberType) -> str:
    """libphonenumber's own example number for a region and line type: never a real person's line."""
    number = phonenumbers.example_number_for_type(region, kind)
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


RS_FIXED = example("RS", PhoneNumberType.FIXED_LINE)
RS_MOBILE = example("RS", PhoneNumberType.MOBILE)
US = example("US", PhoneNumberType.FIXED_LINE_OR_MOBILE)


def test_a_minute_costs_what_the_country_and_line_type_cost():
    assert line_price_usd(RS_FIXED, PRICES) == 0.597
    assert line_price_usd(RS_MOBILE, PRICES) == 0.821
    assert line_price_usd(US, PRICES) == 0.014


def test_a_number_missing_from_the_price_list_counts_at_the_dearest_price():
    assert line_price_usd(example("JP", PhoneNumberType.MOBILE), PRICES) == max(PRICES.values())
    assert line_price_usd("not a number", PRICES) == max(PRICES.values())


def test_the_worst_case_of_a_check_is_every_call_its_escalation_could_make():
    # Three tries to her, two asks to each neighbour, two follow-ups at the dearest neighbour: one minute each.
    two_neighbours = [RS_MOBILE, RS_MOBILE]
    assert worst_case_usd(EpisodeKind.CHECK, RS_FIXED, two_neighbours, PRICES) == pytest.approx(
        3 * 0.597 + 2 * 2 * 0.821 + 2 * 0.821
    )
    assert worst_case_usd(EpisodeKind.MANUAL, RS_FIXED, two_neighbours, PRICES) == pytest.approx(
        3 * 0.597 + 6 * 0.821
    )
    assert worst_case_usd(EpisodeKind.HEADSUP, RS_FIXED, two_neighbours, PRICES) == pytest.approx(
        2 * 0.597 + 6 * 0.821
    )
    assert worst_case_usd(EpisodeKind.CHECK, RS_FIXED, [], PRICES) == pytest.approx(3 * 0.597)
    assert worst_case_usd(EpisodeKind.INTRO, RS_FIXED, two_neighbours, PRICES) == pytest.approx(3 * 0.597)
    assert worst_case_usd(EpisodeKind.INTRO_NEIGHBOR, RS_FIXED, [RS_MOBILE], PRICES) == pytest.approx(3 * 0.821)


def test_what_was_spent_is_a_minute_for_every_call_that_connected():
    calls = [
        make_call(id="a", to=RS_FIXED, status="completed", answer=Answer.OK),
        make_call(id="b", to=RS_MOBILE, status="completed", answer=Answer.UNCLEAR),  # a voicemail answered
        make_call(id="c", to=RS_MOBILE, status="completed", answer=Answer.NO_ANSWER),  # rang out: free
        make_call(id="d", to=RS_FIXED, status="placed"),  # never reported back: counted as connected
        make_call(id="e", to=RS_FIXED, status="queued"),  # may still ring after its check is settled
        make_call(id="f", to=RS_FIXED, status="blocked"),
    ]
    assert spent_usd(calls, PRICES) == pytest.approx(0.597 + 0.821 + 0.597 + 0.597)


def test_a_payer_has_a_monthly_cap_and_a_season_cap_in_its_own_time_zone():
    payer = Payer(id="o1", name="Org", month_cap_usd=50, season_cap_usd=400,
                  season_start=date(2027, 6, 1), season_end=date(2027, 9, 30), tz="Europe/Berlin")  # fmt: skip
    in_season = datetime(2027, 6, 30, 23, 30, tzinfo=UTC)  # already 1 July in Berlin
    assert budget_caps(payer, in_season) == [("o1:month:2027-07", 50), ("o1:season:2027-06-01", 400)]


def test_outside_its_season_or_with_no_cap_a_payer_has_no_money_for_calls():
    seasonal = Payer(id="o1", name="Org", season_cap_usd=400, season_start=date(2027, 6, 1), season_end=date(2027, 9, 30))
    assert budget_caps(seasonal, datetime(2027, 10, 1, 12, tzinfo=UTC)) == []
    assert budget_caps(Payer(id="o2", name="Org"), T0) == []
    monthly = Payer(id="own", name="Kuc-kuc", month_cap_usd=25, tz="Europe/Oslo")
    assert budget_caps(monthly, T0) == [("own:month:2026-11", 25)]
