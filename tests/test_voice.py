import logging
from dataclasses import dataclass, field
from xml.etree import ElementTree

import pytest
from factories import PERSON_PHONE, T0, make_call, make_episode
from fastapi.testclient import TestClient

from kuckuc.deps import Deps, build_memory_deps
from kuckuc.models import Answer
from kuckuc.speech.interpret import FakeInterpreter
from kuckuc.speech.tts import FakeTts
from kuckuc.telephony.fake import FakeTelephony
from kuckuc.voice.app import create_voice_app

BASE = "http://localhost:8082"  # Settings.voice_public_url default


class RaisingInterpreter:
    def interpret(self, audio, lang, question):
        raise RuntimeError("model down")


class RaisingTts:
    def audio_key(self, text, lang):
        raise RuntimeError("synthesis down")


@dataclass
class Voice:
    deps: Deps
    client: TestClient
    sleeps: list[float] = field(default_factory=list)
    on_sleep: object = None
    now: float = 0.0  # the service's stopwatch: moved by its naps, and by whatever a test says takes time

    def twilio_post(self, path, data=None):
        return self.client.post(path, data=data or {}, headers={"X-Twilio-Signature": "sig"})

    def published(self, type):
        return [d for t, d in self.deps.bus.published if t == type]

    def timeline(self, episode_id="e1"):
        return [(e.type, e.public) for e in self.deps.store.list_timeline(episode_id)]


def build_voice(verify_oidc=lambda r: True) -> Voice:
    deps = build_memory_deps(now=T0)
    deps.telephony = FakeTelephony()
    deps.tts = FakeTts()
    deps.interpreter = FakeInterpreter()
    deps.store.create_episode(make_episode(id="e1"))
    deps.store.create_call(make_call(id="c1"))
    deps.store.create_call(make_call(id="c2"))
    deps.store.create_call(
        make_call(id="c3", provider="simulated", params={"name": "Sim", "sim_answer": "needs_help"})
    )
    v = Voice(deps=deps, client=None)

    def sleep(seconds):
        v.sleeps.append(seconds)
        v.now += seconds
        if v.on_sleep:
            v.on_sleep()

    v.client = TestClient(create_voice_app(deps, verify_oidc=verify_oidc, sleep=sleep, monotonic=lambda: v.now))
    return v


@pytest.fixture
def voice() -> Voice:
    return build_voice()


def xml(text):
    return ElementTree.fromstring(text)


# --- starting a call -------------------------------------------------------------------------


def test_a_demo_call_rings_for_less_time_so_a_visitor_does_not_wait_minutes(voice):
    voice.deps.store.create_episode(make_episode(id="demo-e9"))
    voice.deps.store.create_call(make_call(id="demo-c9", episode_id="demo-e9"))
    assert voice.client.post("/calls/start", json={"call_id": "demo-c9"}).status_code == 200
    assert voice.deps.telephony.placed[0].ring_timeout_s == 25


def test_start_places_call_and_marks_placed(voice):
    assert voice.client.post("/calls/start", json={"call_id": "c1"}).status_code == 200
    placed = voice.deps.telephony.placed[0]
    assert placed.to == "+15005550006"
    assert placed.twiml_url == f"{BASE}/twiml/c1" and placed.status_url == f"{BASE}/status/c1"
    # About eight rings: an older person may need that long to reach a landline across the house.
    assert (placed.ring_timeout_s, placed.time_limit_s) == (40, 60)
    call = voice.deps.store.get_call("c1")
    assert call.status == "placed" and call.provider_sid == placed.sid
    assert voice.timeline() == [("call_placed", {"role": "person"})]


def test_start_requires_oidc():
    v = build_voice(verify_oidc=lambda r: False)
    assert v.client.post("/calls/start", json={"call_id": "c1"}).status_code == 403
    assert v.deps.telephony.placed == []


def test_start_twice_places_one_call(voice):
    voice.client.post("/calls/start", json={"call_id": "c1"})
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert len(voice.deps.telephony.placed) == 1


def test_start_for_unknown_call_does_nothing(voice):
    assert voice.client.post("/calls/start", json={"call_id": "nope"}).status_code == 200
    assert voice.deps.telephony.placed == [] and voice.deps.bus.published == []


