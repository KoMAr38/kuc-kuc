"""Google Cloud behind the small interfaces in ``ports``: Pub/Sub, Cloud Tasks, Cloud Storage,
and the check that a request really comes from our own task queue."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from kuckuc.ports import Clock

log = logging.getLogger(__name__)


class PubSubBus:
    """Publishes ``{"type", "data"}`` as JSON. The message id doubles as the event id."""

    def __init__(self, publisher, topic_path: str) -> None:
        self._publisher = publisher
        self._topic = topic_path

    def publish(self, type: str, data: dict) -> str:
        payload = json.dumps({"type": type, "data": data}).encode()
        return str(self._publisher.publish(self._topic, payload).result(timeout=10))


class CloudTasksQueue:
    """Delayed work. ``client.create(parent, url, body, schedule_time, oidc_email, audience)``
    is a thin wrapper over the Cloud Tasks API (see ``GoogleTasksClient``)."""

    def __init__(
        self, client, project: str, region: str, voice_url: str, engine_url: str, invoker: str, clock: Clock
    ) -> None:
        self.client = client
        self._queues = f"projects/{project}/locations/{region}/queues"
        self._voice_url = voice_url
        self._engine_url = engine_url
        self._invoker = invoker
        self._clock = clock

    def enqueue_call(self, call_id: str, delay_s: int, sim: bool = False) -> None:
        when = self._clock.now() + timedelta(seconds=delay_s) if delay_s > 0 else None
        self.client.create(
            parent=f"{self._queues}/{'calls-sim' if sim else 'calls'}",
            url=f"{self._voice_url}/calls/start",
            body={"call_id": call_id},
            schedule_time=when,
            oidc_email=self._invoker,
            audience=self._voice_url,
        )

    def enqueue_due(self, episode_id: str, at: datetime) -> None:
        self.client.create(
            parent=f"{self._queues}/timers",
            url=f"{self._engine_url}/timers",
            body={"episode_id": episode_id},
            schedule_time=at,
            oidc_email=self._invoker,
            audience=self._engine_url,
        )


class GoogleTasksClient:
    """The one place that speaks the Cloud Tasks API."""

    def __init__(self, tasks_client) -> None:
        self._client = tasks_client

    def create(
        self, parent: str, url: str, body: dict, schedule_time: datetime | None, oidc_email: str, audience: str
    ) -> None:
        from google.cloud import tasks_v2
        from google.protobuf import timestamp_pb2

        task = tasks_v2.Task(
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=url,
                headers={"Content-Type": "application/json"},
                body=json.dumps(body).encode(),
                oidc_token=tasks_v2.OidcToken(service_account_email=oidc_email, audience=audience),
            )
        )
        if schedule_time is not None:
            stamp = timestamp_pb2.Timestamp()
            stamp.FromDatetime(schedule_time)
            task.schedule_time = stamp
        self._client.create_task(parent=parent, task=task)


class GcsBlobs:
    def __init__(self, bucket) -> None:
        self._bucket = bucket

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._bucket.blob(key).upload_from_string(data, content_type=content_type)

    def get(self, key: str) -> bytes | None:
        blob = self._bucket.blob(key)
        return blob.download_as_bytes() if blob.exists() else None

    def exists(self, key: str) -> bool:
        return bool(self._bucket.blob(key).exists())


def gemini_generate(settings) -> Callable[[bytes, str], dict]:
    """``generate(audio_wav, prompt)`` for GeminiInterpreter: audio in, a small JSON object out."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=settings.project,
        location=settings.gemini_location,
        http_options=types.HttpOptions(timeout=8000),  # milliseconds
    )
    config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_json_schema={
            "type": "object",
            "properties": {
                "human": {"type": "boolean"},
                "label": {"type": "string"},
                "transcript": {"type": "string"},
            },
            "required": ["human", "label", "transcript"],
        },
        thinking_config=types.ThinkingConfig(thinking_level=settings.gemini_thinking_level),
    )

    def generate(audio: bytes, prompt: str) -> dict:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[types.Part.from_bytes(data=audio, mime_type="audio/wav"), prompt],
            config=config,
        )
        return json.loads(response.text)

    return generate


def google_tts_synth(settings) -> Callable[[str, str], bytes]:
    """``synth(text, voice)`` for CachedTts. ``voice`` is "<voice name>@<speaking rate>",
    so a change of pace gets its own cache entry."""
    from google.cloud import texttospeech

    client = texttospeech.TextToSpeechClient(client_options={"api_endpoint": settings.tts_endpoint})

    def synth(text: str, voice: str) -> bytes:
        name, _, rate = voice.partition("@")
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(language_code="-".join(name.split("-")[:2]), name=name),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3, speaking_rate=float(rate or 1.0)
            ),
            timeout=15,
        )
        return response.audio_content

    return synth


def google_id_token_decoder() -> Callable[[str, str], dict]:
    """Verifies a Google-signed identity token and returns its claims."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    transport = google_requests.Request()
    return lambda token, audience: id_token.verify_oauth2_token(token, transport, audience=audience)


def verify_cloud_tasks_oidc(
    request, audience: str, allowed_email: str, decode: Callable[[str, str], dict]
) -> bool:
    """True only for a request carrying a valid identity token of our own invoker account."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    try:
        claims = decode(token, audience)
    except Exception:
        log.warning("request with an invalid identity token refused")
        return False
    return claims.get("email") == allowed_email and claims.get("email_verified") is True
