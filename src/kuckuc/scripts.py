"""Every sentence the service says on the phone, in Serbian and English.

Serbian lines follow the approved design (section 8) word for word. They are written in Latin
script here and converted to Cyrillic only at the moment they go to speech synthesis.
"""

from __future__ import annotations

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


# Hazard as it follows "Danas je" / "Today is".
_TODAY = {
    "sr": {"heat": "veoma vruće", "cold": "veoma hladno", "wind": "jak vetar", "snow": "mnogo snega"},
    "en": {"heat": "very hot", "cold": "very cold", "wind": "very windy", "snow": "very snowy"},
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
}
_TOMORROW_UNKNOWN = {"sr": "Sutra stiže opasno vreme.", "en": "Dangerous weather is coming tomorrow."}

_PRONOUNS = {
    "sr": {"f": {"to": "do nje", "dative": "joj"}, "m": {"to": "do njega", "dative": "mu"}},
    "en": {"f": {"subject": "she", "object": "her"}, "m": {"subject": "he", "object": "him"}},
}


def _number(value: float | None) -> str:
    n = int(round(value or 0))
    if n < 0:
        return f"minus {abs(n)}"
    return str(n)


def render(script: ScriptKind, lang: Lang, p: ScriptParams) -> str:
    """The opening of a call. It always ends with the question the answer is recorded for."""
    return _render_sr(script, p) if lang == "sr" else _render_en(script, p)


def _render_sr(script: ScriptKind, p: ScriptParams) -> str:
    pro = _PRONOUNS["sr"][p.gender]
    today = _TODAY["sr"].get(p.hazard) if p.hazard else None
    hello_person = f"Dobar dan, {p.name}. Ovde Kuc-kuc."
    hello_neighbor = f"Dobar dan, {p.neighbor}. Ovde Kuc-kuc."
    can_you_go = f"Recite da ili ne: možete li da odete {pro['to']} sada?"

    match script:
        case ScriptKind.INTRO_PERSON:
            return (
                f"{hello_person} Ovu službu je za vas uključio {p.family}. "
                "Zvaćemo vas samo kad stiže opasno vreme, da proverimo da li ste dobro. "
                f"Vaš odgovor se kratko snima da {p.family} može da ga čuje. "
                "Recite da ili ne: da li se slažete?"
            )
        case ScriptKind.INTRO_NEIGHBOR:
            return (
                f"{hello_neighbor} Kad stiže opasno vreme, zovemo telefonom da proverimo da li je {p.name} dobro. "
                f"Ako se ne javi ili {pro['dative']} nije dobro, pozvali bismo vas da odete {pro['to']}. "
                "Vaš odgovor se kratko snima. Recite da ili ne: da li se slažete?"
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
            return f"Dobar dan, {p.neighbor}. Recite: dobro je, ili: treba pomoć. Kako je {p.name}?"
    raise ValueError(f"no Serbian text for {script}")


def _render_en(script: ScriptKind, p: ScriptParams) -> str:
    pro = _PRONOUNS["en"][p.gender]
    today = _TODAY["en"].get(p.hazard) if p.hazard else None
    hello_person = f"Hello, {p.name}. This is Kuc-kuc."
    hello_neighbor = f"Hello, {p.neighbor}. This is Kuc-kuc."
    can_you_go = f"Please say yes or no: can you go and check on {pro['object']} now?"

    match script:
        case ScriptKind.INTRO_PERSON:
            return (
                f"{hello_person} {p.family} turned this service on for you. "
                "We will call you only when dangerous weather is coming, to check that you are okay. "
                f"Your answer is recorded briefly so that {p.family} can hear it. "
                "Please say yes or no: do you agree?"
            )
        case ScriptKind.INTRO_NEIGHBOR:
            return (
                f"{hello_neighbor} When dangerous weather is coming, we phone {p.name} "
                f"to check that {pro['subject']} is okay. "
                f"If {pro['subject']} doesn't answer or is unwell, we would call you "
                f"and ask you to go and check on {pro['object']}. "
                "Your answer is recorded briefly. Please say yes or no: do you agree?"
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
                f"Hello, {p.neighbor}. Please say: {pro['subject']} is fine, or: {pro['subject']} needs help. "
                f"How is {p.name}?"
            )
    raise ValueError(f"no English text for {script}")


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


def closing(script: ScriptKind, answer: Answer, lang: Lang, p: ScriptParams) -> str:
    """The last sentence of a call. "Take care" is said only after a clear "I am fine"."""
    if script in (ScriptKind.INTRO_PERSON, ScriptKind.INTRO_NEIGHBOR) and answer in (Answer.YES, Answer.NO):
        said = {"sr": {Answer.YES: "da", Answer.NO: "ne"}, "en": {Answer.YES: "yes", Answer.NO: "no"}}[lang][answer]
        heard = "Razumela sam" if lang == "sr" else "I understood"
        return f"{heard}: {said}. {neutral_closing(lang)}"
    if script in _WELLBEING_SCRIPTS and answer == Answer.OK:
        return (
            f"Hvala, {p.name}. Čuvajte se. Doviđenja." if lang == "sr" else f"Thank you, {p.name}. Take care. Goodbye."
        )
    if answer in _SENDS_NEIGHBOR.get(script, frozenset()):
        if not p.has_neighbor:
            # No neighbour has said yes, so the family is told instead: say exactly that.
            return "Javiću vašoj porodici. Doviđenja." if lang == "sr" else "I'll let your family know. Goodbye."
        return (
            "Javiću komšiji da svrati do vas. Doviđenja."
            if lang == "sr"
            else "I'll ask your neighbour to stop by. Goodbye."
        )
    return neutral_closing(lang)


def neutral_closing(lang: Lang) -> str:
    """A goodbye that promises nothing. Used whenever the service is not sure what comes next."""
    return "Hvala. Doviđenja." if lang == "sr" else "Thank you. Goodbye."


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
    )
