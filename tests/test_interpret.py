import pytest

from kuckuc.models import Answer, Question
from kuckuc.speech.interpret import FakeInterpreter, GeminiInterpreter, to_answer

LONG = b"\x00" * 9000


@pytest.mark.parametrize(
    "q,raw,expected",
    [
        (Question.WELLBEING, {"human": True, "label": "ok"}, Answer.OK),
        (Question.WELLBEING, {"human": True, "label": "needs_help"}, Answer.NEEDS_HELP),
        (Question.WELLBEING, {"human": False, "label": "ok"}, Answer.UNCLEAR),
        (Question.WELLBEING, {"human": True, "label": "yes"}, Answer.UNCLEAR),
        (Question.WELLBEING, {"human": True}, Answer.UNCLEAR),
        (Question.WELLBEING, {"human": True, "label": "unclear"}, Answer.UNCLEAR),
        (Question.WELLBEING, {"human": "true", "label": "ok"}, Answer.UNCLEAR),
        (Question.WELLBEING, {"label": "ok"}, Answer.UNCLEAR),
        (Question.WELLBEING, None, Answer.UNCLEAR),
        (Question.WELLBEING, ["ok"], Answer.UNCLEAR),
        (Question.YES_NO, {"human": True, "label": "yes"}, Answer.YES),
        (Question.YES_NO, {"human": True, "label": "no"}, Answer.NO),
        (Question.YES_NO, {"human": True, "label": "ok"}, Answer.UNCLEAR),
        (Question.NEIGHBOR_REPORT, {"human": True, "label": "ok"}, Answer.OK),
        (Question.NEIGHBOR_REPORT, {"human": True, "label": "needs_help"}, Answer.NEEDS_HELP),
        (Question.NEIGHBOR_REPORT, {"human": True, "label": "yes"}, Answer.UNCLEAR),
    ],
)
def test_to_answer(q, raw, expected):
    assert to_answer(q, raw) == expected


def test_short_audio_is_unclear_without_calling_model():
    calls = []
    i = GeminiInterpreter(generate=lambda a, p: calls.append(1) or {})
    assert i.interpret(b"\x00" * 100, "sr", Question.WELLBEING).answer == Answer.UNCLEAR
    assert calls == []


def test_model_error_is_unclear():
    def boom(a, p):
        raise TimeoutError()

    out = GeminiInterpreter(generate=boom).interpret(LONG, "sr", Question.WELLBEING)
    assert out.answer == Answer.UNCLEAR and out.human is False


def test_model_returning_garbage_is_unclear():
    out = GeminiInterpreter(generate=lambda a, p: "not json").interpret(LONG, "sr", Question.WELLBEING)
    assert out.answer == Answer.UNCLEAR and out.transcript == ""


def test_clear_answer_carries_the_transcript():
    reply = {"human": True, "label": "needs_help", "transcript": "Nije mi dobro, pala sam."}
    out = GeminiInterpreter(generate=lambda a, p: reply).interpret(LONG, "sr", Question.WELLBEING)
    assert (out.human, out.answer, out.transcript) == (True, Answer.NEEDS_HELP, "Nije mi dobro, pala sam.")


def test_model_receives_the_audio_it_was_given():
    seen = []
    GeminiInterpreter(generate=lambda a, p: seen.append(a) or {}).interpret(LONG, "en", Question.YES_NO)
    assert seen == [LONG]


def test_prompt_names_language_and_labels():
    seen = {}
    GeminiInterpreter(generate=lambda a, p: seen.setdefault("p", p) and {}).interpret(LONG, "sr", Question.YES_NO)
    assert "Serbian" in seen["p"] and '"yes"' in seen["p"] and '"no"' in seen["p"] and '"unclear"' in seen["p"]
    assert '"needs_help"' not in seen["p"]


def test_wellbeing_prompt_offers_only_wellbeing_labels():
    seen = {}
    GeminiInterpreter(generate=lambda a, p: seen.setdefault("p", p) and {}).interpret(LONG, "en", Question.WELLBEING)
    assert "English" in seen["p"] and '"ok"' in seen["p"] and '"needs_help"' in seen["p"] and '"yes"' not in seen["p"]


def test_fake_interpreter_hands_out_answers_in_order_then_unclear():
    fake = FakeInterpreter([Answer.YES, Answer.OK])
    got = [fake.interpret(LONG, "sr", q).answer for q in (Question.YES_NO, Question.NEIGHBOR_REPORT, Question.WELLBEING)]
    assert got == [Answer.YES, Answer.OK, Answer.UNCLEAR]
    assert [c[2] for c in fake.calls] == [Question.YES_NO, Question.NEIGHBOR_REPORT, Question.WELLBEING]
