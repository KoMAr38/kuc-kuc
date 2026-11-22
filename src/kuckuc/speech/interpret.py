"""Turning a few recorded seconds into an answer.

The rule that matters: any doubt is a problem. A failure here can make the service send a
neighbour for nothing, but it can never make it report "fine" when nobody said so.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from kuckuc.models import Answer, Lang, Question

log = logging.getLogger(__name__)

# A quarter of a second of 8 kHz, 16-bit telephone audio. Shorter is a click, or an answer cut off so
# short that the model cannot tell "da" from "ne": on 17 Nov a clipped 0.13 s "Da" was judged "no"
# 2 times out of 3, while every real answer that day lasted half a second or more. It is asked again.
MIN_AUDIO_BYTES = 4000
# Both tries of the model together get MODEL_BUDGET_S: the first up to FIRST_TRY_S, the second what
# is left, at least SECOND_TRY_MIN_S. The budget plus fetching the recording fits inside the hang-up
# wait and the provider's 15 s wait (voice/app.py). Measured 17 Nov: the model answers in 2-3 s,
# at times in 5-9 s; two fixed 5 s tries cut off answers that were on their way.
MODEL_BUDGET_S = 10.0  # leaves room for connecting, which a time limit does not count
FIRST_TRY_S = 7.0
SECOND_TRY_MIN_S = 2.0
MAX_TRANSCRIPT_CHARS = 300

ALLOWED: dict[Question, dict[str, Answer]] = {
    Question.WELLBEING: {"ok": Answer.OK, "needs_help": Answer.NEEDS_HELP},
    Question.YES_NO: {"yes": Answer.YES, "no": Answer.NO},
    Question.NEIGHBOR_REPORT: {"ok": Answer.OK, "needs_help": Answer.NEEDS_HELP},
}


@dataclass(frozen=True)
class Interpretation:
    answer: Answer
    transcript: str


class Interpreter(Protocol):
    def interpret(self, audio: bytes, lang: Lang, question: Question) -> Interpretation: ...


def to_answer(question: Question, raw: object) -> Answer:
    """Map the model's reply to an answer. Only a live human giving an allowed label counts."""
    if not isinstance(raw, dict) or raw.get("human") is not True:
        return Answer.UNCLEAR
    label = raw.get("label")
    if not isinstance(label, str):
        return Answer.UNCLEAR
    return ALLOWED[question].get(label, Answer.UNCLEAR)


_LANGUAGES = {"sr": "Serbian", "en": "English", "de": "German"}

_ASKED = {
    Question.WELLBEING: 'They were asked: "Are you okay?"',
    Question.YES_NO: "They were asked a question to be answered with yes or no.",
    Question.NEIGHBOR_REPORT: (
        "They are a neighbour who went to check on an older person, and were asked: "
        '"Did you see them? Say: fine, or: needs help."'
    ),
}

_LABEL_MEANING = {
    Question.WELLBEING: (
        '- "ok": they say they are fine. A plain yes counts.\n'
        '- "needs_help": they say they are not fine, or mention any trouble. A plain no counts.'
    ),
    # Measured 17 Nov on the same recordings: with "they clearly agree" alone, "Da, hvala." came back
    # "unclear" 2 times out of 3 and a clipped "Da" "no" 2 of 3; with these lines, "yes" 3 of 3 and
    # never "no".
    Question.YES_NO: (
        '- "yes": they agree. In Serbian "da", "može", "hoću", "važi", "naravno", also with thanks '
        '("da, hvala"), are yes.\n'
        '- "no": they decline. In Serbian "ne", "neću", "ne može", "ne treba", also with thanks '
        '("ne, hvala"), are no.\n'
        "One short word is enough when it is clearly heard. If the recording is cut off or too short "
        'to tell "da" from "ne", choose "unclear".'
    ),
    Question.NEIGHBOR_REPORT: (
        '- "ok": the neighbour saw the person and reports they are fine.\n'
        '- "needs_help": the person needs help, is unwell, did not open the door or could not be found.'
    ),
}


