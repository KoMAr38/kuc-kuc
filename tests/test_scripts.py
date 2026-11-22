from dataclasses import replace

import pytest

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
DE = ScriptParams(
    name="Frau Weber", family="Ihr Enkel Peter", neighbor="Herr Braun", gender="f", hazard="cold", value=-12.4, emergency="112"
)
LANGS = ("sr", "en", "de")
NOTICE = {
    "sr": "Ovde Kuc-kuc, automatski poziv.",
    "en": "This is Kuc-kuc, an automated call.",
    "de": "Hier ist Kuc-kuc, ein automatischer Anruf.",
}


def test_check_sr_verbatim():
    assert render(ScriptKind.CHECK, "sr", P) == (
        "Dobar dan, baba Milena. Ovde Kuc-kuc, automatski poziv. Danas je veoma hladno. Zovem da proverim: jeste li dobro?"
    )


def test_headsup_cold_sr_verbatim():
    assert render(ScriptKind.HEADSUP, "sr", P) == (
        "Dobar dan, baba Milena. Ovde Kuc-kuc, automatski poziv. Sutra će biti veoma hladno, do minus 12 stepeni. "
        "Pripremite drva i ne izlazite ako ne morate. Jeste li dobro?"
    )


def test_headsup_heat_sr_speaks_the_rounded_temperature():
    hot = replace(P, hazard="heat", value=37.6)
    assert render(ScriptKind.HEADSUP, "sr", hot) == (
        "Dobar dan, baba Milena. Ovde Kuc-kuc, automatski poziv. Sutra će biti veoma vruće, do 38 stepeni. "
        "Pijte dosta vode i ne izlazite u podne. Jeste li dobro?"
    )


def test_manual_check_has_no_hazard_sentence():
    assert render(ScriptKind.CHECK_MANUAL, "sr", P) == (
        "Dobar dan, baba Milena. Ovde Kuc-kuc, automatski poziv. Zovem da proverim: jeste li dobro?"
    )


def test_neighbour_ask_sr_verbatim():
    assert render(ScriptKind.NEIGHBOR_ASK_SILENT, "sr", P) == (
        "Dobar dan, Dragan. Ovde Kuc-kuc, automatski poziv. baba Milena se ne javlja na telefon, a danas je veoma hladno. "
        "Recite da ili ne: možete li da odete do nje sada?"
    )


def test_neighbour_ask_without_hazard_drops_the_weather_clause():
    calm = replace(P, hazard=None, value=None)
    assert render(ScriptKind.NEIGHBOR_ASK_SILENT, "sr", calm) == (
        "Dobar dan, Dragan. Ovde Kuc-kuc, automatski poziv. baba Milena se ne javlja na telefon. "
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
        "Dobar dan, Dragan. Ovde Kuc-kuc, automatski poziv. Recite: dobro je, ili: treba pomoć. Kako je baba Milena?"
    )


def test_intro_sr_verbatim():
    assert render(ScriptKind.INTRO_PERSON, "sr", replace(P, emergency="194")) == (
        "Dobar dan, baba Milena. Ovde Kuc-kuc, automatski poziv. Ovu službu je za vas uključio vaš unuk Petar. "
        "Zvaćemo vas samo kad stiže opasno vreme, da proverimo da li ste dobro. "
        "Vaš odgovor procenjuje računar. Odgovor se kratko snima da vaš unuk Petar može da ga čuje, "
        "a snimak se briše posle sedam dana. "
        "Ovo nije hitna služba: u hitnom slučaju zovite sto devedeset četiri. "
        "Recite da ili ne: da li se slažete?"
    )


def test_neighbour_intro_sr_verbatim():
    assert render(ScriptKind.INTRO_NEIGHBOR, "sr", P) == (
        "Dobar dan, Dragan. Ovde Kuc-kuc, automatski poziv. "
        "Kad stiže opasno vreme, zovemo telefonom da proverimo da li je baba Milena dobro. "
        "Ako se ne javi ili joj nije dobro, pozvali bismo vas da odete do nje. "
        "Vaš odgovor procenjuje računar. Odgovor se kratko snima i briše posle sedam dana. "
        "Recite da ili ne: da li se slažete?"
    )


