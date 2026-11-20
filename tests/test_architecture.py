import re
from pathlib import Path
from xml.etree import ElementTree

import pytest
from factories import T0, make_call, make_person
from fastapi.testclient import TestClient
from helpers import build_sys

from kuckuc.web.diagram import render_diagram

WEB_DIR = Path(__file__).resolve().parent.parent / "src" / "kuckuc" / "web"
ALL = {"forecast", "family", "loadtest"}


@pytest.fixture
def web():
    sys = build_sys()
    client = TestClient(sys.web)
    client.deps = sys.deps
    return client


def test_stats_shape(web):
    assert set(web.get("/api/stats").json()) == {"calls_today", "calls_total", "persons_active", "loadtest"}


def ring(store, id):
    """A call as voice makes one: created, dialled, placed."""
    store.create_call(make_call(id=id))
    store.start_dialing(id, T0)
    store.mark_placed(id, "CA" + id)


def test_stats_count_real_calls_and_people_and_have_no_load_test_by_default(web):
    ring(web.deps.store, "c1")
    web.deps.store.put_person(make_person(id="demo-1", demo=True))
    assert web.get("/api/stats").json() == {"calls_today": 1, "calls_total": 1, "persons_active": 1, "loadtest": None}


def test_stats_carry_the_latest_load_test(web):
    web.deps.store.put_stat_doc("loadtest_latest", {"n": 2000, "seconds": 95, "calls": 2600})
    assert web.get("/api/stats").json()["loadtest"] == {"n": 2000, "seconds": 95, "calls": 2600}


def test_architecture_hides_unbuilt_features(web):
    web.deps.settings.features = set()
    html = web.get("/architecture").text
    assert "forecast-scan" not in html and "voice" in html and "engine" in html
    web.deps.settings.features = {"forecast"}
    assert "forecast-scan" in web.get("/architecture").text


def test_architecture_names_only_what_is_live(web):
    web.deps.settings.features = set()
    bare = web.get("/architecture").text
    for word in ("Telegram", "Open-Meteo forecast", "loadtest", "Cloud Scheduler"):
        assert word not in bare, word
    web.deps.settings.features = set(ALL)
    full = web.get("/architecture").text
    for word in ("Telegram", "forecast-scan", "loadtest", "Cloud Scheduler"):
        assert word in full, word


def test_architecture_explains_the_parts_and_the_reasons(web):
    html = web.get("/architecture").text
    for needle in (
        "<h1>",
        "Pub/Sub",
        "Cloud Tasks",
        "Firestore",
        "Cloud Storage",
        "Vertex AI",
        "Secret Manager",
        "Why scale to zero matters here",
        "any doubt counts as a problem",
        "What it costs",
        "not an emergency service",
    ):
        assert needle in html, needle


def test_architecture_shows_the_counters_from_storage(web):
    for i in range(3):
        ring(web.deps.store, f"c{i}")
    html = web.get("/architecture").text
    assert re.search(r'id="stat-calls-total"[^>]*>\s*3\s*<', html)
    assert re.search(r'id="stat-persons-active"[^>]*>\s*1\s*<', html)


def test_load_test_numbers_appear_only_when_that_feature_is_on(web):
    # Numbers chosen so they cannot appear in the page by accident (the SVG namespace contains "2000").
    web.deps.store.put_stat_doc("loadtest_latest", {"n": 2345, "seconds": 97, "calls": 3456, "by_result": {}})
    web.deps.settings.features = set()
    bare = web.get("/architecture").text
    assert "2345" not in bare and "3456" not in bare and "Under load" not in bare
    web.deps.settings.features = {"loadtest"}
    html = web.get("/architecture").text
    assert "<strong>2345</strong>" in html and "<strong>97</strong>" in html and "<strong>3456</strong>" in html


def test_diagram_is_inline_accessible_and_csp_safe(web):
    html = web.get("/architecture").text
    assert '<svg class="diagram"' in html and "<title" in html and 'role="img"' in html
    assert " style=" not in html  # inline styles would be blocked by the page's own policy


def test_diagram_is_well_formed_for_every_combination_of_features():
    for features in (set(), {"forecast"}, {"family"}, {"loadtest"}, set(ALL)):
        root = ElementTree.fromstring(render_diagram(features))
        assert root.tag.endswith("svg")
        text = " ".join(root.itertext())
        assert ("forecast-scan" in text) == ("forecast" in features)
        assert ("Telegram" in text) == ("family" in features)
        assert ("loadtest" in text) == ("loadtest" in features)
        for always in ("web", "engine", "voice", "Pub/Sub", "Cloud Tasks", "Twilio"):
            assert always in text


def test_static_diagram_file_is_the_full_diagram_and_up_to_date():
    saved = (WEB_DIR / "static" / "architecture.svg").read_text(encoding="utf-8")
    assert saved == render_diagram(ALL), "run: python -m kuckuc.web.diagram"



def test_the_public_numbers_are_read_from_the_database_at_most_every_half_minute(web):
    reads = []
    stats = web.deps.store.stats
    web.deps.store.stats = lambda: reads.append(1) or stats()
    web.get("/api/stats")
    web.get("/architecture")
    web.get("/api/stats")
    assert len(reads) == 1
    web.deps.clock.advance(31)
    web.get("/api/stats")
    assert len(reads) == 2
