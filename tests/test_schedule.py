from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from factories import make_alert

from kuckuc.engine.schedule import plan_calls
from kuckuc.models import EpisodeKind

BG = "Europe/Belgrade"


def at(local_iso, tz=BG):
    """A local wall-clock time as the UTC moment it is."""
    return datetime.fromisoformat(local_iso).replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)


def test_cold_tomorrow_gets_headsup_today_17_and_check_tomorrow_10():
    a = make_alert(hazard="cold", day=date(2026, 11, 20))
    assert plan_calls(a, BG, at("2026-11-19T09:00"), False) == [
        (EpisodeKind.HEADSUP, at("2026-11-19T17:00")),
        (EpisodeKind.CHECK, at("2026-11-20T10:00")),
    ]


def test_heat_check_is_at_17():
    a = make_alert(hazard="heat", day=date(2027, 7, 15))
    assert plan_calls(a, BG, at("2027-07-14T09:00"), False)[1] == (EpisodeKind.CHECK, at("2027-07-15T17:00"))


def test_wind_and_snow_checks_are_at_10():
    for hazard in ("wind", "snow"):
        a = make_alert(hazard=hazard, day=date(2026, 11, 20))
        assert plan_calls(a, BG, at("2026-11-19T09:00"), True) == [(EpisodeKind.CHECK, at("2026-11-20T10:00"))]


def test_no_headsup_on_second_day_or_when_found_too_late():
    a = make_alert(hazard="cold", day=date(2026, 11, 20))
    assert [k for k, _ in plan_calls(a, BG, at("2026-11-19T09:00"), True)] == [EpisodeKind.CHECK]
    assert [k for k, _ in plan_calls(a, BG, at("2026-11-19T18:00"), False)] == [EpisodeKind.CHECK]
    assert [k for k, _ in plan_calls(a, BG, at("2026-11-19T17:00"), False)] == [EpisodeKind.CHECK]


def test_found_at_noon_on_the_day_checks_now():
    a = make_alert(hazard="cold", day=date(2026, 11, 20))
    now = at("2026-11-20T12:00")
    assert plan_calls(a, BG, now, False) == [(EpisodeKind.CHECK, now)]


def test_found_early_on_the_day_still_waits_for_ten():
    a = make_alert(hazard="cold", day=date(2026, 11, 20))
    assert plan_calls(a, BG, at("2026-11-20T03:30"), False) == [(EpisodeKind.CHECK, at("2026-11-20T10:00"))]


def test_found_at_1959_checks_now_and_at_2000_does_not():
    a = make_alert(hazard="cold", day=date(2026, 11, 20))
    late = at("2026-11-20T19:59")
    assert plan_calls(a, BG, late, False) == [(EpisodeKind.CHECK, late)]
    assert plan_calls(a, BG, at("2026-11-20T20:00"), False) == []


def test_found_at_2030_on_the_day_does_not_call():
    a = make_alert(hazard="cold", day=date(2026, 11, 20))
    assert plan_calls(a, BG, at("2026-11-20T20:30"), False) == []


def test_heat_found_after_17_still_gets_a_check_while_it_is_daytime():
    a = make_alert(hazard="heat", day=date(2027, 7, 15))
    now = at("2027-07-15T18:15")
    assert plan_calls(a, BG, now, True) == [(EpisodeKind.CHECK, now)]


def test_a_day_that_is_already_over_gets_nothing():
    a = make_alert(hazard="cold", day=date(2026, 11, 20))
    assert plan_calls(a, BG, at("2026-11-21T09:00"), False) == []


def test_times_follow_daylight_saving():
    a = make_alert(hazard="cold", day=date(2027, 3, 28))  # clocks go forward that night
    _, when = plan_calls(a, BG, at("2027-03-26T09:00"), True)[0]
    assert when == datetime(2027, 3, 28, 8, 0, tzinfo=UTC)  # 10:00 CEST


def test_times_are_in_the_persons_own_timezone():
    ny = "America/New_York"
    a = make_alert(hazard="cold", day=date(2026, 11, 20))
    planned = plan_calls(a, ny, at("2026-11-19T09:00", ny), False)
    assert planned == [
        (EpisodeKind.HEADSUP, datetime(2026, 11, 19, 22, 0, tzinfo=UTC)),  # 17:00 EST
        (EpisodeKind.CHECK, datetime(2026, 11, 20, 15, 0, tzinfo=UTC)),  # 10:00 EST
    ]
    # 09:00 in Belgrade on the day is 03:00 in New York: far too early to ring there.
    early = at("2026-11-20T09:00")
    assert plan_calls(a, ny, early, True) == [(EpisodeKind.CHECK, datetime(2026, 11, 20, 15, 0, tzinfo=UTC))]


def test_calls_that_can_wait_start_at_once_by_day_and_at_eight_in_the_morning_otherwise():
    from kuckuc.engine.schedule import daytime_start

    for daytime in ("2026-11-13T08:00", "2026-11-13T10:00", "2026-11-13T19:59"):
        assert daytime_start(BG, at(daytime)) == at(daytime)
    assert daytime_start(BG, at("2026-11-13T20:00")) == at("2026-11-14T08:00")
    assert daytime_start(BG, at("2026-11-13T22:30")) == at("2026-11-14T08:00")
    assert daytime_start(BG, at("2026-11-14T05:00")) == at("2026-11-14T08:00")
    assert daytime_start(BG, at("2026-03-28T23:00")) == at("2026-03-29T08:00")  # the clocks go forward that night
    ny = "America/New_York"
    assert daytime_start(ny, at("2026-11-13T21:00", ny)) == at("2026-11-14T08:00", ny)
