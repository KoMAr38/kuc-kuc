"""The telephone network behind one interface: Twilio in production, a fake in tests,
and no network at all for simulated calls."""

from __future__ import annotations

from typing import Literal, Protocol


class Telephony(Protocol):
    def place_call(
        self, to: str, twiml_url: str, status_url: str, ring_timeout_s: int = 25, time_limit_s: int = 60
    ) -> str:
        """Start an outbound call and return the provider's id for it."""
        ...

    def fetch_recording(self, recording_url: str) -> bytes:
        """The recording as WAV. Retries briefly, because a recording is not always ready at once."""
        ...

    def delete_recording(self, recording_url: str) -> None: ...

    def valid_signature(self, url: str, params: dict[str, str], signature: str) -> bool: ...


class Verify(Protocol):
    def start(self, phone: str, channel: Literal["sms", "call"]) -> None: ...
    def check(self, phone: str, code: str) -> bool: ...
    def balance_usd(self) -> float: ...