def test_kill_switch_blocks_and_publishes(voice):
    voice.deps.store.runtime.kill_switch = True
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert voice.deps.telephony.placed == []
    assert voice.deps.bus.published[-1] == (
        "call.blocked",
        {"call_id": "c1", "episode_id": "e1", "reason": "kill_switch"},
    )
    assert voice.deps.store.get_call("c1").status == "blocked"


def test_everything_the_call_might_say_is_synthesised_before_the_phone_rings(voice):
    voice.client.post("/calls/start", json={"call_id": "c1"})
    said = {text for text, lang in voice.deps.tts.spoken}
    assert "Dobar dan, baba Milena. Ovde Kuc-kuc. Danas je veoma hladno. Zovem da proverim: jeste li dobro?" in said
    assert "Nisam vas razumela. Recite da ili ne: jeste li dobro?" in said
    assert {
        "Hvala, baba Milena. Čuvajte se. Doviđenja.",
        "Javiću komšiji da svrati do vas. Doviđenja.",
        "Hvala. Doviđenja.",
    } <= said
    assert {lang for _, lang in voice.deps.tts.spoken} == {"sr"}


def test_a_call_the_phone_company_refuses_counts_as_no_answer(voice):
    def refuse(*args, **kwargs):
        raise RuntimeError("provider said no")

    voice.deps.telephony.place_call = refuse
    assert voice.client.post("/calls/start", json={"call_id": "c1"}).status_code == 200
    assert voice.deps.store.get_call("c1").answer == Answer.NO_ANSWER
    assert voice.published("call.completed")[0]["answer"] == "no_answer"


def test_a_refused_call_logs_the_phone_companys_reason_code_but_never_the_number(voice, caplog):
    # Without the code nobody could tell that the network refused the number, rather than nobody answering.
    from kuckuc.telephony.twilio_provider import TelephonyError

    def refuse(*args, **kwargs):
        raise TelephonyError("call not placed: HTTP 400, provider code 21215")

    voice.deps.telephony.place_call = refuse
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert "provider code 21215" in caplog.text and PERSON_PHONE not in caplog.text


def test_a_number_the_network_refuses_is_reported_at_once_not_retried(voice):
    # Retrying a refused number only repeats the refusal, and "no answer" would be untrue.
    from kuckuc.telephony.twilio_provider import TelephonyError

    def refuse(*args, **kwargs):
        raise TelephonyError("call not placed: HTTP 400, provider code 21215", status=400, code="21215")

    voice.deps.telephony.place_call = refuse
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert voice.deps.store.get_call("c1").status == "blocked"
    assert voice.published("call.blocked")[0]["reason"] == "refused" and voice.published("call.completed") == []
    assert "call_refused" in [e.type for e in voice.deps.store.list_timeline("e1")]


def test_a_passing_network_failure_still_counts_as_no_answer_and_is_tried_again(voice):
    from kuckuc.telephony.twilio_provider import TelephonyError

    def busy(*args, **kwargs):
        raise TelephonyError("call not placed: HTTP 503", status=503)

    voice.deps.telephony.place_call = busy
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert voice.published("call.completed")[0]["answer"] == "no_answer"


def test_simulated_call_completes_without_telephony(voice):
    voice.client.post("/calls/start", json={"call_id": "c3"})
    assert voice.deps.store.get_call("c3").answer == Answer.NEEDS_HELP and voice.deps.telephony.placed == []
    assert voice.published("call.completed") == [
        {"call_id": "c3", "episode_id": "e1", "target": "person", "script": "check", "answer": "needs_help"}
    ]
    assert voice.deps.tts.spoken == []


# --- when the phone is picked up -------------------------------------------------------------


def test_twiml_plays_then_records_8_seconds(voice):
    voice.client.post("/calls/start", json={"call_id": "c1"})
    body = voice.twilio_post("/twiml/c1").text
    assert "<Play>" in body and 'maxLength="8"' in body and 'playBeep="false"' in body and "empty=1" in body
    root = xml(body)
    assert [el.tag for el in root] == ["Play", "Record", "Redirect"]
    assert root[0].text.startswith(f"{BASE}/audio/") and root[0].text.endswith(".mp3")
    assert root[1].attrib["action"] == f"{BASE}/recording/c1?n=1"
    assert root[1].attrib["timeout"] == "3"
    assert root[2].text == f"{BASE}/recording/c1?n=1&empty=1"
    assert ("call_answered", {"role": "person"}) in voice.timeline()


