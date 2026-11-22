import importlib.util
from pathlib import Path

from factories import T0

from kuckuc.store import MemoryStore

spec = importlib.util.spec_from_file_location("payers", Path(__file__).resolve().parent.parent / "scripts" / "payers.py")
payers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payers)


def test_the_owner_sets_a_budget_and_sees_what_is_left():
    store = MemoryStore()
    payers.apply(store, T0, ["own", "--name", "Kuc-kuc", "--month", "25"])
    assert store.get_payer("own").month_cap_usd == 25
    lines = payers.apply(store, T0, ["own"])  # showing it changes nothing
    assert store.get_payer("own").month_cap_usd == 25
    assert lines[-1] == "own:month:2026-11: spent $0.00, held $0.00, cap $25.00"


def test_an_organisation_gets_a_season_staff_and_an_invite_code():
    store = MemoryStore()
    argv = ["org-1", "--name", "Org", "--season", "400", "--from", "2026-11-01", "--to", "2027-03-31",
            "--staff", "uid-a", "--staff", "uid-b", "--invite", "ORG-CODE", "--tz", "Europe/Berlin"]  # fmt: skip
    payers.apply(store, T0, argv)
    payer = store.find_payer_by_invite("ORG-CODE")
    assert (payer.id, payer.season_cap_usd, payer.staff_uids, payer.tz) == ("org-1", 400, ["uid-a", "uid-b"], "Europe/Berlin")
    payers.apply(store, T0, ["org-1", "--month", "150"])  # one change keeps everything else
    assert store.get_payer("org-1").season_cap_usd == 400 and store.get_payer("org-1").month_cap_usd == 150


def test_a_payer_with_no_cap_is_reported_as_having_no_money():
    store = MemoryStore()
    assert payers.apply(store, T0, ["org-2", "--name", "Org"])[-1] == "no money for calls now: its checks do not start"
    assert payers.apply(store, T0, ["nobody"]) == ["no payer nobody"]
