"""The architecture diagram as SVG text.

One template draws it for the page (showing only the parts that are live) and for
``static/architecture.svg`` (the whole picture, used by the README):

    python -m kuckuc.web.diagram        # rewrites static/architecture.svg
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

WEB_DIR = Path(__file__).resolve().parent
ALL_FEATURES = frozenset({"forecast", "family", "loadtest"})

_env = Environment(loader=FileSystemLoader(WEB_DIR / "templates"), autoescape=True)


def render_diagram(features: Iterable[str]) -> str:
    return _env.get_template("_diagram.svg").render(features=set(features))


if __name__ == "__main__":
    target = WEB_DIR / "static" / "architecture.svg"
    target.write_text(render_diagram(ALL_FEATURES), encoding="utf-8", newline="\n")
    print(f"wrote {target}")