def test_intro_mentions_recording_and_asks_consent():
    for script in (ScriptKind.INTRO_PERSON, ScriptKind.INTRO_NEIGHBOR):
        t = render(script, "sr", P)
        assert "snima" in t and t.endswith("Recite da ili ne: da li se slažete?")


def test_neighbour_intro_greets_the_neighbour_not_the_person():
    t = render(ScriptKind.INTRO_NEIGHBOR, "sr", P)
    assert t.startswith("Dobar dan, Dragan. Ovde Kuc-kuc") and "baba Milena" in t


def test_every_call_says_it_is_automated():
    # EU AI Act, Art. 50: a person talking to an AI system is told so. Said at the start of every call.
    for lang, person in (("sr", P), ("en", P), ("de", DE)):
        for script in ScriptKind:
            assert NOTICE[lang] in render(script, lang, person), (lang, script)


def test_welcome_call_says_computer_seven_days_and_emergency_number():
    wanted = {
        "sr": ("računar", "sedam dana", "nije hitna služba", "sto devedeset četiri"),
        "en": ("computer", "seven days", "not an emergency service", "nine one one"),
        "de": ("Computer", "sieben Tagen", "kein Notrufdienst", "eins eins zwei"),
    }
    for lang, person in (("sr", replace(P, emergency="194")), ("en", replace(P, emergency="911")), ("de", DE)):
        text = render(ScriptKind.INTRO_PERSON, lang, person)
        for words in wanted[lang]:
            assert words in text, (lang, words)


def test_english_check_and_neighbour_lines():
    en = replace(P, name="Ann", neighbor="Bob", hazard="heat", value=104)
    assert render(ScriptKind.CHECK, "en", en) == (
        "Hello, Ann. This is Kuc-kuc, an automated call. Today is very hot. I'm calling to check on you: are you okay?"
    )
    assert render(ScriptKind.NEIGHBOR_ASK_SILENT, "en", en) == (
        "Hello, Bob. This is Kuc-kuc, an automated call. Ann isn't answering the phone, and today is very hot. "
        "Please say yes or no: can you go and check on her now?"
    )
    assert render(ScriptKind.NEIGHBOR_FOLLOWUP, "en", replace(en, gender="m")) == (
        "Hello, Bob. This is Kuc-kuc, an automated call. Please say: he is fine, or: he needs help. How is Ann?"
    )


def test_english_intro_verbatim():
    en = replace(P, name="Ann", family="your grandson Peter")  # a phone in a country we have no number for
    assert render(ScriptKind.INTRO_PERSON, "en", en) == (
        "Hello, Ann. This is Kuc-kuc, an automated call. your grandson Peter turned this service on for you. "
        "We will call you only when dangerous weather is coming, to check that you are okay. "
        "A computer assesses your answer. It is recorded briefly so that your grandson Peter can hear it, "
        "and the recording is deleted after seven days. "
        "This is not an emergency service: in an emergency, call your emergency number. "
        "Please say yes or no: do you agree?"
    )


def test_german_check_and_headsup_verbatim():
    assert render(ScriptKind.CHECK, "de", replace(DE, hazard="heat", value=36)) == (
        "Guten Tag, Frau Weber. Hier ist Kuc-kuc, ein automatischer Anruf. Heute ist es sehr heiß. "
        "Ich rufe an, um nachzufragen: Geht es Ihnen gut?"
    )
    assert render(ScriptKind.CHECK_MANUAL, "de", DE) == (
        "Guten Tag, Frau Weber. Hier ist Kuc-kuc, ein automatischer Anruf. Ich rufe an, um nachzufragen: Geht es Ihnen gut?"
    )
    assert render(ScriptKind.HEADSUP, "de", DE) == (
        "Guten Tag, Frau Weber. Hier ist Kuc-kuc, ein automatischer Anruf. Morgen wird es sehr kalt, bis zu minus 12 Grad. "
        "Halten Sie die Wohnung warm und bleiben Sie möglichst zu Hause. Geht es Ihnen gut?"
    )


