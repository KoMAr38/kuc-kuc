#!/usr/bin/env python3
"""Render the five standalone gallery pages with Playwright and Chromium.

Run this file from any directory. Only files next to this script are written.
The capture is 2540 x 1520 (1270 x 760 CSS pixels at DPR 2); Pillow then
downsamples once with Lanczos to the required 1270 x 760 PNG output.
"""
from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
SIZE = (1270, 760)
STEMS = (
    'gallery-1-hero',
    'gallery-2-how-it-works',
    'gallery-3-live-demo',
    'gallery-4-architecture',
    'gallery-5-who-it-is-for',
)
COPY = (
    (
        'A knock on the door when dangerous weather comes.',
        'Kuc-kuc phones an older person who lives alone. No answer? It phones a neighbour.',
        'Works on a landline. No app. No smartphone.',
    ),
    (
        'How it works',
        '1 The forecast crosses a line',
        'Heat, frost, wind or snow above a threshold.',
        '2 Her phone rings',
        'A calm voice asks: are you all right? She just answers out loud.',
        '3 A neighbour knocks',
        "No answer or 'I am not well': the neighbour is called.",
        'One rule: any doubt counts as a problem.',
    ),
    (
        'Try it on your own phone',
        'Forecast crossed the threshold: extreme heat',
        'Calling you…',
        'No answer',
        'Calling the neighbour (you again, in a second role)…',
        'Picked up',
        'Heard → yes',
        'Family notified: “The neighbour is on the way.”',
        'Done: the neighbour went over and reports all is well.',
    ),
    (
        'Three services that sleep until they are needed',
        'Cloud Run services',
        'web (public)',
        'engine (private, one state machine)',
        'voice (talks to the phone network)',
        'Cloud Run jobs',
        'forecast-scan (every 3 h)',
        'loadtest',
        'Pub/Sub: events',
        'Cloud Tasks: every wait (call again in 10 min, ask the neighbour in 20)',
        'Firestore',
        'Cloud Storage',
        'Vertex AI Gemini: understands the spoken answer',
        'Chirp 3 HD: the voice',
        'Twilio: phone calls',
        'Open-Meteo: forecast',
        'Telegram: messages to the family',
        'Min instances: 0. On a quiet day it does nothing and costs nothing.',
    ),
    (
        'For the people a smartphone app will never reach.',
        'An older person on a landline. Family hours away. Help across the road.',
        'A pilot by invitation. Not an emergency service.',
    ),
)

AUDIT_JS = r'''() => {
  const violations = [], fonts = new Set();
  const serif = getComputedStyle(document.documentElement).getPropertyValue('--serif').trim();
  const sans = getComputedStyle(document.documentElement).getPropertyValue('--sans').trim();
  const normalizeFont = value => value.replace(/["'\s]/g, '').toLowerCase();
  const allowedFonts = new Set([normalizeFont(serif), normalizeFont(sans)]);
  const hidden = el => {
    for (let p = el; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return true;
    }
    return false;
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, el = node.parentElement;
    if (!el || el.closest('script,style,title') || !node.textContent.trim() || hidden(el)) continue;
    const cs = getComputedStyle(el);
    fonts.add(cs.fontFamily);
    if (!allowedFonts.has(normalizeFont(cs.fontFamily))) {
      violations.push('Unexpected font: ' + cs.fontFamily);
    }
    const range = document.createRange();
    range.selectNodeContents(node);
    for (const r of range.getClientRects()) {
      if (!r.width || !r.height) continue;
      if (r.left < 59.95 || r.top < 59.95 || r.right > 1210.05 || r.bottom > 700.05) {
        violations.push('Text exceeds the 60px safe area: ' + node.textContent.trim());
      }
      for (let p = el; p && p !== document.body; p = p.parentElement) {
        const s = getComputedStyle(p), b = p.getBoundingClientRect();
        if ((s.overflowX === 'hidden' || s.overflowX === 'clip') && (r.left < b.left - .1 || r.right > b.right + .1)) {
          violations.push('Text clipped horizontally: ' + node.textContent.trim());
        }
        if ((s.overflowY === 'hidden' || s.overflowY === 'clip') && (r.top < b.top - .1 || r.bottom > b.bottom + .1)) {
          violations.push('Text clipped vertically: ' + node.textContent.trim());
        }
      }
    }
  }
  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    if (s.boxShadow !== 'none' || s.textShadow !== 'none' || s.backgroundImage.includes('gradient')) {
      violations.push('Shadow or gradient on ' + el.tagName);
    }
  }
  for (const el of document.querySelectorAll('[data-safe], .node, .brand, .caption, .architecture')) {
    const r = el.getBoundingClientRect();
    if (r.left < 59.95 || r.top < 59.95 || r.right > 1210.05 || r.bottom > 700.05) {
      violations.push('Element exceeds the 60px safe area: ' + el.className);
    }
  }
  return {
    text: document.body.innerText,
    width: innerWidth, height: innerHeight, dpr: devicePixelRatio,
    paper: getComputedStyle(document.body).backgroundColor,
    violations: [...new Set(violations)], fonts: [...fonts],
    events: [...document.querySelectorAll('.event-copy')].map(el => el.innerText),
    lastRowColors: [...document.querySelectorAll('.event-ok time, .event-ok .event-copy')].map(el => getComputedStyle(el).color),
    external: [...document.querySelectorAll('script[src],link[rel="stylesheet"],img[src],iframe[src]')].map(el => el.src || el.href)
  };
}'''


