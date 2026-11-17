from dataclasses import replace

from kuckuc.models import Answer, Question, ScriptKind
from kuckuc.scripts import (
    ScriptParams,
    closing,
    neutral_closing,
    params_from_dict,
    params_to_dict,
    question_for,
    reask,
    render,
    speakable,
    to_cyrillic,
)

P = ScriptParams(name="baba Milena", family="vaš unuk Petar", neighbor="Dragan", gender="f", hazard="cold", value=-12.4)


def test_check_sr_verbatim():
    assert render(ScriptKind.CHECK, "sr", P) == (
        "Dobar dan, baba Milena. Ovde Kuc-kuc. Danas je veoma hladno. Zovem da proverim: jeste li dobro?"
    )


def test_headsup_cold_sr_verbatim():
    assert render(ScriptKind.HEADSUP, "sr", P) == (
        "Dobar dan, baba Milena. Ovde Kuc-kuc. Sutra će biti veoma hladno, do minus 12 stepeni. "
        "Pripremite drva i ne izlazite ako ne morate. Jeste li dobro?"
    )


def test_headsup_heat_sr_speaks_the_rounded_temperature():
    hot = replace(P, hazard="heat", value=37.6)
    assert render(ScriptKind.HEADSUP, "sr", hot) == (
        "Dobar dan, baba Milena. Ovde Kuc-kuc. Sutra će biti veoma vruće, do 38 stepeni. "
        "Pijte dosta vode i ne izlazite u podne. Jeste li dobro?"
    )


def test_manual_check_has_no_hazard_sentence():
    assert render(ScriptKind.CHECK_MANUAL, "sr", P) == (
        "Dobar dan, baba Milena. Ovde Kuc-kuc. Zovem da proverim: jeste li dobro?"
    )


def test_neighbour_ask_sr_verbatim():
    assert render(ScriptKind.NEIGHBOR_ASK_SILENT, "sr", P) == (
        "Dobar dan, Dragan. Ovde Kuc-kuc. baba Milena se ne javlja na telefon, a danas je veoma hladno. "
        "Recite da ili ne: možete li da odete do nje sada?"
    )


def test_neighbour_ask_without_hazard_drops_the_weather_clause():
    calm = replace(P, hazard=None, value=None)
    assert render(ScriptKind.NEIGHBOR_ASK_SILENT, "sr", calm) == (
        "Dobar dan, Dragan. Ovde Kuc-kuc. baba Milena se ne javlja na telefon. "
        "Recite da ili ne: možete li da odete do nje sada?"
    )


def test_neighbour_ask_uses_gender():
    him = replace(P, name="deda Mile", gender="m")
    assert "odete do nje sada" in render(ScriptKind.NEIGHBOR_ASK_SILENT, "sr", P)
    assert "odete do njega sada" in render(ScriptKind.NEIGHBOR_ASK_SILENT, "sr", him)
    assert "kaže da mu nije dobro" in render(ScriptKind.NEIGHBOR_ASK_UNWELL, "sr", him)
    assert "kaže da joj nije dobro" in render(ScriptKind.NEIGHBOR_ASK_UNWELL, "sr", P)


def test_neighbour_followup_sr_verbatim():
    assert render(ScriptKind.NEIGHBOR_FOLLOWUP, "sr", P) == (
        "Dobar dan, Dragan. Recite: dobro je, ili: treba pomoć. Kako je baba Milena?"
    )


def test_intro_sr_verbatim():
    assert render(ScriptKind.INTRO_PERSON, "sr", P) == (
        "Dobar dan, baba Milena. Ovde Kuc-kuc. Ovu službu je za vas uključio vaš unuk Petar. "
        "Zvaćemo vas samo kad stiže opasno vreme, da proverimo da li ste dobro. "
        "Vaš odgovor se kratko snima da vaš unuk Petar može da ga čuje. Recite da ili ne: da li se slažete?"
    )


