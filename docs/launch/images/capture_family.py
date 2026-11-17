#!/usr/bin/env python3
"""Gallery image of the family page, taken from the local preview with made-up people.

    .venv/Scripts/python docs/launch/images/capture_family.py

Nothing real is on it: the local preview reaches no network, the person and the neighbour are
invented, and the phones are Twilio's test numbers. It enrols the person, lets the pretend
phone answer both introductions, asks for a check, and photographs the person's card once the
check has finished. Writes gallery-6-family-page.png next to this file, 1270 x 760.
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "gallery-6-family-page.png"
PORT = 8093
STARTS_AT = datetime(2026, 11, 20, 13, 5, tzinfo=UTC)  # 14:05 in Belgrade: daytime, so calls go at once

os.environ["KUCKUC_INVITE_CODES"] = "LOCAL"
os.environ["KUCKUC_FEATURES"] = '["forecast","family","loadtest"]'
sys.path.insert(0, str(HERE))

import uvicorn  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from render import SIZE, installed_browser  # noqa: E402

from kuckuc.dev import create_dev_app  # noqa: E402


def start_preview() -> None:
    server = uvicorn.Server(
        uvicorn.Config(create_dev_app(now=STARTS_AT), host="127.0.0.1", port=PORT, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            return
        time.sleep(0.05)
    raise RuntimeError("the local preview did not start")


def wait_for(page, script: str, what: str, limit_s: int = 120) -> None:
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        if page.evaluate(script):
            return
        time.sleep(1)
    raise RuntimeError(f"timed out waiting for {what}")


def main() -> int:
    start_preview()
    base = f"http://127.0.0.1:{PORT}"
    with sync_playwright() as p:
        executable = installed_browser(None)
        browser = p.chromium.launch(**({"executable_path": str(executable)} if executable else {}))
        context = browser.new_context(
            viewport={"width": SIZE[0], "height": SIZE[1]},
            device_scale_factor=2,
            locale="en-GB",
            timezone_id="Europe/Belgrade",
            color_scheme="light",
        )
        page = context.new_page()
        page.clock.set_fixed_time(STARTS_AT)
        page.goto(f"{base}/family")
        page.fill("#local-email", "family@example.com")
        page.click("#local-sign-in button[type=submit]")
        page.wait_for_selector("#signed-in:not([hidden])")

        # The form opens by itself while nobody is enrolled; a click on its title would close it.
        page.wait_for_function("document.getElementById('add-person').open")
        page.fill("#enrol-form [name=invite_code]", "LOCAL")
        page.fill("#enrol-form [name=name]", "baba Vera")
        page.fill("#enrol-form [name=family_name]", "vaš unuk Petar")
        page.fill("#enrol-form [name=phone]", "+15005550006")
        page.fill("#place-search", "Selo")
        page.click("#place-results button")
        page.click("#add-neighbor")
        page.fill('#neighbor-rows [data-field="name"]', "Dragan")
        page.fill('#neighbor-rows [data-field="phone"]', "+15005550007")
        page.click("#enrol-form button[type=submit]")

        both_active = """async () => {
            const me = await (await fetch('/api/family/me')).json();
            const p = me.persons[0];
            return !!p && p.status === 'active' && p.neighbors.every(n => n.status === 'active');
        }"""
        wait_for(page, both_active, "both introductions to be answered")
        page.reload()
        page.get_by_role("button", name="Call now").click()
        check_done = """async () => {
            const me = await (await fetch('/api/family/me')).json();
            const eps = await (await fetch('/api/family/persons/' + me.persons[0].id + '/episodes')).json();
            return eps.length > 0 && eps[0].kind === 'manual' && eps[0].result !== null;
        }"""
        wait_for(page, check_done, "the check to finish")
        page.reload()
        page.wait_for_selector(".card.person .timeline li")
        page.evaluate("window.scrollTo(0, document.querySelector('#people').getBoundingClientRect().top + scrollY - 24)")
        time.sleep(0.5)
        shot = page.screenshot()
        browser.close()

    image = Image.open(io.BytesIO(shot)).convert("RGB").resize(SIZE, Image.Resampling.LANCZOS)
    image.save(OUT, optimize=True)
    print(f"wrote {OUT} {image.size[0]}x{image.size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
