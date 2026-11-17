"""The container installs the package for real (not in editable mode), so every file the site
reads at run time has to be listed as package data. A template that is missing there works
on a developer's machine and fails in the cloud."""

import tomllib
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "kuckuc"


def test_every_template_and_static_file_is_shipped_in_the_package():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["kuckuc"]
    needed = [p for folder in ("web/templates", "web/static") for p in (PACKAGE / folder).rglob("*") if p.is_file()]
    assert needed, "no templates or static files found"
    missing = [
        str(p.relative_to(PACKAGE).as_posix())
        for p in needed
        if not any(fnmatch(p.relative_to(PACKAGE).as_posix(), pattern) for pattern in patterns)
    ]
    assert missing == []
