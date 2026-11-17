"""The edges of the system as small interfaces, plus in-memory versions that record what they were given."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol


class EventBus(Protocol):
    def publish(self, type: str, data: dict) -> str:
        """Publish an event and return its id."""
        ...


class TaskQueue(Protocol):
    def enqueue_call(self, call_id: str, delay_s: int, sim: bool = False) -> None: ...
    def enqueue_due(self, episode_id: str, at: datetime) -> None: ...


class Blobs(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def exists(self, key: str) -> bool: ...


class Notifier(Protocol):
    def send(self, owner_uid: str, key: str, params: dict[str, str], urgent: bool) -> bool:
        """True only when the message reached the family."""
        ...


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: int) -> None:
        self.t = self.t + timedelta(seconds=seconds)


Subscriber = Callable[[str, str, dict], None]  # (event_id, type, data)


class MemoryBus:
    """Delivers to subscribers in publish order. An event published while another is being
    handled waits its turn, as it would on a real bus, instead of running inside the handler."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self.subscribers: list[Subscriber] = []
        self._pending: deque[tuple[str, str, dict]] = deque()
        self._draining = False

    def publish(self, type: str, data: dict) -> str:
        event_id = f"evt-{len(self.published) + 1}"
        self.published.append((type, data))
        self._pending.append((event_id, type, data))
        if not self._draining:
            self._draining = True
            try:
                while self._pending:
                    eid, etype, edata = self._pending.popleft()
                    for deliver in list(self.subscribers):
                        deliver(eid, etype, edata)
            finally:
                self._draining = False
        return event_id


@dataclass
class QueuedTask:
    kind: Literal["call", "due"]
    id: str  # call id or episode id
    at: datetime
    sim: bool = False


class MemoryQueue:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.tasks: list[QueuedTask] = []

    def enqueue_call(self, call_id: str, delay_s: int, sim: bool = False) -> None:
        self.tasks.append(QueuedTask("call", call_id, self._clock.now() + timedelta(seconds=delay_s), sim))

    def enqueue_due(self, episode_id: str, at: datetime) -> None:
        self.tasks.append(QueuedTask("due", episode_id, at))


class MemoryBlobs:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.items[key] = data
        self.content_types[key] = content_type

    def get(self, key: str) -> bytes | None:
        return self.items.get(key)

    def exists(self, key: str) -> bool:
        return key in self.items


@dataclass
class SentNotice:
    owner_uid: str
    key: str
    params: dict[str, str]
    urgent: bool


@dataclass
class MemoryNotifier:
    sent: list[SentNotice] = field(default_factory=list)

    def send(self, owner_uid: str, key: str, params: dict[str, str], urgent: bool) -> bool:
        self.sent.append(SentNotice(owner_uid, key, dict(params), urgent))
        return True