def test_bad_signature_rejected(voice):
    voice.deps.telephony.valid = False
    assert voice.client.post("/twiml/c1").status_code == 403
    assert voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"}).status_code == 403
    assert voice.twilio_post("/status/c1", {"CallStatus": "completed"}).status_code == 403
    assert voice.deps.store.get_call("c1").status == "queued"


def test_signature_is_checked_against_the_public_url_and_the_posted_fields(voice):
    seen = []
    voice.deps.telephony.valid_signature = lambda url, params, sig: seen.append((url, params, sig)) or True
    voice.twilio_post("/recording/c1?n=2&empty=1", {"CallSid": "CA1"})
    assert seen == [(f"{BASE}/recording/c1?n=2&empty=1", {"CallSid": "CA1"}, "sig")]


def test_twiml_for_unknown_call_just_hangs_up(voice):
    r = voice.twilio_post("/twiml/nope")
    assert r.status_code == 200 and [el.tag for el in xml(r.text)] == ["Hangup"]


def test_storage_failure_when_the_phone_is_picked_up_hangs_up_cleanly(voice):
    voice.deps.tts = RaisingTts()
    r = voice.twilio_post("/twiml/c1")
    assert r.status_code == 200 and [el.tag for el in xml(r.text)] == ["Hangup"]


# --- the recorded answer ---------------------------------------------------------------------


def test_ok_answer_completes_once_and_says_take_care(voice):
    voice.deps.interpreter.answers = [Answer.OK]
    body = voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"}).text
    assert "<Hangup" in body and [el.tag for el in xml(body)] == ["Play", "Hangup"]
    assert ("Hvala, baba Milena. Čuvajte se. Doviđenja.", "sr") in voice.deps.tts.spoken
    assert voice.published("call.completed") == [
        {"call_id": "c1", "episode_id": "e1", "target": "person", "script": "check", "answer": "ok"}
    ]
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    voice.twilio_post("/status/c1", {"CallStatus": "completed"})
    assert [t for t, _ in voice.deps.bus.published].count("call.completed") == 1
    assert voice.deps.store.get_call("c1").answer == Answer.OK


def test_a_repeated_recording_report_gets_only_the_neutral_goodbye(voice):
    # The person said they are fine. A duplicate webhook must not make the call say
    # "I'll ask your neighbour to stop by".
    voice.deps.interpreter.answers = [Answer.OK]
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    before = len(voice.deps.tts.spoken)
    body = voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"}).text
    assert [el.tag for el in xml(body)] == ["Play", "Hangup"]
    assert voice.deps.tts.spoken[before:] == [("Hvala. Doviđenja.", "sr")]
    assert voice.deps.telephony.fetched == ["https://x/r1"]


def test_simulated_call_with_no_prepared_answer_is_unclear(voice):
    voice.deps.store.create_call(make_call(id="c4", provider="simulated", params={"name": "Sim"}))
    assert voice.client.post("/calls/start", json={"call_id": "c4"}).status_code == 200
    assert voice.deps.store.get_call("c4").answer == Answer.UNCLEAR


def test_answer_goes_on_the_timeline_with_what_was_heard(voice):
    class Heard:
        def interpret(self, audio, lang, question):
            from kuckuc.speech.interpret import Interpretation

            return Interpretation(Answer.NEEDS_HELP, "Nije mi dobro.")

    voice.deps.interpreter = Heard()
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    assert voice.timeline()[-1] == (
        "answer_interpreted",
        {"role": "person", "answer": "needs_help", "transcript": "Nije mi dobro."},
    )
    assert voice.deps.store.get_call("c1").transcript == "Nije mi dobro."
    assert ("Javiću komšiji da svrati do vas. Doviđenja.", "sr") in voice.deps.tts.spoken


