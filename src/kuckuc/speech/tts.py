"""Speech synthesis with a cache, so a sentence is paid for once and a live call never waits for it twice."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Protocol

from kuckuc.models import Lang
from kuckuc.ports import Blobs
from kuckuc.scripts import speakable


class Tts(Protocol):
    def audio_key(self, text: str, lang: Lang) -> str:
        """Storage key ("audio/<sha256>.mp3") of the spoken text, synthesising it if it is not there yet."""
        ...


class CachedTts:
    """``synth(text, voice_name)`` returns MP3 bytes. The key covers the voice and the exact text sent to it."""

    def __init__(self, blobs: Blobs, synth: Callable[[str, str], bytes], voices: dict[Lang, str]) -> None:
        self._blobs = blobs
        self._synth = synth
        self._voices = voices

    def audio_key(self, text: str, lang: Lang) -> str:
        voice = self._voices[lang]
        spoken = speakable(text, lang)
        digest = hashlib.sha256(f"{voice}\n{spoken}".encode()).hexdigest()
        key = f"audio/{digest}.mp3"
        if not self._blobs.exists(key):
            self._blobs.put(key, self._synth(spoken, voice), "audio/mpeg")
        return key


class FakeTts:
    """Makes no sound. Records what it was asked to say, for tests and local preview."""

    def __init__(self) -> None:
        self.spoken: list[tuple[str, Lang]] = []
        self._keys: dict[tuple[str, Lang], str] = {}

    def audio_key(self, text: str, lang: Lang) -> str:
        self.spoken.append((text, lang))
        return self._keys.setdefault((text, lang), f"audio/fake-{len(self._keys) + 1}.mp3")