def test_intro_mentions_recording_and_asks_consent():
    for script in (ScriptKind.INTRO_PERSON, ScriptKind.INTRO_NEIGHBOR):
        t = render(script, "sr", P)
        assert "snima" in t and t.endswith("Recite da ili ne: da li se slažete?")


def test_neighbour_intro_greets_the_neighbour_not_the_person():
    t = render(ScriptKind.INTRO_NEIGHBOR, "sr", P)
    assert t.startswith("Dobar dan, Dragan. Ovde Kuc-kuc.") and "baba Milena" in t


def test_english_check_and_neighbour_lines():
    en = replace(P, name="Ann", neighbor="Bob", hazard="heat", value=104)
    assert render(ScriptKind.CHECK, "en", en) == (
        "Hello, Ann. This is Kuc-kuc. Today is very hot. I'm calling to check on you: are you okay?"
    )
    assert render(ScriptKind.NEIGHBOR_ASK_SILENT, "en", en) == (
        "Hello, Bob. This is Kuc-kuc. Ann isn't answering the phone, and today is very hot. "
        "Please say yes or no: can you go and check on her now?"
    )
    assert render(ScriptKind.NEIGHBOR_FOLLOWUP, "en", replace(en, gender="m")) == (
        "Hello, Bob. Please say: he is fine, or: he needs help. How is Ann?"
    )


def test_to_cyrillic_digraphs():
    assert to_cyrillic("Ljubica, Njegoš, džak, đak, čaša, šuma, žaba") == "Љубица, Његош, џак, ђак, чаша, шума, жаба"


def test_to_cyrillic_keeps_digits_and_punctuation():
    assert to_cyrillic("Kuc-kuc: do minus 12 stepeni?") == "Куц-куц: до минус 12 степени?"


def test_speakable():
    assert speakable("Dobar dan", "sr") == "Добар дан" and speakable("Hello", "en") == "Hello"


def test_question_for():
    assert question_for(ScriptKind.CHECK) == Question.WELLBEING
    assert question_for(ScriptKind.CHECK_MANUAL) == Question.WELLBEING
    assert question_for(ScriptKind.HEADSUP) == Question.WELLBEING
    assert question_for(ScriptKind.INTRO_PERSON) == Question.YES_NO
    assert question_for(ScriptKind.INTRO_NEIGHBOR) == Question.YES_NO
    assert question_for(ScriptKind.NEIGHBOR_ASK_SILENT) == Question.YES_NO
    assert question_for(ScriptKind.NEIGHBOR_ASK_UNWELL) == Question.YES_NO
    assert question_for(ScriptKind.NEIGHBOR_FOLLOWUP) == Question.NEIGHBOR_REPORT


def test_reask_matches_the_question():
    assert reask(Question.WELLBEING, "sr") == "Nisam vas razumela. Recite da ili ne: jeste li dobro?"
    assert reask(Question.YES_NO, "sr") == "Nisam vas razumela. Recite da ili ne."
    assert reask(Question.NEIGHBOR_REPORT, "sr") == "Nisam vas razumela. Recite: dobro je, ili: treba pomoć."
    assert reask(Question.WELLBEING, "en") == "Sorry, I didn't catch that. Please say yes or no: are you okay?"


def test_closing_ok_says_take_care():
    assert closing(ScriptKind.CHECK, Answer.OK, "sr", P) == "Hvala, baba Milena. Čuvajte se. Doviđenja."
    assert closing(ScriptKind.CHECK, Answer.OK, "en", replace(P, name="Ann")) == "Thank you, Ann. Take care. Goodbye."


def test_closing_never_says_take_care_unless_ok():
    assert closing(ScriptKind.CHECK, Answer.UNCLEAR, "sr", P) == "Javiću komšiji da svrati do vas. Doviđenja."
    assert closing(ScriptKind.CHECK_MANUAL, Answer.NEEDS_HELP, "sr", P) == "Javiću komšiji da svrati do vas. Doviđenja."
    assert closing(ScriptKind.NEIGHBOR_ASK_SILENT, Answer.YES, "sr", P) == "Hvala. Doviđenja."
    assert closing(ScriptKind.NEIGHBOR_FOLLOWUP, Answer.OK, "sr", P) == "Hvala. Doviđenja."