def test_unclear_reasks_once_then_completes_unclear(voice):
    voice.deps.interpreter.answers = [Answer.UNCLEAR, Answer.UNCLEAR]
    first = voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"}).text
    assert "n=2" in first and [el.tag for el in xml(first)] == ["Play", "Record", "Redirect"]
    assert xml(first)[1].attrib["action"] == f"{BASE}/recording/c1?n=2"
    assert voice.deps.store.get_call("c1").answer is None
    second = voice.twilio_post("/recording/c1?n=2", {"RecordingUrl": "https://x/r2"}).text
    assert [el.tag for el in xml(second)] == ["Play", "Hangup"]
    assert voice.deps.store.get_call("c1").answer == Answer.UNCLEAR
    assert ("Nisam vas razumela. Recite da ili ne: jeste li dobro?", "sr") in voice.deps.tts.spoken


def test_clear_answer_after_the_reask_counts(voice):
    voice.deps.interpreter.answers = [Answer.UNCLEAR, Answer.OK]
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    voice.twilio_post("/recording/c1?n=2", {"RecordingUrl": "https://x/r2"})
    assert voice.deps.store.get_call("c1").answer == Answer.OK


def test_silence_after_pickup_gets_one_reask(voice):
    body = voice.twilio_post("/recording/c1?n=1&empty=1").text
    assert [el.tag for el in xml(body)] == ["Play", "Record", "Redirect"]
    assert voice.deps.store.get_call("c1").answer is None and voice.deps.telephony.fetched == []


def test_silence_is_unclear_without_fetching(voice):
    voice.twilio_post("/recording/c1?n=2&empty=1")
    assert voice.deps.store.get_call("c1").answer == Answer.UNCLEAR and voice.deps.telephony.fetched == []
    assert voice.deps.interpreter.calls == []


def test_interpreter_crash_still_returns_twiml_and_unclear(voice):
    voice.deps.interpreter = RaisingInterpreter()
    r = voice.twilio_post("/recording/c1?n=2", {"RecordingUrl": "https://x/r"})
    assert r.status_code == 200 and "<Response>" in r.text
    assert voice.deps.store.get_call("c1").answer == Answer.UNCLEAR


def test_recording_download_failure_is_unclear_not_an_error(voice):
    def broken(url):
        raise RuntimeError("download failed")

    voice.deps.telephony.fetch_recording = broken
    r = voice.twilio_post("/recording/c1?n=2", {"RecordingUrl": "https://x/r"})
    assert r.status_code == 200 and [el.tag for el in xml(r.text)] == ["Play", "Hangup"]
    assert voice.deps.store.get_call("c1").answer == Answer.UNCLEAR


def test_closing_audio_failure_still_hangs_up_and_keeps_the_answer(voice):
    voice.deps.interpreter.answers = [Answer.OK]
    voice.deps.tts = RaisingTts()
    r = voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    assert r.status_code == 200 and [el.tag for el in xml(r.text)] == ["Hangup"]
    assert voice.deps.store.get_call("c1").answer == Answer.OK


def test_recording_stored_and_deleted_at_provider(voice):
    voice.deps.interpreter.answers = [Answer.OK]
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    assert voice.deps.blobs.exists("recordings/c1-1.wav") and voice.deps.telephony.deleted == ["https://x/r1"]
    assert voice.deps.blobs.get("recordings/c1-1.wav") == voice.deps.telephony.recording
    assert voice.deps.store.get_call("c1").recording_key == "recordings/c1-1.wav"


def test_a_recording_delivered_twice_is_handled_once_and_never_turns_into_unclear(voice):
    # During a release one answer reached two servers: the first fetched it and deleted it at the
    # provider, the second found it gone and reported "unclear" first.
    assert voice.deps.store.claim_recording("c1", 1) is True  # the other server has it
    reply = voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    assert reply.status_code == 200 and "<Hangup/>" in reply.text
    assert voice.deps.telephony.fetched == [] and voice.deps.store.get_call("c1").status != "completed"
    assert voice.published("call.completed") == []


def test_the_interpreter_hears_the_recording_in_the_calls_language(voice):
    voice.deps.interpreter.answers = [Answer.OK]
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    size, lang, question = voice.deps.interpreter.calls[0]
    assert (size, lang, question.value) == (9000, "sr", "wellbeing")


