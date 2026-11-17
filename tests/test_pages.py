import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import build_sys

WEB_DIR = Path(__file__).resolve().parent.parent / "src" / "kuckuc" / "web"


@pytest.fixture
def web():
    sys = build_sys()
    client = TestClient(sys.web)
    client.deps = sys.deps
    return client


def test_landing_has_demo_and_disclaimer(web):
    html = web.get("/").text
    assert "Try it on your phone" in html and "not an emergency service" in html and "Open-Meteo" in html


def test_landing_says_what_it_is_in_the_first_heading(web):
    html = web.get("/").text
    assert "<h1>A knock on the door when dangerous weather comes.</h1>" in html
    assert "southern Serbia" in html and "landline" in html and "neighbour" in html


def private_words() -> list[str]:
    """Names that must never reach a public page: the real person, her village, her family.
    They are kept in tests/private_words.txt, which is never committed, so that this public
    repository does not name them either. Without the file, only the example names are checked."""
    from pathlib import Path

    kept = Path(__file__).with_name("private_words.txt")
    words = kept.read_text(encoding="utf-8").split() if kept.exists() else []
    return ["milena", *(w.lower() for w in words)]


def test_public_pages_name_no_person_and_no_village(web):
    for path in ("/", "/architecture", "/family"):
        html = web.get(path).text.lower()
        for word in private_words():
            assert word not in html, path


def test_landing_carries_everything_the_demo_script_needs(web):
    html = web.get("/").text
    for element_id in ("demo", "demo-phone", "demo-send", "demo-code", "demo-verify", "demo-timeline", "demo-message"):
        assert f'id="{element_id}"' in html
    assert 'data-scenario="heat"' in html and 'data-scenario="cold"' in html
    assert '<script src="/static/demo.js?v=' in html
    assert "Heatwave tomorrow" in html and "Cold snap tomorrow" in html


def test_landing_tells_the_visitor_what_happens_to_their_number(web):
    html = web.get("/").text
    assert "United States, Canada, the United Kingdom and Norway" in html
    # Only what really happens: database expiry runs up to a day late, and recordings are kept a week.
    assert "within two days" in html and "recording of your answer after seven days" in html
    assert "within 24 hours" not in html


def test_healthz(web):
    assert web.get("/healthz").json() == {"ok": True}


def test_health_under_the_name_cloud_run_lets_through(web):
    # Google's front end answers /healthz itself with its own 404, so the same check also lives at /health.
    assert web.get("/health").json() == {"ok": True}


def test_static_files_are_served(web):
    css = web.get("/static/app.css")
    js = web.get("/static/demo.js")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def test_pages_forbid_framing_and_outside_scripts(web):
    headers = web.get("/").headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "script-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"


def test_the_page_has_no_inline_script_or_style(web):
    html = web.get("/").text
    assert "<style" not in html and "onclick=" not in html
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)


def test_github_link_appears_only_when_a_repository_is_set(web):
    assert "github.com" not in web.get("/").text
    web.deps.settings.github_url = "https://github.com/example/kuc-kuc"
    assert 'href="https://github.com/example/kuc-kuc"' in web.get("/").text


def test_demo_script_explains_every_refusal_and_every_timeline_row():
    js = (WEB_DIR / "static" / "demo.js").read_text(encoding="utf-8")
    for code in ("bad_number", "country", "number_type", "phone_limit", "ip_limit", "budget", "ended", "low_balance",
                 "wrong_code", "session", "verify_failed", "own_number"):  # fmt: skip
        assert f"{code}:" in js, code
    for event in ("episode_started", "call_queued", "call_placed", "call_answered", "answer_interpreted",
                  "family_notified", "episode_closed", "call_missed", "call_hung_up", "call_failed", "call_blocked"):  # fmt: skip
        assert f"{event}:" in js, event


def test_no_inline_phone_numbers_or_secrets_in_static():
    for p in WEB_DIR.rglob("*.*"):
        if p.suffix in {".html", ".js", ".css"}:
            assert not re.search(r"\+\d{8,}", p.read_text(encoding="utf-8")), p


def test_scripts_and_styles_are_checked_for_a_newer_version_on_every_visit(web):
    # Without this a browser kept an old family.js for hours after a release, missing a new button.
    for path in ("/static/family.js", "/static/demo.js", "/static/app.css"):
        assert web.get(path).headers.get("cache-control") == "no-cache"


def test_pages_load_scripts_and_styles_by_a_name_that_changes_with_every_release(web):
    # A browser that cached yesterday's file must still fetch today's at once.
    import re

    for path, asset in (("/", "demo.js"), ("/family", "family.js"), ("/", "app.css"), ("/architecture", "app.css")):
        assert re.search(rf'/static/{re.escape(asset)}\?v=[0-9a-f]{{10}}"', web.get(path).text), (path, asset)
