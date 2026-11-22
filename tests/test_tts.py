from kuckuc.ports import MemoryBlobs
from kuckuc.speech.tts import CachedTts, FakeTts


def test_same_text_synthesised_once():
    calls = []
    blobs = MemoryBlobs()
    tts = CachedTts(blobs, lambda t, v: calls.append((t, v)) or b"mp3", {"sr": "v-sr", "en": "v-en"})
    k1 = tts.audio_key("Dobar dan", "sr")
    k2 = tts.audio_key("Dobar dan", "sr")
    assert k1 == k2 and k1.startswith("audio/") and k1.endswith(".mp3")
    assert calls == [("Добар дан", "v-sr")]
    assert blobs.get(k1) == b"mp3"
    assert blobs.content_types[k1] == "audio/mpeg"


def test_voice_change_changes_key():
    blobs = MemoryBlobs()
    a = CachedTts(blobs, lambda t, v: b"x", {"sr": "A", "en": "A"}).audio_key("Hello", "en")
    b = CachedTts(blobs, lambda t, v: b"x", {"sr": "B", "en": "B"}).audio_key("Hello", "en")
    assert a != b


def test_different_text_gets_a_different_key_and_english_is_not_transliterated():
    calls = []
    tts = CachedTts(MemoryBlobs(), lambda t, v: calls.append((t, v)) or b"x", {"sr": "v-sr", "en": "v-en"})
    assert tts.audio_key("Hello", "en") != tts.audio_key("Goodbye", "en")
    assert calls == [("Hello", "v-en"), ("Goodbye", "v-en")]


def test_a_second_instance_reuses_audio_already_in_storage():
    blobs = MemoryBlobs()
    voices = {"sr": "v-sr", "en": "v-en"}
    key = CachedTts(blobs, lambda t, v: b"first", voices).audio_key("Hello", "en")
    calls = []
    assert CachedTts(blobs, lambda t, v: calls.append(t) or b"second", voices).audio_key("Hello", "en") == key
    assert calls == [] and blobs.get(key) == b"first"


def test_failed_synthesis_stores_nothing_and_raises():
    blobs = MemoryBlobs()

    def boom(text, voice):
        raise RuntimeError("synthesis down")

    try:
        CachedTts(blobs, boom, {"sr": "v", "en": "v"}).audio_key("Hello", "en")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the synthesis error to reach the caller")
    assert blobs.items == {}


def test_fake_tts_records_what_it_was_asked_to_say():
    fake = FakeTts()
    k1 = fake.audio_key("Dobar dan", "sr")
    k2 = fake.audio_key("Hello", "en")
    assert k1 != k2 and k1.startswith("audio/") and k1.endswith(".mp3")
    assert fake.spoken == [("Dobar dan", "sr"), ("Hello", "en")]


def test_every_call_language_has_a_voice():
    from typing import get_args

    from kuckuc.config import Settings
    from kuckuc.deps import tts_voices
    from kuckuc.models import Lang

    voices = tts_voices(Settings())
    assert set(voices) == set(get_args(Lang))
    assert voices["de"] == "de-DE-Chirp3-HD-Leda@0.9"