# --- how the call ended ----------------------------------------------------------------------


def test_hangup_before_answer_is_unclear_and_no_answer_is_no_answer(voice):
    voice.twilio_post("/status/c1", {"CallStatus": "completed"})
    assert voice.deps.store.get_call("c1").answer == Answer.UNCLEAR
    voice.twilio_post("/status/c2", {"CallStatus": "no-answer"})
    assert voice.deps.store.get_call("c2").answer == Answer.NO_ANSWER
    assert [d["answer"] for d in voice.published("call.completed")] == ["unclear", "no_answer"]


@pytest.mark.parametrize("status", ["busy", "no-answer", "failed", "canceled"])
def test_every_way_of_not_getting_through_is_no_answer(voice, status):
    voice.twilio_post("/status/c1", {"CallStatus": status})
    assert voice.deps.store.get_call("c1").answer == Answer.NO_ANSWER
    assert voice.sleeps == []


def test_progress_statuses_change_nothing(voice):
    for status in ("initiated", "ringing", "in-progress"):
        voice.twilio_post("/status/c1", {"CallStatus": status})
    assert voice.deps.store.get_call("c1").answer is None and voice.deps.bus.published == []


def test_hanging_up_right_after_answering_waits_for_the_answer_to_be_understood(voice):
    # The person says "dobro sam" and puts the receiver down. The hang-up report can arrive
    # while the recording is still being interpreted; it must not overwrite the real answer.
    def recording_handler_finishes():
        if len(voice.sleeps) == 3:
            voice.deps.store.complete_call("c1", Answer.OK, "dobro sam", None)

    voice.on_sleep = recording_handler_finishes
    voice.twilio_post("/status/c1", {"CallStatus": "completed"})
    assert voice.deps.store.get_call("c1").answer == Answer.OK
    assert voice.published("call.completed") == []
    assert len(voice.sleeps) == 3


def test_hangup_waits_at_most_the_grace_period_before_calling_it_unclear(voice):
    from kuckuc.voice.app import HANGUP_GRACE_S

    voice.twilio_post("/status/c1", {"CallStatus": "completed"})
    assert sum(voice.sleeps) == HANGUP_GRACE_S and voice.deps.store.get_call("c1").answer == Answer.UNCLEAR


# --- audio and privacy -----------------------------------------------------------------------


def test_audio_is_served_as_mp3_and_missing_audio_is_404(voice):
    voice.deps.blobs.put("audio/abc.mp3", b"ID3data", "audio/mpeg")
    r = voice.client.get("/audio/abc.mp3")
    assert r.status_code == 200 and r.content == b"ID3data" and r.headers["content-type"] == "audio/mpeg"
    assert voice.client.get("/audio/none.mp3").status_code == 404


def test_recordings_are_not_reachable_through_the_audio_route(voice):
    voice.deps.blobs.put("recordings/c1-1.wav", b"RIFF", "audio/wav")
    assert voice.client.get("/audio/../recordings/c1-1.wav").status_code == 404


def test_logs_never_contain_the_phone_number(voice, caplog):
    caplog.set_level(logging.DEBUG)
    voice.deps.interpreter.answers = [Answer.OK]
    voice.client.post("/calls/start", json={"call_id": "c1"})
    voice.twilio_post("/twiml/c1", {"To": PERSON_PHONE, "From": "+15005550001"})
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1", "To": PERSON_PHONE})
    voice.twilio_post("/status/c1", {"CallStatus": "completed", "To": PERSON_PHONE})
    assert "5005550006" not in caplog.text
    assert "c1" in caplog.text  # the service does log; it logs call ids, not numbers


# --- audit 17 Nov: timing, failures, first answers ------------------------------------------


