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


def test_the_release_guard_waits_as_long_as_a_call_counts_as_live_and_stops_on_any_other_failure():
    import re

    script = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    guard = script[script.index("guard_live_calls() {") : script.index("\n}\n", script.index("guard_live_calls() {"))]
    tries = int(re.search(r"seq 1 (\d+)", guard).group(1))
    wait = int(re.search(r"sleep (\d+)", guard).group(1))
    import importlib.util

    spec = importlib.util.spec_from_file_location("live_calls", ROOT / "scripts" / "live_calls.py")
    live_calls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live_calls)
    assert tries * wait >= live_calls.LIVE_FOR.total_seconds()
    assert "-ne 1 ]" in guard  # anything but "a call is live" stops the release at once


def test_voice_keeps_its_old_secret_until_its_new_revision_is_running():
    # Revoking first breaks the old revision's cold starts if the release then stops at the guard.
    script = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    assert script.index("run_deploy voice ") < script.index("remove-iam-policy-binding twilio-verify-sid")


def test_the_server_never_uses_the_one_library_the_pins_cannot_cover():
    # uvloop is installed only on Linux, so a pip freeze on Windows cannot pin it; the asyncio loop needs no pin.
    script = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    assert "--loop,asyncio" in script



def test_a_release_with_changes_not_committed_to_git_is_refused_or_named_so():
    # The version's name is the commit's: changes not committed would run under a name that hides them.
    script = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    check = script.index("git diff --quiet HEAD")
    assert script.index('TAG="$(git rev-parse') < check < script.index("gcloud builds submit")
    assert "ALLOW_DIRTY" in script and "-dirty-" in script
