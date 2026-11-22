"""One container for everything a service needs, so tests can swap any edge for a fake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from kuckuc.config import Settings
from kuckuc.ports import (
    Blobs,
    Clock,
    EventBus,
    FixedClock,
    MemoryBlobs,
    MemoryBus,
    MemoryNotifier,
    MemoryQueue,
    Notifier,
    TaskQueue,
)
from kuckuc.store import MemoryStore, Store

if TYPE_CHECKING:
    from kuckuc.speech.interpret import Interpreter
    from kuckuc.speech.tts import Tts
    from kuckuc.telephony.base import Telephony, Verify


@dataclass
class Deps:
    """Services read these at request time, never at start-up, so a test can replace one mid-flight."""

    settings: Settings
    store: Store
    bus: EventBus
    queue: TaskQueue
    blobs: Blobs
    notifier: Notifier
    clock: Clock
    telephony: Telephony | None = None
    tts: Tts | None = None
    interpreter: Interpreter | None = None
    verify: Verify | None = None


class LogNotifier:
    """Used when no Telegram bot is configured. Nothing reaches the family, so every notice is an
    error in the log, and the episode's timeline says it was not delivered."""

    def send(self, owner_uid: str, key: str, params: dict[str, str], urgent: bool) -> bool:
        import logging

        logging.getLogger(__name__).error(
            "family notice %s (urgent=%s) NOT delivered: no Telegram bot is configured", key, urgent
        )
        return False


def tts_voices(settings: Settings) -> dict[str, str]:
    """The voice for each call language, as "<voice name>@<speaking rate>"."""
    return {
        "sr": f"{settings.tts_voice_sr}@{settings.tts_rate_sr}",
        "en": f"{settings.tts_voice_en}@{settings.tts_rate_en}",
        "de": f"{settings.tts_voice_de}@{settings.tts_rate_de}",
    }


def build_cloud_deps(settings: Settings, role: Literal["engine", "voice", "web", "job"]) -> Deps:
    """Real Google Cloud and Twilio edges. Each service builds only what it uses, which keeps
    cold starts short and lets every service account hold only the permissions its code needs."""
    import httpx
    from google.cloud import firestore, pubsub_v1, storage, tasks_v2

    from kuckuc.cloud import CloudTasksQueue, GcsBlobs, GoogleTasksClient, PubSubBus
    from kuckuc.ports import SystemClock
    from kuckuc.store_firestore import FirestoreStore

    clock = SystemClock()
    deps = Deps(
        settings=settings,
        store=FirestoreStore(firestore.Client(project=settings.project), now=clock.now),
        bus=PubSubBus(pubsub_v1.PublisherClient(), f"projects/{settings.project}/topics/events"),
        queue=CloudTasksQueue(
            GoogleTasksClient(tasks_v2.CloudTasksClient()),
            project=settings.project,
            region=settings.region,
            voice_url=settings.voice_public_url,
            engine_url=settings.engine_url,
            invoker=settings.invoker_email,
            clock=clock,
        )
        if role == "engine"  # only the engine queues calls and timers
        else None,
        blobs=GcsBlobs(storage.Client(project=settings.project).bucket(settings.bucket)),
        notifier=LogNotifier(),
        clock=clock,
    )
    if role == "engine" and settings.telegram_bot_token:
        from kuckuc.engine.notify import TelegramNotifier, telegram_post

        post = telegram_post(httpx.Client(timeout=3))  # three tries stay near 12 s, so a step stays short
        deps.notifier = TelegramNotifier(deps.store, settings.telegram_bot_token, post)
    if role in ("voice", "web"):
        from kuckuc.telephony.twilio_provider import TwilioTelephony, TwilioVerify

        http = httpx.Client(timeout=10)
        sid, token = settings.twilio_account_sid, settings.twilio_auth_token
        from kuckuc.web.demo_limits import CachedBalance

        # Each gets only its own part: voice dials and never verifies; the public web service verifies
        # numbers for the demo and never dials.
        if role == "voice":
            deps.telephony = TwilioTelephony(sid, token, settings.twilio_from_number, http=http)
        else:
            # The demo checks the account balance on every start; ask the provider at most every 5 minutes.
            deps.verify = CachedBalance(TwilioVerify(sid, token, settings.twilio_verify_sid, http=http), clock)
    if role == "voice":
        from kuckuc.cloud import gemini_generate, google_tts_synth
        from kuckuc.speech.interpret import GeminiInterpreter
        from kuckuc.speech.tts import CachedTts

        deps.tts = CachedTts(deps.blobs, google_tts_synth(settings), tts_voices(settings))
        deps.interpreter = GeminiInterpreter(gemini_generate(settings))
    return deps


def build_memory_deps(now: datetime | None = None) -> Deps:
    from kuckuc.models import OWN_PAYER, Payer

    clock = FixedClock(now or datetime.now(UTC))
    store = MemoryStore(now=clock.now)
    # Locally the service's own account has money for calls, as the owner sets it in the cloud.
    store.put_payer(Payer(id=OWN_PAYER, name="Kuc-kuc", month_cap_usd=1000.0))
    return Deps(
        settings=Settings(_env_file=None),
        store=store,
        bus=MemoryBus(),
        queue=MemoryQueue(clock),
        blobs=MemoryBlobs(),
        notifier=MemoryNotifier(),
        clock=clock,
    )
