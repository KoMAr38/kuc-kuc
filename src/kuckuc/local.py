"""All services wired together in memory, with a clock you move by hand.

Used by the tests and by the local preview. Nothing here reaches a network: telephony,
speech and the family's messages are fakes that record what they were asked to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kuckuc.deps import Deps, build_memory_deps
from kuckuc.engine.app import create_engine_app, handle_event
from kuckuc.ports import FixedClock, MemoryQueue
from kuckuc.speech.interpret import FakeInterpreter
from kuckuc.speech.tts import FakeTts
from kuckuc.telephony.fake import FakeTelephony, FakeVerify
from kuckuc.voice.app import create_voice_app
from kuckuc.web.app import create_web_app

_SIGNED = {"X-Twilio-Signature": "local"}


@dataclass
class LocalSystem:
    deps: Deps
    engine: FastAPI
    voice: FastAPI
    web: FastAPI | None
    clock: FixedClock
    _engine_client: TestClient = field(init=False, repr=False)
    _voice_client: TestClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._engine_client = TestClient(self.engine)
        self._voice_client = TestClient(self.voice)

    def run_tasks(self, advance_s: int = 0) -> int:
        """Move the clock forward, then run every queued task whose time has come,
        including tasks that become due while doing so. Returns how many ran."""
        self.clock.advance(advance_s)
        ran = 0
        while True:
            self.deps.bus.redeliver()  # what the engine asked to come back later, as Pub/Sub would
            now = self.clock.now()
            due = [t for t in self.deps.queue.tasks if t.at <= now]
            if not due:
                return ran
            task = min(due, key=lambda t: t.at)
            self.deps.queue.tasks.remove(task)
            if task.kind == "call":
                placed_before = len(self.deps.telephony.placed)
                self._voice_client.post("/calls/start", json={"call_id": task.id})
                rang = len(self.deps.telephony.placed) > placed_before
                if rang and self.deps.telephony.script_status:
                    status = self.deps.telephony.script_status.pop(0)
                    self._twilio(f"/status/{task.id}", {"CallStatus": status})
            else:
                self._engine_client.post("/timers", json={"episode_id": task.id})
            ran += 1

    def deps_with_auto_tasks(self) -> Deps:
        """The same system, except that a queued task runs by itself as soon as its time has come,
        the way Cloud Tasks would run it. For code that waits for episodes to finish on their own."""
        system = self

        class AutoQueue(MemoryQueue):
            def enqueue_call(self, call_id: str, delay_s: int, sim: bool = False) -> None:
                super().enqueue_call(call_id, delay_s, sim)
                system.run_tasks(0)

            def enqueue_due(self, episode_id: str, at: datetime) -> None:
                super().enqueue_due(episode_id, at)
                system.run_tasks(0)

        queue = AutoQueue(self.clock)
        queue.tasks = self.deps.queue.tasks
        self.deps.queue = queue
        return self.deps

    def answer_next_call(self) -> None:
        """Pick up the call that is ringing and let the prepared answer be heard
        (a second time too, if the service asks again)."""
        self.run_tasks(0)
        call_id = self.ringing_call_id()
        if call_id is None:
            raise AssertionError("no call is ringing")
        self._twilio(f"/twiml/{call_id}")
        reply = self._twilio(f"/recording/{call_id}?n=1", {"RecordingUrl": f"https://fake.invalid/{call_id}-1"})
        if "n=2" in reply.text:
            self._twilio(f"/recording/{call_id}?n=2", {"RecordingUrl": f"https://fake.invalid/{call_id}-2"})

    def pending_task_delays(self) -> list[int]:
        now = self.clock.now()
        return [int((t.at - now).total_seconds()) for t in self.deps.queue.tasks]

    def ringing_call_id(self) -> str | None:
        for placed in reversed(self.deps.telephony.placed):
            call_id = placed.twiml_url.rsplit("/", 1)[-1]
            call = self.deps.store.get_call(call_id)
            if call is not None and call.status == "placed":
                return call_id
        return None

    def _twilio(self, path: str, data: dict | None = None):
        return self._voice_client.post(path, data=data or {}, headers=_SIGNED)


class _Stopwatch:
    """The voice service's waits pass at once here: a nap moves this stopwatch, not the wall clock."""

    def __init__(self) -> None:
        self.t = 0.0

    def sleep(self, seconds: float) -> None:
        self.t += seconds

    def monotonic(self) -> float:
        return self.t


def _local_sign_in(credential: str, client_id: str) -> dict:
    """Local preview and tests have no Google: whatever is typed as the credential is the email."""
    return {"sub": f"local-{credential}", "email": credential, "email_verified": True}


def _local_http_get(url: str, params: dict) -> dict:
    """Stands in for Open-Meteo: one made-up place for any search, and three mild days of weather."""
    if "geocoding" in url:
        name = str(params.get("name", "")).strip().title() or "Testgrad"
        return {
            "results": [
                {"name": name, "admin1": "Local preview", "country": "Nowhere", "latitude": 44.8, "longitude": 20.5}
            ]
        }
    today = datetime.now(UTC).date()
    times = [f"{today + timedelta(days=d)}T{h:02d}:00" for d in range(3) for h in range(24)]
    n = len(times)
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [8.0] * n,
            "apparent_temperature": [6.0] * n,
            "wind_gusts_10m": [25.0] * n,
            "snowfall": [0.0] * n,
        }
    }


def build_local(now: datetime | None = None) -> LocalSystem:
    deps = build_memory_deps(now)
    deps.telephony = FakeTelephony()
    deps.tts = FakeTts()
    deps.interpreter = FakeInterpreter()
    deps.verify = FakeVerify()
    deps.bus.subscribers.append(lambda event_id, type, data: handle_event(deps, event_id, type, data))
    stopwatch = _Stopwatch()
    return LocalSystem(
        deps=deps,
        engine=create_engine_app(deps),
        voice=create_voice_app(
            deps, verify_oidc=lambda request: True, sleep=stopwatch.sleep, monotonic=stopwatch.monotonic
        ),
        web=create_web_app(deps, verify_credential=_local_sign_in, http_get=_local_http_get),
        clock=deps.clock,
    )