# German calls get German examples in place of the Serbian ones. Not yet measured on real German voices:
# measure before a German person is called, as on 17 Nov for Serbian. Serbian and English stay as they are.
_LABEL_MEANING_DE = _LABEL_MEANING | {
    Question.YES_NO: (
        '- "yes": they agree. In German "ja", "gerne", "jawohl", "freilich", "natürlich", "in Ordnung", '
        'also with thanks ("ja, danke"), are yes.\n'
        '- "no": they decline. In German "nein", "nee", "lieber nicht", "nicht nötig", also with thanks '
        '("nein, danke"), are no.\n'
        "One short word is enough when it is clearly heard. If the recording is cut off or too short "
        'to tell "ja" from "nein", choose "unclear".'
    ),
}


def build_prompt(lang: Lang, question: Question) -> str:
    meanings = _LABEL_MEANING_DE if lang == "de" else _LABEL_MEANING
    return (
        "You will hear a short telephone recording, at most 8 seconds long. "
        f"The speaker was called by an automated welfare-check service and is expected to answer in {_LANGUAGES[lang]}.\n"
        f"{_ASKED[question]}\n\n"
        'Reply with JSON only: {"human": true or false, "label": "...", "transcript": "..."}\n\n'
        '"human" is true only when a live person is speaking on the line.\n\n'
        "Labels:\n"
        f"{meanings[question]}\n"
        '- "unclear": a recorded message, a different person asking who is calling, background noise, '
        "silence, or anything that does not answer the question.\n\n"
        "Any mention of pain, a fall, being ill, being cold, not feeling well, or asking for someone to come "
        "means the person needs help, even if polite words surround it.\n"
        'If you are not sure, choose "unclear". Never guess that someone is fine.\n\n'
        '"transcript" is what was said, in the language it was said, in at most 200 characters.'
    )


class GeminiInterpreter:
    """``generate(audio_wav, prompt, timeout_s)`` returns the model's JSON reply as a dict. It is
    injected so the fail-safe logic here can be tested without the model, and so the model can be swapped."""

    def __init__(self, generate: Callable[[bytes, str, float], dict], clock: Callable[[], float] = time.monotonic) -> None:
        self._generate = generate
        self._clock = clock

    def interpret(self, audio: bytes, lang: Lang, question: Question) -> Interpretation:
        if len(audio) < MIN_AUDIO_BYTES:
            return Interpretation(Answer.UNCLEAR, "")
        prompt = build_prompt(lang, question)
        raw = None
        started = self._clock()
        for attempt in (1, 2):  # a passing timeout must not decide an answer; both fit the provider's wait
            if attempt == 1:
                timeout = FIRST_TRY_S
            else:
                timeout = max(SECOND_TRY_MIN_S, MODEL_BUDGET_S - (self._clock() - started))
            try:
                raw = self._generate(audio, prompt, timeout)
                break
            except Exception:
                if attempt == 2:
                    log.exception("interpretation failed twice; treating the answer as unclear")
                    return Interpretation(Answer.UNCLEAR, "")
                log.warning("interpretation failed once; asking again")
        if not isinstance(raw, dict):
            return Interpretation(Answer.UNCLEAR, "")
        transcript = raw.get("transcript")
        return Interpretation(
            answer=to_answer(question, raw),
            transcript=transcript[:MAX_TRANSCRIPT_CHARS] if isinstance(transcript, str) else "",
        )


class FakeInterpreter:
    """Hands out prepared answers in order, then "unclear". For tests and local preview."""

    def __init__(self, answers: list[Answer] | None = None) -> None:
        self.answers: list[Answer] = list(answers or [])
        self.calls: list[tuple[int, Lang, Question]] = []

    def interpret(self, audio: bytes, lang: Lang, question: Question) -> Interpretation:
        self.calls.append((len(audio), lang, question))
        answer = self.answers.pop(0) if self.answers else Answer.UNCLEAR
        return Interpretation(answer, "")