def test_understanding_an_answer_fits_inside_the_hang_up_wait_and_the_providers_wait():
    # Twilio waits 15 s for our reply, and a hang-up waits HANGUP_GRACE_S for the answer. Fetching the
    # recording plus two tries of the model must fit inside both, or a heard answer becomes "unclear"
    # and a person still on the line hears the provider's error message in English.
    from kuckuc.speech.interpret import FIRST_TRY_S, MODEL_BUDGET_S, SECOND_TRY_MIN_S
    from kuckuc.telephony.twilio_provider import RECORDING_RETRIES, RECORDING_RETRY_WAIT_S
    from kuckuc.voice.app import HANGUP_GRACE_S, PROVIDER_WAIT_S

    assert FIRST_TRY_S + SECOND_TRY_MIN_S <= MODEL_BUDGET_S  # both tries never take longer than the budget
    worst = MODEL_BUDGET_S + RECORDING_RETRIES * RECORDING_RETRY_WAIT_S + 1  # +1 s to fetch and store
    assert worst < HANGUP_GRACE_S < PROVIDER_WAIT_S


def test_the_providers_copy_is_deleted_only_after_the_answer_is_recorded(voice):
    # Deleting is not needed to understand the answer, so it must not delay it.
    seen = []
    voice.deps.telephony.delete_recording = lambda url: seen.append(voice.deps.store.get_call("c1").status)
    voice.deps.interpreter.answers = [Answer.OK]
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    assert seen == ["completed"]


def test_a_passing_speech_failure_counts_as_no_answer_so_the_check_goes_on(voice):
    # One failed synthesis used to close the whole check, even with a neighbour about to be called.
    voice.deps.tts = RaisingTts()
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert voice.deps.telephony.placed == [] and voice.published("call.blocked") == []
    assert voice.published("call.completed")[0]["answer"] == "no_answer"
    assert ("call_failed", {"role": "person"}) in voice.timeline()


def test_our_own_provider_account_errors_are_not_called_a_refused_number(voice):
    # A rotated token (401) is our fault: the family must not be sent to check a correct number.
    from kuckuc.telephony.twilio_provider import TelephonyError

    def unauthorised(*args, **kwargs):
        raise TelephonyError("call not placed: HTTP 401, provider code 20003", status=401, code="20003")

    voice.deps.telephony.place_call = unauthorised
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert voice.published("call.blocked")[0]["reason"] == "provider_error"
    assert ("call_blocked", {"role": "person", "reason": "provider_error"}) in voice.timeline()
    assert "call_refused" not in [type for type, _ in voice.timeline()]


def test_a_call_is_claimed_before_dialling_so_a_second_delivery_cannot_dial_again(voice):
    # The queue delivers a task again when the first delivery takes too long; both saw "queued".
    statuses = []
    original = voice.deps.telephony.place_call

    def place(*args, **kwargs):
        statuses.append(voice.deps.store.get_call("c1").status)
        assert voice.deps.store.start_dialing("c1", T0) is False  # what a second delivery would try now
        return original(*args, **kwargs)

    voice.deps.telephony.place_call = place
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert statuses == ["dialing"] and len(voice.deps.telephony.placed) == 1
    assert voice.deps.store.get_call("c1").placed_at == T0


def test_what_was_said_before_the_reask_is_kept_when_the_call_ends_unclear(voice):
    # "Halo? Ko je to?", asked again, then the receiver goes down: the family can still hear it.
    from kuckuc.speech.interpret import Interpretation

    class Heard:
        def interpret(self, audio, lang, question):
            return Interpretation(Answer.UNCLEAR, "Halo? Ko je to?")

    voice.deps.interpreter = Heard()
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    voice.twilio_post("/status/c1", {"CallStatus": "completed"})
    call = voice.deps.store.get_call("c1")
    assert (call.answer, call.transcript, call.recording_key) == (Answer.UNCLEAR, "Halo? Ko je to?", "recordings/c1-1.wav")



# --- audit 17 Nov, second round -------------------------------------------------------------


def test_a_speech_failure_never_ends_a_call_another_delivery_is_dialling(voice):
    # Two deliveries of one task: the other one claimed and dialled while this one made speech.
    class ClaimedMeanwhile:
        def audio_key(self, text, lang):
            voice.deps.store.start_dialing("c1", T0)
            raise RuntimeError("synthesis down")

    voice.deps.tts = ClaimedMeanwhile()
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert voice.published("call.completed") == [] and voice.deps.store.get_call("c1").status == "dialing"


