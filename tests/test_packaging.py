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


def test_the_image_installs_exactly_the_versions_the_tests_ran_with():
    # Without pins every release installs whatever is newest that day, which the tests never saw.
    import re

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = {re.split(r"[<>=!\[ ;]", d, maxsplit=1)[0].lower() for d in config["project"]["dependencies"]}
    pins = {}
    for line in (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, _, version = line.partition("==")
            pins[name.strip().lower().replace("_", "-")] = version.strip()
    assert all(pins.get(n.replace("_", "-")) for n in names), sorted(n for n in names if not pins.get(n))
    assert "-c constraints.txt" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_a_release_checks_for_a_live_call_right_before_it_swaps_voice_and_engine():
    # The check used to run before the build, and a call could start in the minutes the build takes.
    script = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    build = script.index("gcloud builds submit")
    for service in ("voice", "engine"):
        swap = script.index(f"run_deploy {service} ")
        guard = script.rindex("guard_live_calls", 0, swap)
        assert build < guard < swap, service