def test_german_intro_verbatim():
    assert render(ScriptKind.INTRO_PERSON, "de", DE) == (
        "Guten Tag, Frau Weber. Hier ist Kuc-kuc, ein automatischer Anruf. "
        "Ihr Enkel Peter hat diesen Dienst für Sie eingeschaltet. "
        "Wir rufen Sie nur an, wenn gefährliches Wetter kommt, um zu fragen, ob es Ihnen gut geht. "
        "Ein Computer wertet Ihre Antwort aus. Die Antwort wird kurz aufgenommen, damit Ihr Enkel Peter sie hören kann, "
        "und nach sieben Tagen gelöscht. "
        "Kuc-kuc ist kein Notrufdienst: Im Notfall wählen Sie bitte eins eins zwei. "
        "Bitte sagen Sie ja oder nein: Sind Sie einverstanden?"
    )


def test_german_neighbour_lines_follow_the_persons_gender():
    him = replace(DE, name="Herr Weber", gender="m", hazard="wind", value=None)
    assert render(ScriptKind.NEIGHBOR_ASK_SILENT, "de", him) == (
        "Guten Tag, Herr Braun. Hier ist Kuc-kuc, ein automatischer Anruf. "
        "Herr Weber geht nicht ans Telefon, und heute gibt es starken Wind. "
        "Bitte sagen Sie ja oder nein: Können Sie jetzt zu ihm gehen?"
    )
    assert render(ScriptKind.NEIGHBOR_ASK_UNWELL, "de", him) == (
        "Guten Tag, Herr Braun. Hier ist Kuc-kuc, ein automatischer Anruf. "
        "Herr Weber sagt, dass es ihm nicht gut geht. Bitte sagen Sie ja oder nein: Können Sie jetzt zu ihm gehen?"
    )
    assert "zu ihr gehen?" in render(ScriptKind.NEIGHBOR_ASK_UNWELL, "de", DE)
    assert render(ScriptKind.INTRO_NEIGHBOR, "de", DE) == (
        "Guten Tag, Herr Braun. Hier ist Kuc-kuc, ein automatischer Anruf. "
        "Wenn gefährliches Wetter kommt, rufen wir Frau Weber an, um zu fragen, ob es ihr gut geht. "
        "Wenn sie nicht abnimmt oder es ihr nicht gut geht, würden wir Sie anrufen und bitten, zu ihr zu gehen. "
        "Ein Computer wertet Ihre Antwort aus. Die Antwort wird kurz aufgenommen und nach sieben Tagen gelöscht. "
        "Bitte sagen Sie ja oder nein: Sind Sie einverstanden?"
    )


def test_german_puts_herr_into_the_case_the_sentence_needs():
    # "Wie geht es Herr Weber?" is wrong German: after "es" and "rufen wir ... an" it is "Herrn Weber".
    him = replace(DE, name="Herr Weber", gender="m")
    assert render(ScriptKind.NEIGHBOR_FOLLOWUP, "de", him) == (
        "Guten Tag, Herr Braun. Hier ist Kuc-kuc, ein automatischer Anruf. "
        "Bitte sagen Sie: alles gut, oder: braucht Hilfe. Wie geht es Herrn Weber?"
    )
    assert "rufen wir Herrn Weber an" in render(ScriptKind.INTRO_NEIGHBOR, "de", him)
    assert "Guten Tag, Herr Weber." in render(ScriptKind.CHECK, "de", him)
    assert render(ScriptKind.NEIGHBOR_FOLLOWUP, "de", DE).endswith("Wie geht es Frau Weber?")