def test_the_kill_switch_never_blocks_a_call_another_delivery_is_dialling(voice):
    voice.deps.store.runtime.kill_switch = True
    read = voice.deps.store.get_runtime

    def claimed_meanwhile():
        voice.deps.store.start_dialing("c1", T0)
        return read()

    voice.deps.store.get_runtime = claimed_meanwhile
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert voice.published("call.blocked") == [] and voice.deps.store.get_call("c1").status == "dialing"


def test_a_call_reported_finished_while_dialling_stays_finished(voice):
    # "busy" reached another server before this one wrote "placed".
    original = voice.deps.telephony.place_call

    def place_then_busy(*args, **kwargs):
        sid = original(*args, **kwargs)
        voice.deps.store.complete_call("c1", Answer.NO_ANSWER, None, None)
        return sid

    voice.deps.telephony.place_call = place_then_busy
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert voice.deps.store.get_call("c1").status == "completed"


def test_the_providers_copy_is_deleted_even_when_storing_ours_fails(voice):
    def broken(*args, **kwargs):
        raise RuntimeError("bucket unavailable")

    voice.deps.blobs.put = broken
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    assert voice.deps.telephony.deleted == ["https://x/r1"]


def test_a_recording_for_a_call_that_already_ended_is_deleted_too(voice):
    voice.deps.store.complete_call("c1", Answer.UNCLEAR, None, None)
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    assert voice.deps.telephony.deleted == ["https://x/r1"]


def test_the_transcript_kept_belongs_to_the_recording_kept(voice):
    from kuckuc.speech.interpret import Interpretation

    class FirstThenDown:
        calls = 0

        def interpret(self, audio, lang, question):
            self.calls += 1
            if self.calls == 1:
                return Interpretation(Answer.UNCLEAR, "Halo? Ko je to?")
            raise RuntimeError("model down")

    voice.deps.interpreter = FirstThenDown()
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    voice.twilio_post("/recording/c1?n=2", {"RecordingUrl": "https://x/r2"})
    call = voice.deps.store.get_call("c1")
    assert call.recording_key == "recordings/c1-2.wav" and not call.transcript


def test_when_the_opening_cannot_be_played_the_call_counts_as_failed_not_hung_up(voice):
    voice.deps.tts = RaisingTts()
    reply = voice.twilio_post("/twiml/c1")
    assert "<Hangup/>" in reply.text
    call = voice.deps.store.get_call("c1")
    assert call.answer == Answer.UNCLEAR and ("call_failed", {"role": "person"}) in voice.timeline()


def test_a_call_that_already_ended_on_our_side_only_says_goodbye_when_picked_up(voice):
    voice.deps.store.complete_call("c1", Answer.NO_ANSWER, None, None)
    body = voice.twilio_post("/twiml/c1").text
    assert "<Record" not in body and "call_answered" not in [type for type, _ in voice.timeline()]



# --- audit 17 Nov, third round --------------------------------------------------------------


def test_a_failure_inside_a_call_still_answers_the_phone_company_with_valid_instructions():
    # A 500 makes Twilio play its own error message, in English, to the person on the line.
    v = build_voice()
    v.client = TestClient(v.client.app, raise_server_exceptions=False)

    def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    v.deps.store.claim_recording = broken
    reply = v.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    assert reply.status_code == 200 and "<Hangup/>" in reply.text


def test_the_answer_reaches_the_engine_even_when_writing_the_history_row_fails(voice):
    def broken(*args, **kwargs):
        raise RuntimeError("timeline unavailable")

    voice.deps.store.add_timeline = broken
    voice.deps.interpreter.answers = [Answer.NEEDS_HELP]
    voice.twilio_post("/recording/c1?n=1", {"RecordingUrl": "https://x/r1"})
    assert [d["answer"] for d in voice.published("call.completed")] == ["needs_help"]


def test_when_a_call_ends_every_recording_of_it_is_deleted_at_the_provider(voice):
    # Sent when the line closes, a reply nobody hears; also for a call that already ended here.
    voice.twilio_post("/status/c1", {"CallStatus": "completed", "CallSid": "CA" + "1" * 32})
    voice.deps.store.complete_call("c2", Answer.OK, None, None)
    voice.twilio_post("/status/c2", {"CallStatus": "completed", "CallSid": "CA" + "2" * 32})
    assert voice.deps.telephony.swept == ["CA" + "1" * 32, "CA" + "2" * 32]



