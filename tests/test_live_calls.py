"""The check deploy.sh runs before replacing a server that a running call depends on."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location("live_calls", Path(__file__).resolve().parent.parent / "scripts" / "live_calls.py")
live_calls_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live_calls_module)
live_calls = live_calls_module.live_calls

NOW = datetime(2026, 11, 17, 19, 24, tzinfo=UTC)


def test_a_call_that_is_ringing_or_talking_counts_as_live():
    assert live_calls([{"status": "placed", "provider": "twilio", "created_at": (NOW - timedelta(minutes=1)).isoformat()}], NOW) == 1


def test_finished_simulated_and_long_lost_calls_do_not_stop_a_release():
    rows = [
        {"status": "completed", "provider": "twilio", "created_at": NOW.isoformat()},
        {"status": "placed", "provider": "simulated", "created_at": NOW.isoformat()},
        {"status": "placed", "provider": "twilio", "created_at": (NOW - timedelta(hours=2)).isoformat()},
    ]
    assert live_calls(rows, NOW) == 0


def test_a_call_queued_long_before_it_rang_counts_from_when_it_was_placed():
    # The question to the neighbour is queued 20 minutes ahead; it is live while it rings.
    row = {
        "status": "placed",
        "provider": "twilio",
        "created_at": (NOW - timedelta(minutes=21)).isoformat(),
        "placed_at": (NOW - timedelta(minutes=1)).isoformat(),
    }
    assert live_calls([row], NOW) == 1


def test_a_failure_to_look_is_not_reported_as_a_live_call(monkeypatch):
    # Exit 1 means "a call is live" and makes the release wait; missing credentials must not look like that.
    import google.cloud.firestore as firestore

    def no_credentials(*args, **kwargs):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(firestore, "Client", no_credentials)
    assert live_calls_module.main() == 2