def test_german_reasks_and_closings():
    assert reask(Question.WELLBEING, "de") == (
        "Entschuldigung, ich habe Sie nicht verstanden. Bitte sagen Sie ja oder nein: Geht es Ihnen gut?"
    )
    assert reask(Question.YES_NO, "de") == "Entschuldigung, ich habe Sie nicht verstanden. Bitte sagen Sie ja oder nein."
    assert reask(Question.NEIGHBOR_REPORT, "de") == (
        "Entschuldigung, ich habe Sie nicht verstanden. Bitte sagen Sie: alles gut, oder: braucht Hilfe."
    )
    assert closing(ScriptKind.CHECK, Answer.OK, "de", DE) == "Danke, Frau Weber. Passen Sie gut auf sich auf. Auf Wiederhören."
    assert closing(ScriptKind.CHECK, Answer.NEEDS_HELP, "de", DE) == (
        "Ich bitte Ihren Nachbarn, bei Ihnen vorbeizuschauen. Auf Wiederhören."
    )
    alone = replace(DE, has_neighbor=False)
    assert closing(ScriptKind.CHECK, Answer.UNCLEAR, "de", alone) == "Ich sage Ihrer Familie Bescheid. Auf Wiederhören."
    assert closing(ScriptKind.INTRO_PERSON, Answer.YES, "de", DE) == "Ich habe verstanden: ja. Danke. Auf Wiederhören."
    assert closing(ScriptKind.INTRO_PERSON, Answer.NO, "de", DE) == "Ich habe verstanden: nein. Danke. Auf Wiederhören."
    assert closing(ScriptKind.HEADSUP, Answer.UNCLEAR, "de", DE) == "Danke. Auf Wiederhören."
    assert neutral_closing("de") == "Danke. Auf Wiederhören."


def test_to_cyrillic_digraphs():
    assert to_cyrillic("Ljubica, Njegoš, džak, đak, čaša, šuma, žaba") == "Љубица, Његош, џак, ђак, чаша, шума, жаба"


def test_to_cyrillic_keeps_digits_and_punctuation():
    assert to_cyrillic("Kuc-kuc: do minus 12 stepeni?") == "Куц-куц: до минус 12 степени?"


def test_speakable():
    assert speakable("Dobar dan", "sr") == "Добар дан" and speakable("Hello", "en") == "Hello"
    assert speakable("Guten Tag, Frau Weber. Geht es Ihnen gut?", "de") == "Guten Tag, Frau Weber. Geht es Ihnen gut?"


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
    assert closing(ScriptKind.CHECK, Answer.UNCLEAR, "en", P) == "I'll ask your neighbour to stop by. Goodbye."


def test_neutral_closing_promises_nothing():
    assert neutral_closing("sr") == "Hvala. Doviđenja."
    assert neutral_closing("en") == "Thank you. Goodbye."


def test_headsup_closing_promises_the_neighbour_only_when_one_is_sent():
    # An unclear heads-up is retried later and never sends the neighbour (spec 5.4).
    assert closing(ScriptKind.HEADSUP, Answer.NEEDS_HELP, "sr", P) == "Javiću komšiji da svrati do vas. Doviđenja."
    assert closing(ScriptKind.HEADSUP, Answer.UNCLEAR, "sr", P) == "Hvala. Doviđenja."


@pytest.mark.parametrize("lang", LANGS)
def test_every_script_renders_in_every_language(lang):
    for s in ScriptKind:
        for p in (P, replace(P, hazard=None, value=None), replace(P, gender="m", value=None)):
            text = render(s, lang, p)
            assert text and "{" not in text and "None" not in text
        for answer in Answer:
            assert "{" not in closing(s, answer, lang, P) and "None" not in closing(s, answer, lang, P)


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
    alone = replace(P, has_neighbor=False)
    assert closing(ScriptKind.CHECK, Answer.NEEDS_HELP, "sr", alone) == "Javiću vašoj porodici. Doviđenja."
    assert closing(ScriptKind.CHECK, Answer.UNCLEAR, "en", alone) == "I'll let your family know. Goodbye."
    assert closing(ScriptKind.CHECK, Answer.NEEDS_HELP, "sr", P) == "Javiću komšiji da svrati do vas. Doviđenja."


def test_whether_there_is_a_neighbour_survives_storage_and_old_calls_keep_the_old_words():
    assert params_from_dict(params_to_dict(replace(P, has_neighbor=False))).has_neighbor is False
    assert params_from_dict(params_to_dict(P)).has_neighbor is True
    assert params_from_dict({"name": "N"}).has_neighbor is True  # calls stored before the field existed