# --- audit 20 Nov, fourth round -------------------------------------------------------------


def broken_history(*args, **kwargs):
    raise RuntimeError("timeline unavailable")


def test_a_stopped_call_reaches_the_engine_even_when_its_history_row_fails(voice):
    voice.deps.store.add_timeline = broken_history
    voice.deps.store.runtime.kill_switch = True
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert voice.published("call.blocked") == [{"call_id": "c1", "episode_id": "e1", "reason": "kill_switch"}]


def test_a_refused_number_reaches_the_engine_even_when_its_history_row_fails(voice):
    from kuckuc.telephony.twilio_provider import TelephonyError

    def refuse(*args, **kwargs):
        raise TelephonyError("call not placed: HTTP 400, provider code 21215", status=400, code="21215")

    voice.deps.store.add_timeline = broken_history
    voice.deps.telephony.place_call = refuse
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert [d["reason"] for d in voice.published("call.blocked")] == ["refused"]


def test_a_placed_call_is_not_reported_as_failed_when_its_history_row_fails(voice):
    voice.deps.store.add_timeline = broken_history
    r = voice.client.post("/calls/start", json={"call_id": "c1"})
    assert r.status_code == 200 and voice.deps.store.get_call("c1").status == "placed"


def test_a_picked_up_call_is_asked_its_question_even_when_its_history_row_fails(voice):
    voice.deps.store.add_timeline = broken_history
    r = voice.twilio_post("/twiml/c1")
    assert [el.tag for el in xml(r.text)] == ["Play", "Record", "Redirect"]


def test_the_hang_up_wait_is_measured_by_the_clock_not_by_counting_naps(voice):
    # Each look at the database takes time too: counted in naps, the reply reached the provider after
    # its own 15 seconds.
    from kuckuc.voice.app import HANGUP_GRACE_S, PROVIDER_WAIT_S

    look = voice.deps.store.get_call

    def slow_look(call_id):
        voice.now += 0.1  # a database read
        return look(call_id)

    voice.deps.store.get_call = slow_look
    voice.twilio_post("/status/c1", {"CallStatus": "completed"})
    assert look("c1").answer == Answer.UNCLEAR
    assert HANGUP_GRACE_S <= voice.now < PROVIDER_WAIT_S


def test_a_request_signed_as_twilio_signs_it_is_accepted_and_a_changed_one_refused():
    import twilio.request_validator

    from kuckuc.telephony.twilio_provider import TwilioTelephony

    v = build_voice()
    v.deps.telephony = TwilioTelephony("AC1", "secret", "+15005550006", http=object())
    params = {"CallSid": "CA" + "1" * 32, "CallStatus": "ringing", "AccountSid": "AC1"}
    signature = twilio.request_validator.RequestValidator("secret").compute_signature(f"{BASE}/status/c1", params)
    signed = v.client.post("/status/c1", data=params, headers={"X-Twilio-Signature": signature})
    changed = v.client.post(
        "/status/c1", data=params | {"CallStatus": "completed"}, headers={"X-Twilio-Signature": signature}
    )
    assert (signed.status_code, changed.status_code) == (200, 403)



def test_a_failure_when_the_phone_is_picked_up_still_answers_with_valid_instructions():
    v = build_voice()
    v.client = TestClient(v.client.app, raise_server_exceptions=False)
    v.deps.store.get_call = broken_history
    reply = v.twilio_post("/twiml/c1")
    assert reply.status_code == 200 and [el.tag for el in xml(reply.text)] == ["Hangup"]


def test_a_call_reported_busy_while_still_dialling_never_gets_a_placed_row(voice):
    original = voice.deps.telephony.place_call

    def place_then_busy(*args, **kwargs):
        sid = original(*args, **kwargs)
        voice.deps.store.complete_call("c1", Answer.NO_ANSWER, None, None)  # "busy" reached another server
        return sid

    voice.deps.telephony.place_call = place_then_busy
    voice.client.post("/calls/start", json={"call_id": "c1"})
    assert "call_placed" not in [t for t, _ in voice.timeline()]
