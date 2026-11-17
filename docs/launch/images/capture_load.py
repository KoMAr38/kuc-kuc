#!/usr/bin/env python3
"""Gallery image of the load test: Cloud Run instances per minute, from Cloud Monitoring.

    .venv/Scripts/python docs/launch/images/capture_load.py

The first run reads the instance counts of `engine` and `voice` during the load test of
13 November 2026 from Cloud Monitoring (needs `gcloud auth application-default login`) and
keeps them in src/loadtest-instances.json; later runs draw from that file. Writes
gallery-7-under-load.png next to this file, 1270 x 760.
"""

from __future__ import annotations

import io
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "src" / "loadtest-instances.json"
OUT = HERE / "gallery-7-under-load.png"
PROJECT = "kuc-kuc-38"
START, END = "2026-11-13T20:50:00Z", "2026-11-13T21:23:00Z"
TEST_STARTED, TEST_CLOSED = "20:53:52", "20:57:25"  # from the load test's own summary
NUMBERS = {"people": "2,000", "calls": "3,008", "seconds": "213", "peak": "14"}

sys.path.insert(0, str(HERE))


def fetch() -> list[dict]:
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/monitoring.read"])
    session = AuthorizedSession(credentials)
    session.headers["x-goog-user-project"] = PROJECT
    reply = session.get(
        f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries",
        params={
            "filter": 'metric.type="run.googleapis.com/container/instance_count" AND '
            '(resource.labels.service_name="engine" OR resource.labels.service_name="voice")',
            "interval.startTime": START,
            "interval.endTime": END,
            "aggregation.alignmentPeriod": "60s",
            "aggregation.perSeriesAligner": "ALIGN_MAX",
        },
        timeout=30,
    )
    reply.raise_for_status()
    minutes: dict[str, dict[str, int]] = defaultdict(lambda: {"active": 0, "idle": 0})
    for series in reply.json().get("timeSeries", []):
        state = series["metric"]["labels"].get("state", "active")
        for point in series["points"]:
            value = point["value"]
            minutes[point["interval"]["endTime"][11:16]][state] += int(value.get("int64Value", value.get("doubleValue", 0)))
    first = datetime.fromisoformat(START.replace("Z", "+00:00")) + timedelta(minutes=1)
    rows = []
    for i in range(int((datetime.fromisoformat(END.replace("Z", "+00:00")) - first).total_seconds() // 60)):
        label = (first + timedelta(minutes=i)).strftime("%H:%M")
        rows.append({"minute": label, **minutes.get(label, {"active": 0, "idle": 0})})
    return rows


def chart(rows: list[dict]) -> str:
    width, height, left, bottom, top = 1100, 350, 46, 34, 14
    plot_w, plot_h = width - left - 8, height - bottom - top
    most = max(max(r["active"] + r["idle"] for r in rows), 14)
    scale = plot_h / most
    step = plot_w / len(rows)
    bar = step * 0.72
    parts = []
    for tick in range(0, most + 1, 5):
        y = top + plot_h - tick * scale
        parts.append(f'<line x1="{left}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 10}" y="{y + 5:.1f}" class="axis" text-anchor="end">{tick}</text>')
    for i, r in enumerate(rows):
        x = left + i * step + (step - bar) / 2
        base = top + plot_h
        h_active, h_idle = r["active"] * scale, r["idle"] * scale
        if r["active"]:
            parts.append(f'<rect x="{x:.1f}" y="{base - h_active:.1f}" width="{bar:.1f}" height="{h_active:.1f}" class="active"/>')
        if r["idle"]:
            parts.append(
                f'<rect x="{x:.1f}" y="{base - h_active - h_idle:.1f}" width="{bar:.1f}" height="{h_idle:.1f}" class="idle"/>'
            )
        if r["minute"].endswith(("0", "5")):
            parts.append(f'<text x="{x + bar / 2:.1f}" y="{height - 8}" class="axis" text-anchor="middle">{r["minute"]}</text>')
    peak = max(range(len(rows)), key=lambda i: rows[i]["active"])
    px = left + peak * step + step / 2
    py = top + plot_h - rows[peak]["active"] * scale
    parts.append(
        f'<text x="{px - step / 2 - 10:.1f}" y="{py + 16:.1f}" class="note" text-anchor="end">'
        f'{rows[peak]["active"]} serving at the peak</text>'
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
        'aria-label="Instances of engine and voice per minute during the load test">' + "".join(parts) + "</svg>"
    )


def page(rows: list[dict]) -> str:
    n = NUMBERS
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Under load</title>
<style>
:root {{ --paper:#f7f3ec; --card:#fffdf9; --ink:#1d2528; --muted:#586367; --line:#ddd5c8; --accent:#a8432a;
  --accent-strong:#8a3520; --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; }}
* {{ box-sizing:border-box; margin:0; }}
html,body {{ width:1270px; height:760px; background:var(--paper); color:var(--ink); font-family:var(--sans); }}
main {{ padding:56px 64px; }}
.eyebrow {{ color:var(--accent-strong); font-weight:600; letter-spacing:.02em; font-size:18px; }}
h1 {{ font-family:var(--serif); font-size:46px; line-height:1.1; margin-top:8px; font-weight:600; }}
.numbers {{ display:flex; gap:44px; margin-top:26px; }}
.numbers div {{ border-top:3px solid var(--accent); padding-top:8px; }}
.numbers strong {{ display:block; font-family:var(--serif); font-size:34px; }}
.numbers span {{ color:var(--muted); font-size:16px; }}
.chart {{ margin-top:26px; background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 18px 6px; }}
svg .grid {{ stroke:var(--line); stroke-width:1; }}
svg .axis {{ fill:var(--muted); font:13px var(--sans); }}
svg .active {{ fill:var(--accent); }}
svg .idle {{ fill:#e7c9bd; }}
svg .note {{ fill:var(--accent-strong); font:600 15px var(--sans); }}
.legend {{ display:flex; gap:22px; color:var(--muted); font-size:14px; margin-top:12px; }}
.legend i {{ display:inline-block; width:12px; height:12px; border-radius:2px; margin-right:6px; vertical-align:-1px; }}
</style></head>
<body><main>
<p class="eyebrow">Under load</p>
<h1>{n["people"]} people in a heatwave, all at once</h1>
<div class="numbers">
  <div><strong>{n["calls"]}</strong><span>simulated calls</span></div>
  <div><strong>{n["seconds"]} s</strong><span>until every case was closed</span></div>
  <div><strong>{n["peak"]}</strong><span>instances at the peak</span></div>
  <div><strong>0</strong><span>when it was over</span></div>
</div>
<div class="chart">
  {chart(rows)}
</div>
<p class="legend"><span><i style="background:var(--accent)"></i>engine and voice instances serving requests</span>
<span><i style="background:#e7c9bd"></i>kept warm, idle, then stopped</span>
<span>Per minute, UTC, 13 November 2026 · Cloud Monitoring · telephony simulated, everything else real</span></p>
</main></body></html>"""


def main() -> int:
    from PIL import Image
    from playwright.sync_api import sync_playwright
    from render import SIZE, installed_browser

    if not DATA.exists():
        DATA.write_text(json.dumps(fetch(), indent=1), encoding="utf-8")
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    html = HERE / "src" / "gallery-7-under-load.html"
    html.write_text(page(rows), encoding="utf-8")
    with sync_playwright() as p:
        executable = installed_browser(None)
        browser = p.chromium.launch(**({"executable_path": str(executable)} if executable else {}))
        context = browser.new_context(viewport={"width": SIZE[0], "height": SIZE[1]}, device_scale_factor=2)
        tab = context.new_page()
        tab.goto(html.as_uri())
        shot = tab.screenshot()
        browser.close()
    image = Image.open(io.BytesIO(shot)).convert("RGB").resize(SIZE, Image.Resampling.LANCZOS)
    image.save(OUT, optimize=True)
    print(f"wrote {OUT} {image.size[0]}x{image.size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