def test_every_call_ends_with_its_question_so_the_answer_is_recorded():
    # Recording starts when the voice stops. "Recite da ili ne: da li se slažete?" made a neighbour
    # answer "Da" during the last four words, which were not recorded.
    for lang in LANGS:
        for script in ScriptKind:
            assert render(script, lang, P).endswith("?"), (lang, script)
        assert reask(Question.WELLBEING, lang).endswith("?"), lang


def test_a_consent_answer_is_said_back_so_a_mistake_is_heard_at_once():
    assert closing(ScriptKind.INTRO_NEIGHBOR, Answer.YES, "sr", P) == "Razumela sam: da. Hvala. Doviđenja."
    assert closing(ScriptKind.INTRO_NEIGHBOR, Answer.NO, "sr", P) == "Razumela sam: ne. Hvala. Doviđenja."
    assert closing(ScriptKind.INTRO_PERSON, Answer.YES, "en", P) == "I understood: yes. Thank you. Goodbye."
    assert closing(ScriptKind.INTRO_PERSON, Answer.UNCLEAR, "sr", P) == "Hvala. Doviđenja."


def test_the_emergency_number_is_said_in_the_language_of_the_call():
    # A Serbian speaker in Germany is told 112, not 194; a phone we know no number for gets plain words.
    assert "u hitnom slučaju zovite sto dvanaest." in render(ScriptKind.INTRO_PERSON, "sr", replace(P, emergency="112"))
    assert "u hitnom slučaju zovite hitnu pomoć." in render(ScriptKind.INTRO_PERSON, "sr", P)
    assert "in an emergency, call one one two." in render(ScriptKind.INTRO_PERSON, "en", replace(P, emergency="112"))
    assert "Im Notfall rufen Sie bitte den Notruf an." in render(ScriptKind.INTRO_PERSON, "de", replace(DE, emergency=""))
    assert "Im Notfall wählen Sie bitte eins neun vier." in render(ScriptKind.INTRO_PERSON, "de", replace(DE, emergency="194"))
    for emergency in ("194", "112", "911", "999", "000", "", "12345"):
        for lang in LANGS:
            text = render(ScriptKind.INTRO_PERSON, lang, replace(P, emergency=emergency))
            assert "{" not in text and "None" not in text and "12345" not in text, (lang, emergency)


def test_the_emergency_number_survives_the_call_record_and_old_calls_get_plain_words():
    assert params_from_dict(params_to_dict(replace(P, emergency="112"))).emergency == "112"
    assert params_from_dict({"name": "N"}).emergency == ""


def test_every_welcome_call_says_a_computer_assesses_the_answer():
    words = {"sr": "Vaš odgovor procenjuje računar.", "en": "A computer assesses your answer.",
             "de": "Ein Computer wertet Ihre Antwort aus."}  # fmt: skip
    for lang, person in (("sr", P), ("en", P), ("de", DE)):
        for script in (ScriptKind.INTRO_PERSON, ScriptKind.INTRO_NEIGHBOR):
            assert words[lang] in render(script, lang, person), (lang, script)


def test_german_snow_advice_is_not_heard_as_a_question():
    # "und haben Sie Essen ... im Haus" sounds like "Haben Sie Essen im Haus?"; someone may answer "ja".
    text = render(ScriptKind.HEADSUP, "de", replace(DE, hazard="snow", value=None))
    assert "sorgen Sie für Essen und Medikamente im Haus." in text and "haben Sie" not in text


def test_german_herr_in_any_written_form_goes_into_the_case():
    for written in ("Hr. Weber", "herr Weber", "HERR Weber"):
        him = replace(DE, name=written, gender="m")
        assert render(ScriptKind.NEIGHBOR_FOLLOWUP, "de", him).endswith("Wie geht es Herrn Weber?"), written
    assert render(ScriptKind.NEIGHBOR_FOLLOWUP, "de", replace(DE, name="Herrmann")).endswith("Wie geht es Herrmann?")


def test_norways_ambulance_number_is_said_in_every_language():
    for lang, words in (("sr", "sto trinaest"), ("en", "one one three"), ("de", "eins eins drei")):
        assert words in render(ScriptKind.INTRO_PERSON, lang, replace(P, emergency="113")), lang
