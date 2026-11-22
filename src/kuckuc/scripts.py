"""Every sentence the service says on the phone, in Serbian, English and German.

Serbian lines follow the approved design (section 8), with the additions the user approved on 22 Nov 2026:
every call says it is automated (EU AI Act, Art. 50), and the welcome call says that a computer listens,
that the recording is deleted after seven days, and that this is not an emergency service. They are written
in Latin script here and converted to Cyrillic only at the moment they go to speech synthesis.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

from kuckuc.models import Answer, Gender, Hazard, Lang, Question, ScriptKind


@dataclass(frozen=True)
class ScriptParams:
    name: str  # the person, as spoken: "baba Milena"
    family: str = ""  # who turned the service on, as said to the person: "vaš unuk Petar"
    neighbor: str = ""
    gender: Gender = "f"
    hazard: Hazard | None = None
    value: float | None = None  # already in the unit to be spoken
    has_neighbor: bool = True  # whether a neighbour has said yes; nobody is promised one who does not exist
    emergency: str = ""  # the emergency number of the person's country ("194", "112"); "" when not known


_QUESTIONS: dict[ScriptKind, Question] = {
    ScriptKind.INTRO_PERSON: Question.YES_NO,
    ScriptKind.INTRO_NEIGHBOR: Question.YES_NO,
    ScriptKind.HEADSUP: Question.WELLBEING,
    ScriptKind.CHECK: Question.WELLBEING,
    ScriptKind.CHECK_MANUAL: Question.WELLBEING,
    ScriptKind.NEIGHBOR_ASK_SILENT: Question.YES_NO,
    ScriptKind.NEIGHBOR_ASK_UNWELL: Question.YES_NO,
    ScriptKind.NEIGHBOR_FOLLOWUP: Question.NEIGHBOR_REPORT,
}


def question_for(script: ScriptKind) -> Question:
    return _QUESTIONS[script]


# Hazard as it follows "Danas je" / "Today is" / "Heute".
_TODAY = {
    "sr": {"heat": "veoma vruće", "cold": "veoma hladno", "wind": "jak vetar", "snow": "mnogo snega"},
    "en": {"heat": "very hot", "cold": "very cold", "wind": "very windy", "snow": "very snowy"},
    "de": {"heat": "ist es sehr heiß", "cold": "ist es sehr kalt", "wind": "gibt es starken Wind", "snow": "gibt es viel Schnee"},
}

# What is coming tomorrow, with one piece of advice. {temp} is filled in for heat and cold.
_TOMORROW = {
    "sr": {
        "heat": "Sutra će biti veoma vruće, do {temp} stepeni. Pijte dosta vode i ne izlazite u podne.",
        "cold": "Sutra će biti veoma hladno, do {temp} stepeni. Pripremite drva i ne izlazite ako ne morate.",
        "wind": "Sutra će duvati jak vetar. Zatvorite prozore i ne izlazite ako ne morate.",
        "snow": "Sutra će pasti mnogo snega. Ne izlazite ako ne morate i imajte hranu i lekove u kući.",
    },
    "en": {
        "heat": "Tomorrow will be very hot, up to {temp} degrees. Drink plenty of water and stay inside at midday.",
        "cold": "Tomorrow will be very cold, down to {temp} degrees. Keep the house warm and stay in if you can.",
        "wind": "Tomorrow will be very windy. Close your windows and stay in if you can.",
        "snow": "Tomorrow will bring heavy snow. Stay in if you can, and keep food and medicine at home.",
    },
    "de": {
        "heat": "Morgen wird es sehr heiß, bis zu {temp} Grad. Trinken Sie viel Wasser und bleiben Sie mittags im Haus.",
        "cold": "Morgen wird es sehr kalt, bis zu {temp} Grad. Halten Sie die Wohnung warm und bleiben Sie möglichst zu Hause.",
        "wind": "Morgen gibt es starken Wind. Schließen Sie die Fenster und bleiben Sie möglichst zu Hause.",
        "snow": "Morgen fällt viel Schnee. Bleiben Sie möglichst zu Hause und sorgen Sie für Essen und Medikamente im Haus.",
    },
}
_TOMORROW_UNKNOWN = {
    "sr": "Sutra stiže opasno vreme.",
    "en": "Dangerous weather is coming tomorrow.",
    "de": "Morgen kommt gefährliches Wetter.",
}

_PRONOUNS = {
    "sr": {"f": {"to": "do nje", "dative": "joj"}, "m": {"to": "do njega", "dative": "mu"}},
    "en": {"f": {"subject": "she", "object": "her"}, "m": {"subject": "he", "object": "him"}},
    "de": {"f": {"subject": "sie", "dative": "ihr", "to": "zu ihr"}, "m": {"subject": "er", "dative": "ihm", "to": "zu ihm"}},
}


# The emergency number as people say it in each language, and the words for "call it". With no number
# we know for the person's country, plain words are said instead of a number.
_EMERGENCY_WORDS = {
    "sr": {"194": "sto devedeset četiri", "112": "sto dvanaest", "911": "devet jedan jedan",
           "999": "devet devet devet", "000": "nula nula nula"},
    "en": {"194": "one nine four", "112": "one one two", "911": "nine one one", "999": "nine nine nine",
           "000": "triple zero"},
    "de": {"194": "eins neun vier", "112": "eins eins zwei", "911": "neun eins eins", "999": "neun neun neun",
           "000": "null null null"},
}  # fmt: skip
_CALL_EMERGENCY = {
    "sr": ("zovite {number}", "zovite hitnu pomoć"),
    "en": ("call {number}", "call your emergency number"),
    "de": ("wählen Sie bitte {number}", "rufen Sie bitte den Notruf an"),
}


def _call_emergency(lang: Lang, p: ScriptParams) -> str:
    number = _EMERGENCY_WORDS[lang].get(p.emergency)
    known, unknown = _CALL_EMERGENCY[lang]
    return known.format(number=number) if number else unknown


def _number(value: float | None) -> str:
    n = int(round(value or 0))
    if n < 0:
        return f"minus {abs(n)}"
    return str(n)


def render(script: ScriptKind, lang: Lang, p: ScriptParams) -> str:
    """The opening of a call. It always ends with the question the answer is recorded for."""
    return _RENDER[lang](script, p)


def _render_sr(script: ScriptKind, p: ScriptParams) -> str:
    pro = _PRONOUNS["sr"][p.gender]
    today = _TODAY["sr"].get(p.hazard) if p.hazard else None
    hello_person = f"Dobar dan, {p.name}. Ovde Kuc-kuc, automatski poziv."
    hello_neighbor = f"Dobar dan, {p.neighbor}. Ovde Kuc-kuc, automatski poziv."
    can_you_go = f"Recite da ili ne: možete li da odete {pro['to']} sada?"

    match script:
        case ScriptKind.INTRO_PERSON:
            return (
                f"{hello_person} Ovu službu je za vas uključio {p.family}. "
                "Zvaćemo vas samo kad stiže opasno vreme, da proverimo da li ste dobro. "
                f"Vaš odgovor procenjuje računar. Odgovor se kratko snima da {p.family} može da ga čuje, "
                "a snimak se briše posle sedam dana. "
                f"Ovo nije hitna služba: u hitnom slučaju {_call_emergency('sr', p)}. "
                "Recite da ili ne: da li se slažete?"
            )
        case ScriptKind.INTRO_NEIGHBOR:
            return (
                f"{hello_neighbor} Kad stiže opasno vreme, zovemo telefonom da proverimo da li je {p.name} dobro. "
                f"Ako se ne javi ili {pro['dative']} nije dobro, pozvali bismo vas da odete {pro['to']}. "
                "Vaš odgovor procenjuje računar. Odgovor se kratko snima i briše posle sedam dana. "
                "Recite da ili ne: da li se slažete?"
            )
        case ScriptKind.HEADSUP:
            return f"{hello_person} {_tomorrow('sr', p)} Jeste li dobro?"
        case ScriptKind.CHECK if today:
            return f"{hello_person} Danas je {today}. Zovem da proverim: jeste li dobro?"
        case ScriptKind.CHECK | ScriptKind.CHECK_MANUAL:
            return f"{hello_person} Zovem da proverim: jeste li dobro?"
        case ScriptKind.NEIGHBOR_ASK_SILENT:
            weather = f", a danas je {today}" if today else ""
            return f"{hello_neighbor} {p.name} se ne javlja na telefon{weather}. {can_you_go}"
        case ScriptKind.NEIGHBOR_ASK_UNWELL:
            return f"{hello_neighbor} {p.name} kaže da {pro['dative']} nije dobro. {can_you_go}"
        case ScriptKind.NEIGHBOR_FOLLOWUP:
            return f"{hello_neighbor} Recite: dobro je, ili: treba pomoć. Kako je {p.name}?"
    raise ValueError(f"no Serbian text for {script}")


def _render_en(script: ScriptKind, p: ScriptParams) -> str:
    pro = _PRONOUNS["en"][p.gender]
    today = _TODAY["en"].get(p.hazard) if p.hazard else None
    hello_person = f"Hello, {p.name}. This is Kuc-kuc, an automated call."
    hello_neighbor = f"Hello, {p.neighbor}. This is Kuc-kuc, an automated call."
    can_you_go = f"Please say yes or no: can you go and check on {pro['object']} now?"

    match script:
        case ScriptKind.INTRO_PERSON:
            return (
                f"{hello_person} {p.family} turned this service on for you. "
                "We will call you only when dangerous weather is coming, to check that you are okay. "
                f"A computer assesses your answer. It is recorded briefly so that {p.family} can hear it, "
                "and the recording is deleted after seven days. "
                f"This is not an emergency service: in an emergency, {_call_emergency('en', p)}. "
                "Please say yes or no: do you agree?"
            )
        case ScriptKind.INTRO_NEIGHBOR:
            return (
                f"{hello_neighbor} When dangerous weather is coming, we phone {p.name} "
                f"to check that {pro['subject']} is okay. "
                f"If {pro['subject']} doesn't answer or is unwell, we would call you "
                f"and ask you to go and check on {pro['object']}. "
                "A computer assesses your answer. It is recorded briefly and deleted after seven days. "
                "Please say yes or no: do you agree?"
            )
        case ScriptKind.HEADSUP:
            return f"{hello_person} {_tomorrow('en', p)} Are you okay?"
        case ScriptKind.CHECK if today:
            return f"{hello_person} Today is {today}. I'm calling to check on you: are you okay?"
        case ScriptKind.CHECK | ScriptKind.CHECK_MANUAL:
            return f"{hello_person} I'm calling to check on you: are you okay?"
        case ScriptKind.NEIGHBOR_ASK_SILENT:
            weather = f", and today is {today}" if today else ""
            return f"{hello_neighbor} {p.name} isn't answering the phone{weather}. {can_you_go}"
        case ScriptKind.NEIGHBOR_ASK_UNWELL:
            return f"{hello_neighbor} {p.name} says {pro['subject']} is not feeling well. {can_you_go}"
        case ScriptKind.NEIGHBOR_FOLLOWUP:
            return (
                f"{hello_neighbor} Please say: {pro['subject']} is fine, or: {pro['subject']} needs help. "
                f"How is {p.name}?"
            )
    raise ValueError(f"no English text for {script}")


def _de_oblique(name: str) -> str:
    """The name where German needs the accusative or dative: "Herr Weber" (or "Hr. Weber", "herr Weber")
    becomes "Herrn Weber". Other names ("Frau Weber", "Oma Inge") are the same in every case."""
    for title in ("herr ", "hr. "):
        if name.lower().startswith(title):
            return "Herrn " + name[len(title) :]
    return name


def _render_de(script: ScriptKind, p: ScriptParams) -> str:
    pro = _PRONOUNS["de"][p.gender]
    today = _TODAY["de"].get(p.hazard) if p.hazard else None
    hello_person = f"Guten Tag, {p.name}. Hier ist Kuc-kuc, ein automatischer Anruf."
    hello_neighbor = f"Guten Tag, {p.neighbor}. Hier ist Kuc-kuc, ein automatischer Anruf."
    can_you_go = f"Bitte sagen Sie ja oder nein: Können Sie jetzt {pro['to']} gehen?"
    are_you_ok = "Geht es Ihnen gut?"

    match script:
        case ScriptKind.INTRO_PERSON:
            return (
                f"{hello_person} {p.family} hat diesen Dienst für Sie eingeschaltet. "
                "Wir rufen Sie nur an, wenn gefährliches Wetter kommt, um zu fragen, ob es Ihnen gut geht. "
                f"Ein Computer wertet Ihre Antwort aus. Die Antwort wird kurz aufgenommen, damit {p.family} sie hören kann, "
                "und nach sieben Tagen gelöscht. "
                f"Kuc-kuc ist kein Notrufdienst: Im Notfall {_call_emergency('de', p)}. "
                "Bitte sagen Sie ja oder nein: Sind Sie einverstanden?"
            )
        case ScriptKind.INTRO_NEIGHBOR:
            return (
                f"{hello_neighbor} Wenn gefährliches Wetter kommt, rufen wir {_de_oblique(p.name)} an, "
                f"um zu fragen, ob es {pro['dative']} gut geht. "
                f"Wenn {pro['subject']} nicht abnimmt oder es {pro['dative']} nicht gut geht, "
                f"würden wir Sie anrufen und bitten, {pro['to']} zu gehen. "
                "Ein Computer wertet Ihre Antwort aus. Die Antwort wird kurz aufgenommen und nach sieben Tagen gelöscht. "
                "Bitte sagen Sie ja oder nein: Sind Sie einverstanden?"
            )
        case ScriptKind.HEADSUP:
            return f"{hello_person} {_tomorrow('de', p)} {are_you_ok}"
        case ScriptKind.CHECK if today:
            return f"{hello_person} Heute {today}. Ich rufe an, um nachzufragen: {are_you_ok}"
        case ScriptKind.CHECK | ScriptKind.CHECK_MANUAL:
            return f"{hello_person} Ich rufe an, um nachzufragen: {are_you_ok}"
        case ScriptKind.NEIGHBOR_ASK_SILENT:
            weather = f", und heute {today}" if today else ""
            return f"{hello_neighbor} {p.name} geht nicht ans Telefon{weather}. {can_you_go}"
        case ScriptKind.NEIGHBOR_ASK_UNWELL:
            return f"{hello_neighbor} {p.name} sagt, dass es {pro['dative']} nicht gut geht. {can_you_go}"
        case ScriptKind.NEIGHBOR_FOLLOWUP:
            return (
                f"{hello_neighbor} Bitte sagen Sie: alles gut, oder: braucht Hilfe. "
                f"Wie geht es {_de_oblique(p.name)}?"
            )
    raise ValueError(f"no German text for {script}")


_RENDER: dict[str, Callable[[ScriptKind, ScriptParams], str]] = {"sr": _render_sr, "en": _render_en, "de": _render_de}


def _tomorrow(lang: Lang, p: ScriptParams) -> str:
    if not p.hazard:
        return _TOMORROW_UNKNOWN[lang]
    return _TOMORROW[lang][p.hazard].format(temp=_number(p.value))


_REASK = {
    "sr": {
        Question.WELLBEING: "Nisam vas razumela. Recite da ili ne: jeste li dobro?",
        Question.YES_NO: "Nisam vas razumela. Recite da ili ne.",
        Question.NEIGHBOR_REPORT: "Nisam vas razumela. Recite: dobro je, ili: treba pomoć.",
    },
    "en": {
        Question.WELLBEING: "Sorry, I didn't catch that. Please say yes or no: are you okay?",
        Question.YES_NO: "Sorry, I didn't catch that. Please say yes or no.",
        Question.NEIGHBOR_REPORT: "Sorry, I didn't catch that. Please say: fine, or: needs help.",
    },
    "de": {
        Question.WELLBEING: "Entschuldigung, ich habe Sie nicht verstanden. Bitte sagen Sie ja oder nein: Geht es Ihnen gut?",
        Question.YES_NO: "Entschuldigung, ich habe Sie nicht verstanden. Bitte sagen Sie ja oder nein.",
        Question.NEIGHBOR_REPORT: "Entschuldigung, ich habe Sie nicht verstanden. Bitte sagen Sie: alles gut, oder: braucht Hilfe.",
    },
}


def reask(question: Question, lang: Lang) -> str:
    return _REASK[lang][question]


_SENDS_NEIGHBOR: dict[ScriptKind, frozenset[Answer]] = {
    ScriptKind.CHECK: frozenset({Answer.NEEDS_HELP, Answer.UNCLEAR}),
    ScriptKind.CHECK_MANUAL: frozenset({Answer.NEEDS_HELP, Answer.UNCLEAR}),
    # An unclear heads-up is only retried later; promising the neighbour would be untrue.
    ScriptKind.HEADSUP: frozenset({Answer.NEEDS_HELP}),
}
_WELLBEING_SCRIPTS = frozenset(_SENDS_NEIGHBOR)

_CLOSINGS = {
    "sr": {
        "heard": "Razumela sam",
        Answer.YES: "da",
        Answer.NO: "ne",
        "ok": "Hvala, {name}. Čuvajte se. Doviđenja.",
        "family": "Javiću vašoj porodici. Doviđenja.",
        "neighbor": "Javiću komšiji da svrati do vas. Doviđenja.",
        "neutral": "Hvala. Doviđenja.",
    },
    "en": {
        "heard": "I understood",
        Answer.YES: "yes",
        Answer.NO: "no",
        "ok": "Thank you, {name}. Take care. Goodbye.",
        "family": "I'll let your family know. Goodbye.",
        "neighbor": "I'll ask your neighbour to stop by. Goodbye.",
        "neutral": "Thank you. Goodbye.",
    },
    "de": {
        "heard": "Ich habe verstanden",
        Answer.YES: "ja",
        Answer.NO: "nein",
        "ok": "Danke, {name}. Passen Sie gut auf sich auf. Auf Wiederhören.",
        "family": "Ich sage Ihrer Familie Bescheid. Auf Wiederhören.",
        "neighbor": "Ich bitte Ihren Nachbarn, bei Ihnen vorbeizuschauen. Auf Wiederhören.",
        "neutral": "Danke. Auf Wiederhören.",
    },
}


def closing(script: ScriptKind, answer: Answer, lang: Lang, p: ScriptParams) -> str:
    """The last sentence of a call. "Take care" is said only after a clear "I am fine"."""
    words = _CLOSINGS[lang]
    if script in (ScriptKind.INTRO_PERSON, ScriptKind.INTRO_NEIGHBOR) and answer in (Answer.YES, Answer.NO):
        return f"{words['heard']}: {words[answer]}. {neutral_closing(lang)}"
    if script in _WELLBEING_SCRIPTS and answer == Answer.OK:
        return words["ok"].format(name=p.name)
    if answer in _SENDS_NEIGHBOR.get(script, frozenset()):
        # With no neighbour who said yes, the family is told instead: say exactly that.
        return words["neighbor"] if p.has_neighbor else words["family"]
    return neutral_closing(lang)


def neutral_closing(lang: Lang) -> str:
    """A goodbye that promises nothing. Used whenever the service is not sure what comes next."""
    return _CLOSINGS[lang]["neutral"]


_DIGRAPHS = {"lj": "љ", "nj": "њ", "dž": "џ"}
_LETTERS = {
    "a": "а", "b": "б", "c": "ц", "č": "ч", "ć": "ћ", "d": "д", "đ": "ђ", "e": "е", "f": "ф", "g": "г",
    "h": "х", "i": "и", "j": "ј", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р",
    "s": "с", "š": "ш", "t": "т", "u": "у", "v": "в", "z": "з", "ž": "ж",
}  # fmt: skip


def to_cyrillic(text: str) -> str:
    """Serbian Latin to Cyrillic. Anything that is not a Serbian letter passes through unchanged."""
    out: list[str] = []
    i = 0
    while i < len(text):
        pair = text[i : i + 2]
        cyr = _DIGRAPHS.get(pair.lower())
        if cyr and len(pair) == 2:
            out.append(cyr.upper() if pair[0].isupper() else cyr)
            i += 2
            continue
        ch = text[i]
        cyr = _LETTERS.get(ch.lower())
        out.append(ch if cyr is None else (cyr.upper() if ch.isupper() else cyr))
        i += 1
    return "".join(out)


def speakable(text: str, lang: Lang) -> str:
    """The text exactly as it is handed to speech synthesis."""
    return to_cyrillic(text) if lang == "sr" else text


def params_to_dict(p: ScriptParams) -> dict[str, str]:
    """For storing on a call record. Empty string stands for "not set"."""
    return {k: "" if v is None else str(v) for k, v in asdict(p).items()}


def params_from_dict(d: dict[str, str]) -> ScriptParams:
    """Reverse of params_to_dict. Keys that are not script parameters are ignored."""
    return ScriptParams(
        name=d.get("name", ""),
        family=d.get("family", ""),
        neighbor=d.get("neighbor", ""),
        gender="m" if d.get("gender") == "m" else "f",
        hazard=d.get("hazard") or None,  # type: ignore[arg-type]
        value=float(d["value"]) if d.get("value") else None,
        has_neighbor=d.get("has_neighbor") != "False",  # calls stored before this field keep the old words
        emergency=d.get("emergency", ""),  # calls stored before this field: plain words, right anywhere
    )