def test_neutral_closing_promises_nothing():
    assert neutral_closing("sr") == "Hvala. Doviđenja."
    assert neutral_closing("en") == "Thank you. Goodbye."


def test_headsup_closing_promises_the_neighbour_only_when_one_is_sent():
    # An unclear heads-up is retried later and never sends the neighbour (spec 5.4).
    assert closing(ScriptKind.HEADSUP, Answer.NEEDS_HELP, "sr", P) == "Javiću komšiji da svrati do vas. Doviđenja."
    assert closing(ScriptKind.HEADSUP, Answer.UNCLEAR, "sr", P) == "Hvala. Doviđenja."


def test_every_script_renders_in_both_languages():
    for s in ScriptKind:
        for lang in ("sr", "en"):
            for p in (P, replace(P, hazard=None, value=None)):
                text = render(s, lang, p)
                assert text and "{" not in text and "None" not in text


def test_params_survive_the_trip_through_a_call_record():
    assert params_from_dict(params_to_dict(P)) == P
    calm = replace(P, hazard=None, value=None)
    assert params_from_dict(params_to_dict(calm)) == calm
    assert params_from_dict(params_to_dict(P) | {"sim_answer": "ok"}) == P


def test_serbian_texts_use_the_name_only_where_its_entered_form_is_correct():
    # Names are spoken as entered ("baba Vera"). After "kod" Serbian needs another case
    # ("kod babe Vere"), so no sentence may put the name there.
    for script in ScriptKind:
        assert "kod baba" not in render(script, "sr", P), script


def test_with_no_neighbour_who_said_yes_the_person_is_never_promised_one():
    from dataclasses import replace

    alone = replace(P, has_neighbor=False)
    assert closing(ScriptKind.CHECK, Answer.NEEDS_HELP, "sr", alone) == "Javiću vašoj porodici. Doviđenja."
    assert closing(ScriptKind.CHECK, Answer.UNCLEAR, "en", alone) == "I'll let your family know. Goodbye."
    assert closing(ScriptKind.CHECK, Answer.NEEDS_HELP, "sr", P) == "Javiću komšiji da svrati do vas. Doviđenja."


def test_whether_there_is_a_neighbour_survives_storage_and_old_calls_keep_the_old_words():
    from dataclasses import replace

    assert params_from_dict(params_to_dict(replace(P, has_neighbor=False))).has_neighbor is False
    assert params_from_dict(params_to_dict(P)).has_neighbor is True
    assert params_from_dict({"name": "N"}).has_neighbor is True  # calls stored before the field existed


def test_every_call_ends_with_its_question_so_the_answer_is_recorded():
    # Recording starts when the voice stops. "Recite da ili ne: da li se slažete?" made a neighbour
    # answer "Da" during the last four words, which were not recorded.
    for lang in ("sr", "en"):
        for script in ScriptKind:
            assert render(script, lang, P).endswith("?"), (lang, script)
        assert reask(Question.WELLBEING, lang).endswith("?"), lang


def test_a_consent_answer_is_said_back_so_a_mistake_is_heard_at_once():
    assert closing(ScriptKind.INTRO_NEIGHBOR, Answer.YES, "sr", P) == "Razumela sam: da. Hvala. Doviđenja."
    assert closing(ScriptKind.INTRO_NEIGHBOR, Answer.NO, "sr", P) == "Razumela sam: ne. Hvala. Doviđenja."
    assert closing(ScriptKind.INTRO_PERSON, Answer.YES, "en", P) == "I understood: yes. Thank you. Goodbye."
    assert closing(ScriptKind.INTRO_PERSON, Answer.UNCLEAR, "sr", P) == "Hvala. Doviđenja."
