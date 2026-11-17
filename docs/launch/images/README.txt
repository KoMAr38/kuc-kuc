KUC-KUC PRODUCT HUNT GALLERY

STATUS
All five PNGs have been rendered with render.py on Windows (installed Chrome) and inspected
at full size on 2026-11-13. The wires in gallery-4-architecture were redrawn after the first
render: services never call each other directly, so the arrows now go through Pub/Sub and
Cloud Tasks, as on the site's architecture page.

Also in this folder, not produced by render.py:
thumbnail.png            the door-and-knock mark, 480 x 480, for the Product Hunt logo
gallery-1-landing.png    a screenshot of the live landing page, 1270 x 760

CONTENTS
src/gallery-1-hero.html
src/gallery-2-how-it-works.html
src/gallery-3-live-demo.html
src/gallery-4-architecture.html
src/gallery-5-who-it-is-for.html
render.py
requirements.txt
README.txt

INSTALL INTO THE LOCAL REPOSITORY
Extract the package into D:\Kuc kuc. The package contains only docs/launch/images/.
It contains no root src/ or tests/ files.

RENDER ON WINDOWS (POWERSHELL)
Run these commands from the repository root:

    Set-Location -LiteralPath 'D:\Kuc kuc'
    py -m pip install -r docs/launch/images/requirements.txt
    py docs/launch/images/render.py

If the Python command on your computer is python, use python instead of py.
The renderer uses an installed Chrome/Chromium browser when it finds one.
On Windows, installed Microsoft Edge can also be used through Playwright.
If it reports that no browser was found, install Playwright's Chromium:

    py -m playwright install chromium
    py docs/launch/images/render.py

To choose a browser explicitly:

    py docs/launch/images/render.py --browser-path 'C:\Program Files\Google\Chrome\Application\chrome.exe'

OUTPUT FILES
docs/launch/images/gallery-1-hero.png
docs/launch/images/gallery-2-how-it-works.png
docs/launch/images/gallery-3-live-demo.png
docs/launch/images/gallery-4-architecture.png
docs/launch/images/gallery-5-who-it-is-for.png

Each page is captured with a 1270 x 760 CSS-pixel viewport and device scale
factor 2, giving a 2540 x 1520 screenshot. Pillow downsamples that capture once
using Lanczos to an opaque RGB PNG at exactly 1270 x 760.

The renderer checks exact copy, the eight demo events, the green final row,
the original font stacks, the paper background, and a 60 px safe area.
It stops if a page fails those checks. It writes final PNGs only after all five
pages pass. It never writes to the repository's root src/ or tests/ directories.
Use --keep-2x to retain the original captures in docs/launch/images/render-2x/.

DESIGN SOURCES
Each HTML page embeds the supplied app.css and door.svg, so it opens without
a server, external fonts, network assets, or any other supporting file.
The presentation rules use 64 px outer offsets, the supplied light palette,
the original serif and system sans stacks, and no gradients or shadows.
Animations and automatic dark-mode colors are disabled for predictable captures.
Native font fallback varies by operating system because the site uses font stacks.

REVIEW
Open every PNG after re-rendering and look at it at 100% before uploading.
