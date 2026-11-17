"""Turning a few recorded seconds into an answer.

The rule that matters: any doubt is a problem. A failure here can make the service send a
neighbour for nothing, but it can never make it report "fine" when nobody said so.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from kuckuc.models import Answer, Lang, Question

log = logging.getLogger(__name__)

# About a quarter of a second of 8 kHz telephone audio. Anything shorter is silence or a click.
MIN_AUDIO_BYTES = 4000
MAX_TRANSCRIPT_CHARS = 300

ALLOWED: dict[Question, dict[str, Answer]] = {
    Question.WELLBEING: {"ok": Answer.OK, "needs_help": Answer.NEEDS_HELP},
    Question.YES_NO: {"yes": Answer.YES, "no": Answer.NO},
    Question.NEIGHBOR_REPORT: {"ok": Answer.OK, "needs_help": Answer.NEEDS_HELP},
}


@dataclass(frozen=True)
class Interpretation:
    human: bool
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


_LANGUAGES = {"sr": "Serbian", "en": "English"}

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
    Question.YES_NO: '- "yes": they clearly agree.\n- "no": they clearly decline.',
    Question.NEIGHBOR_REPORT: (
        '- "ok": the neighbour saw the person and reports they are fine.\n'
        '- "needs_help": the person needs help, is unwell, did not open the door or could not be found.'
    ),
}


def build_prompt(lang: Lang, question: Question) -> str:
    return (
        "You will hear a short telephone recording, at most 8 seconds long. "
        f"The speaker was called by an automated welfare-check service and is expected to answer in {_LANGUAGES[lang]}.\n"
        f"{_ASKED[question]}\n\n"
        'Reply with JSON only: {"human": true or false, "label": "...", "transcript": "..."}\n\n'
        '"human" is true only when a live person is speaking on the line.\n\n'
        "Labels:\n"
        f"{_LABEL_MEANING[question]}\n"
        '- "unclear": a recorded message, a different person asking who is calling, background noise, '
        "silence, or anything that does not answer the question.\n\n"
        "Any mention of pain, a fall, being ill, being cold, not feeling well, or asking for someone to come "
        "means the person needs help, even if polite words surround it.\n"
        'If you are not sure, choose "unclear". Never guess that someone is fine.\n\n'
        '"transcript" is what was said, in the language it was said, in at most 200 characters.'
    )


class GeminiInterpreter:
    """``generate(audio_wav, prompt)`` returns the model's JSON reply as a dict. It is injected so
    the fail-safe logic here can be tested without the model, and so the model can be swapped."""

    def __init__(self, generate: Callable[[bytes, str], dict]) -> None:
        self._generate = generate

    def interpret(self, audio: bytes, lang: Lang, question: Question) -> Interpretation:
        if len(audio) < MIN_AUDIO_BYTES:
            return Interpretation(False, Answer.UNCLEAR, "")
        try:
            raw = self._generate(audio, build_prompt(lang, question))
        except Exception:
            log.exception("interpretation failed; treating the answer as unclear")
            return Interpretation(False, Answer.UNCLEAR, "")
        if not isinstance(raw, dict):
            return Interpretation(False, Answer.UNCLEAR, "")
        transcript = raw.get("transcript")
        return Interpretation(
            human=raw.get("human") is True,
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
        return Interpretation(answer != Answer.UNCLEAR, answer, "")