def normalized(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def installed_browser(explicit: str | None) -> Path | None:
    if explicit:
        found = Path(explicit).expanduser().resolve()
        if not found.is_file():
            raise RuntimeError(f'Browser executable not found: {found}')
        return found
    candidates = []
    for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'chrome', 'msedge'):
        path = shutil.which(name)
        if path:
            candidates.append(Path(path))
    if sys.platform == 'win32':
        for variable in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            directory = os.environ.get(variable)
            if directory:
                candidates.extend([
                    Path(directory) / 'Google/Chrome/Application/chrome.exe',
                    Path(directory) / 'Microsoft/Edge/Application/msedge.exe',
                ])
    if sys.platform == 'darwin':
        candidates.append(Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
    return next((p for p in candidates if p.is_file()), None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-path', help='Optional path to a Chrome or Chromium executable.')
    parser.add_argument('--keep-2x', action='store_true', help='Also save the 2540 x 1520 captures in render-2x/.')
    args = parser.parse_args()

    try:
        from PIL import Image
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        parser.exit(2, f'Missing dependency: {exc}\nInstall with: python -m pip install -r "{HERE / "requirements.txt"}"\n')

    for stem in STEMS:
        if not (HERE / 'src' / f'{stem}.html').is_file():
            parser.exit(2, f'Missing source: src/{stem}.html\n')

    try:
        executable = installed_browser(args.browser_path)
        with sync_playwright() as p:
            options = {'headless': True, 'args': ['--disable-background-networking', '--force-color-profile=srgb']}
            if executable:
                options['executable_path'] = str(executable)
            elif not Path(p.chromium.executable_path).is_file():
                raise RuntimeError('No Chrome/Chromium executable found. Run: python -m playwright install chromium')
            browser = p.chromium.launch(**options)
            try:
                context = browser.new_context(
                    viewport={'width': SIZE[0], 'height': SIZE[1]},
                    device_scale_factor=2,
                    color_scheme='light',
                    reduced_motion='reduce',
                    locale='en-US',
                    offline=True,
                    service_workers='block',
                )
                page = context.new_page()
                results = []
                for stem, expected in zip(STEMS, COPY):
                    page.goto((HERE / 'src' / f'{stem}.html').as_uri(), wait_until='load')
                    page.evaluate('document.fonts.ready')
                    audit = page.evaluate(AUDIT_JS)
                    if (audit['width'], audit['height'], audit['dpr']) != (*SIZE, 2):
                        raise RuntimeError(f'{stem}: viewport or device scale factor is incorrect.')
                    if audit['paper'] != 'rgb(247, 243, 236)':
                        raise RuntimeError(f'{stem}: paper background does not match #f7f3ec.')
                    if audit['external']:
                        raise RuntimeError(f'{stem}: unexpected external dependencies.')
                    text = normalized(audit['text'])
                    if not text.startswith('Kuc-kuc'):
                        raise RuntimeError(f'{stem}: brand mark is missing.')
                    missing = [line for line in expected if normalized(line) not in text]
                    if missing:
                        raise RuntimeError(f'{stem}: missing exact copy: {missing}')
                    if stem == 'gallery-3-live-demo':
                        if audit['events'] != list(expected[1:]):
                            raise RuntimeError('Demo event wording or order is incorrect.')
                        if audit['lastRowColors'] != ['rgb(37, 107, 74)'] * 2:
                            raise RuntimeError('The complete final demo row must be green.')
                    if audit['violations']:
                        raise RuntimeError(stem + ': ' + '; '.join(audit['violations']))
                    capture = page.screenshot(type='png', full_page=False, animations='disabled', scale='device')
                    with Image.open(io.BytesIO(capture)) as source:
                        if source.size != (SIZE[0] * 2, SIZE[1] * 2):
                            raise RuntimeError(f'{stem}: unexpected capture dimensions: {source.size}')
                        final = source.convert('RGB').resize(SIZE, Image.Resampling.LANCZOS)
                        results.append((stem, capture, final))
                # All pages passed before any final image is written.
                for stem, capture, final in results:
                    target = HERE / f'{stem}.png'
                    final.save(target, format='PNG', optimize=True)
                    with Image.open(target) as verified:
                        if verified.size != SIZE:
                            raise RuntimeError(f'{stem}: final PNG size is incorrect.')
                    if args.keep_2x:
                        (HERE / 'render-2x').mkdir(exist_ok=True)
                        (HERE / 'render-2x' / f'{stem}.png').write_bytes(capture)
                    print(f'Created {target.name}: 1270 x 760; captured at DPR 2; copy and safe-area checks passed.')
                print('Open all five PNGs for final visual inspection before uploading them.')
            finally:
                browser.close()
    except Exception as exc:
        print(f'Rendering failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
