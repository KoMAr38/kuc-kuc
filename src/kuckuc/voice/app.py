"""The voice service: the only code that touches the telephone network.

One call is: say a few sentences, record up to 8 seconds, understand the answer, say goodbye.
Whatever goes wrong on the way, the phone company always gets valid instructions back and the
answer is never better than what was actually heard. Logs carry call ids, never phone numbers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from xml.sax.saxutils import escape, quoteattr

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Request, Response

from kuckuc.deps import Deps
from kuckuc.models import Answer, CallRecord
from kuckuc.telephony.twilio_provider import TelephonyError
from kuckuc.scripts import closing, neutral_closing, params_from_dict, reask, render

log = logging.getLogger(__name__)

RECORD_MAX_S = 8
RECORD_SILENCE_S = 3
NOT_REACHED = frozenset({"busy", "no-answer", "failed", "canceled"})
# A person who answers and puts the receiver straight down produces two reports at once:
# the recording and the hang-up. The hang-up waits this long for the recording to be understood:
# longer than fetching it plus two tries of the model, shorter than the provider's own wait.
HANGUP_GRACE_S = 14
# How long Twilio waits for our reply to a webhook before it plays its own error message, in English.
PROVIDER_WAIT_S = 15
# About eight rings: an older person may need that long to reach a landline across the house.
RING_TIMEOUT_S = 40
# A demo visitor holds their phone; a long ring would only make them wait through each retry.
DEMO_RING_TIMEOUT_S = 25


def create_voice_app(
    deps: Deps, verify_oidc: Callable[[Request], bool], sleep: Callable[[float], None] = time.sleep
) -> FastAPI:
    app = FastAPI(title="kuc-kuc voice", docs_url=None, redoc_url=None, openapi_url=None)

    def public(path: str) -> str:
        return f"{deps.settings.voice_public_url}{path}"

    async def twilio_form(request: Request) -> dict[str, str]:
        """The posted fields, once the request is proven to come from the phone company."""
        params = {key: str(value) for key, value in (await request.form()).items()}
        url = public(request.url.path) + (f"?{request.url.query}" if request.url.query else "")
        signature = request.headers.get("x-twilio-signature", "")
        if not signature or not deps.telephony.valid_signature(url, params, signature):
            raise HTTPException(status_code=403)
        return params

    def finish(
        call: CallRecord,
        answer: Answer,
        transcript: str | None,
        recording_key: str | None,
        timeline: tuple[str, dict[str, str]] | None = None,
    ) -> bool:
        """Record the outcome and tell the engine, exactly once per call.
        The timeline row is written first, so it reads in order with what the engine does next.
        When this try heard nothing, what an earlier try of the same call heard is kept."""
        stored = deps.store.get_call(call.id)
        if stored is not None:
            transcript = transcript or stored.transcript
            recording_key = recording_key or stored.recording_key
        if not deps.store.complete_call(call.id, answer, transcript, recording_key):
            return False
        if timeline:
            deps.store.add_timeline(call.episode_id, timeline[0], timeline[1])
        deps.bus.publish(
            "call.completed",
            {
                "call_id": call.id,
                "episode_id": call.episode_id,
                "target": call.target.value,
                "script": call.script.value,
                "answer": answer.value,
            },
        )
        log.info("call %s finished: %s", call.id, answer.value)
        return True

    def block(call: CallRecord, reason: str) -> None:
        deps.store.patch_call(call.id, status="blocked")
        if reason != "refused":
            deps.store.add_timeline(call.episode_id, "call_blocked", {"role": call.target.value, "reason": reason})
        deps.bus.publish("call.blocked", {"call_id": call.id, "episode_id": call.episode_id, "reason": reason})
        log.warning("call %s blocked: %s", call.id, reason)

    def audio_url(text: str, call: CallRecord) -> str:
        return public("/" + deps.tts.audio_key(text, call.lang))

    def record_verbs(call_id: str, n: int) -> list[str]:
        action = public(f"/recording/{call_id}?n={n}")
        return [
            f"<Record action={quoteattr(action)} method=\"POST\" maxLength=\"{RECORD_MAX_S}\" "
            f"timeout=\"{RECORD_SILENCE_S}\" playBeep=\"false\" trim=\"trim-silence\"/>",
            # Reached only when nothing at all was recorded.
            f"<Redirect method=\"POST\">{escape(action + '&empty=1')}</Redirect>",
        ]

    def goodbye(call: CallRecord, answer: Answer | None) -> Response:
        """The closing sentence for a freshly understood answer; ``None`` says goodbye and promises nothing."""
        try:
            if answer is None:
                text = neutral_closing(call.lang)
            else:
                text = closing(call.script, answer, call.lang, params_from_dict(call.params))
            return _twiml(_play(audio_url(text, call)), "<Hangup/>")
        except Exception:
            log.exception("call %s: closing audio unavailable", call.id)
            return _twiml("<Hangup/>")

    @app.post("/calls/start")
    def start_call(request: Request, call_id: str = Body(embed=True)) -> dict:
        if not verify_oidc(request):
            raise HTTPException(status_code=403)
        call = deps.store.get_call(call_id)
        if call is None or call.status != "queued":
            return {"ok": True, "skipped": True}
        if deps.store.get_runtime().kill_switch:
            block(call, "kill_switch")
            return {"ok": True}
        if call.provider == "simulated":
            prepared = call.params.get("sim_answer", "")
            finish(call, Answer(prepared) if prepared in set(Answer) else Answer.UNCLEAR, None, None)
            return {"ok": True}

        # Synthesise every sentence this call can say before the phone rings,
        # so the person never waits in silence for speech to be made.
        try:
            p = params_from_dict(call.params)
            texts = {render(call.script, call.lang, p), reask(call.question, call.lang)}
            texts |= {closing(call.script, answer, call.lang, p) for answer in Answer}
            for text in sorted(texts):
                deps.tts.audio_key(text, call.lang)
        except Exception:
            # A passing failure: the call did not go through, and is tried again like any such call.
            log.exception("call %s: speech synthesis failed", call.id)
            finish(call, Answer.NO_ANSWER, None, None, ("call_failed", {"role": call.target.value}))
            return {"ok": True}

        # A task delivered twice (the queue retries when this takes long) must ring once.
        if not deps.store.start_dialing(call.id, deps.clock.now()):
            return {"ok": True, "skipped": True}

        try:
            sid = deps.telephony.place_call(
                to=call.to,
                twiml_url=public(f"/twiml/{call.id}"),
                status_url=public(f"/status/{call.id}"),
                ring_timeout_s=DEMO_RING_TIMEOUT_S if call.episode_id.startswith("demo-") else RING_TIMEOUT_S,
                time_limit_s=60,
            )
        except Exception as exc:
            # Our own TelephonyError holds only the HTTP status and the provider's error code, so it is
            # logged; any other error's text could quote the dialled number, so only its type is.
            reason = str(exc) if isinstance(exc, TelephonyError) else type(exc).__name__
            log.error("call %s could not be placed (%s)", call.id, reason)
            if isinstance(exc, TelephonyError) and exc.refused:
                # The phone never rang: say so, rather than "no answer", and do not try a refusal again.
                deps.store.add_timeline(call.episode_id, "call_refused", {"role": call.target.value})
                block(call, "refused")
                return {"ok": True}
            if isinstance(exc, TelephonyError) and exc.ours:
                # Our own account or request was refused: not the number's fault, and not "no answer".
                block(call, "provider_error")
                return {"ok": True}
            finish(call, Answer.NO_ANSWER, None, None, ("call_failed", {"role": call.target.value}))
            return {"ok": True}
        deps.store.patch_call(call.id, status="placed", provider_sid=sid)  # placed_at was set on dialling
        deps.store.add_timeline(call.episode_id, "call_placed", {"role": call.target.value})
        log.info("call %s placed", call.id)
        return {"ok": True}

    @app.post("/twiml/{call_id}")
    def on_answer(call_id: str, params: dict = Depends(twilio_form)) -> Response:
        call = deps.store.get_call(call_id)
        if call is None:
            return _twiml("<Hangup/>")
        try:
            opening = audio_url(render(call.script, call.lang, params_from_dict(call.params)), call)
        except Exception:
            log.exception("call %s: opening audio unavailable", call.id)
            return _twiml("<Hangup/>")
        deps.store.add_timeline(call.episode_id, "call_answered", {"role": call.target.value})
        log.info("call %s answered", call.id)
        return _twiml(_play(opening), *record_verbs(call.id, 1))

    @app.post("/recording/{call_id}")
    def on_recording(
        background: BackgroundTasks, call_id: str, n: int = 1, empty: int = 0, params: dict = Depends(twilio_form)
    ) -> Response:
        call = deps.store.get_call(call_id)
        if call is None:
            return _twiml("<Hangup/>")
        if call.status == "completed":
            return goodbye(call, None)
        if not deps.store.claim_recording(call.id, n):
            # The same answer reached another server too (the provider retried, for instance during a
            # release). That server handles it; this one only says goodbye and promises nothing.
            log.warning("call %s: recording %d already being handled elsewhere", call.id, n)
            return goodbye(call, None)

        answer, transcript, recording_key = Answer.UNCLEAR, "", None
        if not empty:
            try:
                recording_url = params["RecordingUrl"]
                wav = deps.telephony.fetch_recording(recording_url)
                key = f"recordings/{call.id}-{n}.wav"
                deps.blobs.put(key, wav, "audio/wav")
                recording_key = key
                # Our copy is stored; the provider's is deleted once the answer is handled and replied to.
                background.add_task(deps.telephony.delete_recording, recording_url)
                heard = deps.interpreter.interpret(wav, call.lang, call.question)
                answer, transcript = heard.answer, heard.transcript
            except Exception as exc:
                log.error("call %s: recording could not be understood (%s)", call.id, type(exc).__name__)
                answer, transcript = Answer.UNCLEAR, ""

        if answer == Answer.UNCLEAR and n == 1:
            # Keep what was heard: if the call ends without a second answer, the family can still hear this one.
            deps.store.patch_call(call.id, transcript=transcript or None, recording_key=recording_key)
            try:
                again = audio_url(reask(call.question, call.lang), call)
                return _twiml(_play(again), *record_verbs(call.id, 2))
            except Exception:
                log.exception("call %s: re-ask audio unavailable", call.id)

        heard_row = (
            "answer_interpreted",
            {"role": call.target.value, "answer": answer.value, "transcript": transcript},
        )
        if finish(call, answer, transcript or None, recording_key, heard_row):
            return goodbye(call, answer)
        return goodbye(call, None)  # the hang-up report got there first

    @app.post("/status/{call_id}")
    def on_status(call_id: str, params: dict = Depends(twilio_form)) -> dict:
        call = deps.store.get_call(call_id)
        status = params.get("CallStatus", "")
        if call is None or call.status == "completed":
            return {"ok": True}
        role = {"role": call.target.value}
        if status in NOT_REACHED:
            finish(call, Answer.NO_ANSWER, None, None, ("call_missed", role))
        elif status == "completed":
            waited = 0
            while waited < HANGUP_GRACE_S:
                sleep(1)
                waited += 1
                current = deps.store.get_call(call_id)
                if current is not None and current.status == "completed":
                    return {"ok": True}
            # The line closed and no answer was understood.
            finish(call, Answer.UNCLEAR, None, None, ("call_hung_up", role))
        return {"ok": True}

    @app.get("/audio/{name}")
    def audio(name: str) -> Response:
        data = deps.blobs.get(f"audio/{name}") if name.endswith(".mp3") and "/" not in name else None
        if data is None:
            raise HTTPException(status_code=404)
        return Response(content=data, media_type="audio/mpeg")

    return app


def _twiml(*verbs: str) -> Response:
    body = '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(verbs) + "</Response>"
    return Response(content=body, media_type="text/xml")


def _play(url: str) -> str:
    return f"<Play>{escape(url)}</Play>"
