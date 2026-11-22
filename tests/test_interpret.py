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
    i = GeminiInterpreter(generate=lambda a, p, t=None: calls.append(1) or {})
    assert i.interpret(b"\x00" * 100, "sr", Question.WELLBEING).answer == Answer.UNCLEAR
    assert calls == []


def test_model_error_is_unclear():
    def boom(a, p, t=None):
        raise TimeoutError()

    out = GeminiInterpreter(generate=boom).interpret(LONG, "sr", Question.WELLBEING)
    assert out.answer == Answer.UNCLEAR and out.transcript == ""


def test_model_returning_garbage_is_unclear():
    out = GeminiInterpreter(generate=lambda a, p, t=None: "not json").interpret(LONG, "sr", Question.WELLBEING)
    assert out.answer == Answer.UNCLEAR and out.transcript == ""


def test_clear_answer_carries_the_transcript():
    reply = {"human": True, "label": "needs_help", "transcript": "Nije mi dobro, pala sam."}
    out = GeminiInterpreter(generate=lambda a, p, t=None: reply).interpret(LONG, "sr", Question.WELLBEING)
    assert (out.answer, out.transcript) == (Answer.NEEDS_HELP, "Nije mi dobro, pala sam.")


def test_model_receives_the_audio_it_was_given():
    seen = []
    GeminiInterpreter(generate=lambda a, p, t=None: seen.append(a) or {}).interpret(LONG, "en", Question.YES_NO)
    assert seen == [LONG]


def test_prompt_names_language_and_labels():
    seen = {}
    GeminiInterpreter(generate=lambda a, p, t=None: seen.setdefault("p", p) and {}).interpret(LONG, "sr", Question.YES_NO)
    assert "Serbian" in seen["p"] and '"yes"' in seen["p"] and '"no"' in seen["p"] and '"unclear"' in seen["p"]
    assert '"needs_help"' not in seen["p"]


def test_wellbeing_prompt_offers_only_wellbeing_labels():
    seen = {}
    GeminiInterpreter(generate=lambda a, p, t=None: seen.setdefault("p", p) and {}).interpret(LONG, "en", Question.WELLBEING)
    assert "English" in seen["p"] and '"ok"' in seen["p"] and '"needs_help"' in seen["p"] and '"yes"' not in seen["p"]


def test_fake_interpreter_hands_out_answers_in_order_then_unclear():
    fake = FakeInterpreter([Answer.YES, Answer.OK])
    got = [fake.interpret(LONG, "sr", q).answer for q in (Question.YES_NO, Question.NEIGHBOR_REPORT, Question.WELLBEING)]
    assert got == [Answer.YES, Answer.OK, Answer.UNCLEAR]
    assert [c[2] for c in fake.calls] == [Question.YES_NO, Question.NEIGHBOR_REPORT, Question.WELLBEING]


