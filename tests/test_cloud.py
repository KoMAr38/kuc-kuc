import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace

from factories import T0

from kuckuc.cloud import CloudTasksQueue, PubSubBus, verify_cloud_tasks_oidc
from kuckuc.ports import FixedClock

INVOKER = "invoker-sa@p.iam.gserviceaccount.com"


def req(headers):
    return SimpleNamespace(headers=headers)


@dataclass
class CreatedTask:
    parent: str
    url: str
    body: dict
    schedule_time: datetime | None
    oidc_email: str
    audience: str
    name: str | None = None


class FakeTasksClient:
    def __init__(self):
        self.created: list[CreatedTask] = []

    def create(self, parent, url, body, schedule_time, oidc_email, audience, name=None):
        self.created.append(CreatedTask(parent, url, body, schedule_time, oidc_email, audience, name))


def queue():
    return CloudTasksQueue(
        client=FakeTasksClient(),
        project="p",
        region="europe-west1",
        voice_url="https://voice",
        engine_url="https://engine",
        invoker=INVOKER,
        clock=FixedClock(T0),
    )


def test_oidc_rejects_wrong_email_and_missing_header():
    assert verify_cloud_tasks_oidc(req(headers={}), "https://voice", INVOKER, decode=lambda t, a: {}) is False
    assert (
        verify_cloud_tasks_oidc(
            req(headers={"authorization": "Bearer x"}),
            "https://voice",
            INVOKER,
            decode=lambda t, a: {"email": "other@p.iam.gserviceaccount.com", "email_verified": True},
        )
        is False
    )


def test_oidc_accepts_the_invoker_and_passes_token_and_audience_to_the_decoder():
    seen = []

    def decode(token, audience):
        seen.append((token, audience))
        return {"email": INVOKER, "email_verified": True}

    assert verify_cloud_tasks_oidc(req({"authorization": "Bearer tok123"}), "https://voice", INVOKER, decode) is True
    assert seen == [("tok123", "https://voice")]


def test_oidc_rejects_unverified_email_bad_scheme_and_invalid_token():
    ok = {"email": INVOKER, "email_verified": True}
    assert (
        verify_cloud_tasks_oidc(
            req({"authorization": "Bearer x"}), "a", INVOKER, lambda t, a: {**ok, "email_verified": False}
        )
        is False
    )
    assert verify_cloud_tasks_oidc(req({"authorization": "Basic x"}), "a", INVOKER, lambda t, a: ok) is False

    def invalid(token, audience):
        raise ValueError("bad signature")

    assert verify_cloud_tasks_oidc(req({"authorization": "Bearer x"}), "a", INVOKER, invalid) is False


def test_cloud_tasks_payload_for_delayed_call():
    q = queue()
    q.enqueue_call("c1", delay_s=600)
    task = q.client.created[0]
    assert task.parent.endswith("/queues/calls") and task.url == "https://voice/calls/start"
    assert task.schedule_time == T0 + timedelta(seconds=600) and task.oidc_email == INVOKER
    assert task.parent == "projects/p/locations/europe-west1/queues/calls"
    assert task.body == {"call_id": "c1"} and task.audience == "https://voice"
    q.enqueue_call("c2", delay_s=0, sim=True)
    assert q.client.created[1].parent.endswith("/queues/calls-sim")
    assert q.client.created[1].schedule_time is None


def test_cloud_tasks_payload_for_a_timer():
    q = queue()
    when = T0 + timedelta(hours=8)
    q.enqueue_due("p1:2026-11-20:check", when)
    task = q.client.created[0]
    assert task.parent == "projects/p/locations/europe-west1/queues/timers"
    assert (task.url, task.audience) == ("https://engine/timers", "https://engine")
    assert task.body == {"episode_id": "p1:2026-11-20:check"} and task.schedule_time == when


def test_pubsub_bus_publishes_type_and_data_and_returns_the_message_id():
    sent = []

    class Publisher:
        def publish(self, topic, data):
            sent.append((topic, data))
            return SimpleNamespace(result=lambda timeout=None: "msg-42")

    bus = PubSubBus(Publisher(), "projects/p/topics/events")
    assert bus.publish("call.completed", {"call_id": "c1", "answer": "ok"}) == "msg-42"
    topic, data = sent[0]
    assert topic == "projects/p/topics/events"
    assert json.loads(data) == {"type": "call.completed", "data": {"call_id": "c1", "answer": "ok"}}


def test_every_task_has_a_name_that_follows_from_what_it_does():
    # A retried or repeated request then creates nothing new: the queue refuses a second task of that name.
    import re

    q = queue()
    when = T0 + timedelta(hours=8)
    q.enqueue_call("c1", delay_s=600)
    q.enqueue_call("c1", delay_s=600)
    q.enqueue_due("p1:2026-11-20:check", when)
    q.enqueue_due("p1:2026-11-20:check", when)
    q.enqueue_due("p1:2026-11-20:check", when + timedelta(minutes=10))
    names = [t.name for t in q.client.created]
    assert names[0] == names[1] == "call-c1"
    assert names[2] == names[3] != names[4]
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", n) for n in names)


def test_creating_a_task_is_retried_and_a_task_that_exists_already_is_not_an_error():
    from google.api_core.exceptions import AlreadyExists

    from kuckuc.cloud import GoogleTasksClient

    seen = []

    class Api:
        def create_task(self, parent, task, retry=None, timeout=None):
            seen.append((task.name, retry))
            raise AlreadyExists("a task with this name exists")

    GoogleTasksClient(Api()).create(
        "projects/p/locations/l/queues/calls", "https://voice/calls/start", {"call_id": "c1"}, None,
        INVOKER, "https://voice", name="call-c1",
    )
    ((name, retry),) = seen
    assert name == "projects/p/locations/l/queues/calls/tasks/call-c1" and retry is not None


def test_each_service_gets_only_the_twilio_part_it_uses(monkeypatch):
    # The public web service never dials, so it holds no telephony; voice never verifies numbers.
    from types import SimpleNamespace

    import google.cloud.firestore as firestore
    import google.cloud.pubsub_v1 as pubsub
    import google.cloud.storage as storage
    import google.cloud.tasks_v2 as tasks

    import kuckuc.cloud
    from kuckuc.config import Settings
    from kuckuc.deps import build_cloud_deps
    from kuckuc.telephony.twilio_provider import TwilioTelephony

    monkeypatch.setattr(firestore, "Client", lambda **kw: SimpleNamespace())
    monkeypatch.setattr(pubsub, "PublisherClient", lambda **kw: SimpleNamespace())
    monkeypatch.setattr(storage, "Client", lambda **kw: SimpleNamespace(bucket=lambda name: SimpleNamespace()))
    monkeypatch.setattr(tasks, "CloudTasksClient", lambda **kw: SimpleNamespace())
    monkeypatch.setattr(kuckuc.cloud, "gemini_generate", lambda settings: None)
    monkeypatch.setattr(kuckuc.cloud, "google_tts_synth", lambda settings: None)
    settings = Settings(_env_file=None, project="p", twilio_account_sid="AC1", twilio_auth_token="t")
    web = build_cloud_deps(settings, "web")
    assert web.telephony is None and web.verify is not None
    voice = build_cloud_deps(settings, "voice")
    assert isinstance(voice.telephony, TwilioTelephony) and voice.verify is None
