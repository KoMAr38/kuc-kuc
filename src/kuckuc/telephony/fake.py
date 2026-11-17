"""Telephony that rings nobody. For tests and local preview."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class PlacedCall:
    to: str
    twiml_url: str
    status_url: str
    ring_timeout_s: int
    time_limit_s: int
    sid: str


class FakeTelephony:
    def __init__(self, recording: bytes = b"\x00" * 9000, valid: bool = True) -> None:
        self.recording = recording
        self.valid = valid
        self.placed: list[PlacedCall] = []
        self.fetched: list[str] = []
        self.deleted: list[str] = []
        # Statuses the local system reports for the next placed calls ("no-answer", "busy", ...).
        self.script_status: list[str] = []

    def place_call(
        self, to: str, twiml_url: str, status_url: str, ring_timeout_s: int = 25, time_limit_s: int = 60
    ) -> str:
        sid = f"CAfake{len(self.placed) + 1}"
        self.placed.append(PlacedCall(to, twiml_url, status_url, ring_timeout_s, time_limit_s, sid))
        return sid

    def fetch_recording(self, recording_url: str) -> bytes:
        self.fetched.append(recording_url)
        return self.recording

    def delete_recording(self, recording_url: str) -> None:
        self.deleted.append(recording_url)

    def valid_signature(self, url: str, params: dict[str, str], signature: str) -> bool:
        return self.valid


class FakeVerify:
    def __init__(self, code: str = "123456", balance: float = 50.0) -> None:
        self.code = code
        self.balance = balance
        self.started: list[tuple[str, str]] = []

    def start(self, phone: str, channel: Literal["sms", "call"]) -> None:
        self.started.append((phone, channel))

    def check(self, phone: str, code: str) -> bool:
        return code == self.code

    def balance_usd(self) -> float:
        return self.balance