def test_a_model_that_fails_once_is_asked_again_before_the_answer_counts_as_unclear():
    # A Vertex AI gateway timeout once turned a spoken answer into "unclear".
    attempts = []

    def flaky(audio, prompt, t=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("504 Gateway Timeout")
        return {"human": True, "label": "ok", "transcript": "Dobro sam."}

    out = GeminiInterpreter(generate=flaky).interpret(LONG, "sr", Question.WELLBEING)
    assert out.answer == Answer.OK and out.transcript == "Dobro sam." and len(attempts) == 2


def test_a_model_that_keeps_failing_is_unclear_after_two_tries():
    attempts = []

    def down(audio, prompt, t=None):
        attempts.append(1)
        raise RuntimeError("504 Gateway Timeout")

    assert GeminiInterpreter(generate=down).interpret(LONG, "sr", Question.WELLBEING).answer == Answer.UNCLEAR
    assert len(attempts) == 2


def test_a_clipped_answer_under_a_quarter_second_is_asked_again_not_guessed():
    # Measured 17 Nov: a clipped 0.13 s "Da" was judged "no" by the model 2 times out of 3, while
    # real answers that day all lasted half a second or more. Asking again is the safe choice.
    calls = []
    out = GeminiInterpreter(generate=lambda a, p, t=None: calls.append(1) or {}).interpret(b"\x00" * 2000, "sr", Question.YES_NO)
    assert out.answer == Answer.UNCLEAR and calls == []


def test_the_yes_no_instructions_name_the_serbian_answers_and_ask_for_unclear_when_cut_off():
    # With the old instructions "Da, hvala." came back "unclear" 2 times out of 3; with these, "yes" 3 of 3.
    from kuckuc.speech.interpret import build_prompt

    prompt = build_prompt("sr", Question.YES_NO)
    assert '"da, hvala"' in prompt and '"ne, hvala"' in prompt
    assert 'too short to tell "da" from "ne"' in prompt


def test_the_first_try_gets_most_of_the_time_and_the_second_what_is_left():
    # Measured 17 Nov: the model usually answers in 2-3 s, at times in 5-9 s. Two fixed 5 s tries
    # cut off answers that were on their way; the budget for both stays the same.
    from kuckuc.speech.interpret import FIRST_TRY_S, MODEL_BUDGET_S

    now, given = [0.0], []

    def slow_then_ok(audio, prompt, timeout_s):
        given.append(timeout_s)
        if len(given) == 1:
            now[0] += timeout_s  # the first try runs out of time
            raise TimeoutError()
        return {"human": True, "label": "ok", "transcript": "Dobro sam."}

    out = GeminiInterpreter(generate=slow_then_ok, clock=lambda: now[0]).interpret(LONG, "sr", Question.WELLBEING)
    assert out.answer == Answer.OK
    assert given == [FIRST_TRY_S, MODEL_BUDGET_S - FIRST_TRY_S]


def test_a_first_try_that_fails_at_once_leaves_the_second_almost_the_whole_budget():
    from kuckuc.speech.interpret import MODEL_BUDGET_S

    now, given = [0.0], []

    def error_then_ok(audio, prompt, timeout_s):
        given.append(timeout_s)
        now[0] += 0.5
        if len(given) == 1:
            raise RuntimeError("500")
        return {"human": True, "label": "ok", "transcript": "Dobro sam."}

    GeminiInterpreter(generate=error_then_ok, clock=lambda: now[0]).interpret(LONG, "sr", Question.WELLBEING)
    assert given[1] == MODEL_BUDGET_S - 0.5


# Fingerprints of the Serbian and English instructions measured on real audio on 17 Nov 2026. A change
# to any of them needs a new measurement first (see the note above _LABEL_MEANING in interpret.py).
MEASURED = {
    ("sr", Question.WELLBEING): "b1010974ad65189ed387bb4a92cd87c76b02614c88731017d7d6fb7d440238b3",
    ("sr", Question.YES_NO): "1d2154f933cdc226a24cda7a199b86dc1bec1c55a889a9c2cd3fa9e12daf5f4c",
    ("sr", Question.NEIGHBOR_REPORT): "37af7517d8ccfd32369c759c7cde7e25e74d9d40931b4012bf30160681164547",
    ("en", Question.WELLBEING): "8a6653f54194785e8cd8997ee3cc74202fa62e0e6b6eba3dbef506404fbfe4c7",
    ("en", Question.YES_NO): "40b6acadab08906828112fac1498a8054f99a24f222275b0e0bc65b3ca6b2b49",
    ("en", Question.NEIGHBOR_REPORT): "f2fa8d991658eb811b78f0df23d47764ac1bba76d402b0c4a26bf11f0b800651",
}


@pytest.mark.parametrize("lang,question", sorted(MEASURED))
def test_the_measured_serbian_and_english_instructions_are_unchanged(lang, question):
    import hashlib

    from kuckuc.speech.interpret import build_prompt

    assert hashlib.sha256(build_prompt(lang, question).encode()).hexdigest() == MEASURED[(lang, question)]


def test_german_calls_get_german_yes_and_no_examples():
    from kuckuc.speech.interpret import build_prompt

    yes_no = build_prompt("de", Question.YES_NO)
    assert "German" in yes_no and '"ja"' in yes_no and '"nein"' in yes_no and '"nein, danke"' in yes_no
    assert "Serbian" not in yes_no and '"da"' not in yes_no
    for question in Question:
        assert "expected to answer in German" in build_prompt("de", question)
