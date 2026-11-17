"""What the family is told, in Serbian and English.

Sentences keep names in the form they were entered (no grammatical cases are applied), and
avoid past-tense verbs that would need the person's gender.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from kuckuc.models import Lang
from kuckuc.store import Store

log = logging.getLogger(__name__)

KEYS = (
    "alert_created",
    "call_ok",
    "call_unwell",
    "neighbor_called",
    "neighbor_going",
    "resolved_ok",
    "needs_help_194",
    "nobody_reachable",
    "neighbor_unconfirmed",
    "headsup_ok",
    "headsup_missed",
    "consent_yes",
    "consent_no",
    "consent_missing",
    "call_blocked",
    "lost_track",
    "intro_lost",
)

_HAZARD_WORDS = {
    "sr": {"heat": "velika vrućina", "cold": "velika hladnoća", "wind": "jak vetar", "snow": "mnogo snega"},
    "en": {"heat": "extreme heat", "cold": "extreme cold", "wind": "strong wind", "snow": "heavy snow"},
}
_HAZARD_UNKNOWN = {"sr": "opasno vreme", "en": "dangerous weather"}

_MESSAGES: dict[str, dict[str, str]] = {
    "sr": {
        "alert_created": "Stiže {hazard} tamo gde živi {name}. Kuc-kuc će zvati i javiti ti kako je.",
        "call_ok": "{name} se javlja i kaže da je dobro.",
        "call_unwell": "HITNO: {name} kaže da nije dobro. Zovemo komšiju.",
        "neighbor_called": "{neighbor} dobija poziv da ode i proveri kako je {name}.",
        "neighbor_going": "{neighbor} kaže da ide da proveri kako je {name}. Uskoro pitamo šta je bilo.",
        "resolved_ok": "{neighbor} javlja da je {name} dobro. Uzbuna je završena.",
        "needs_help_194": "HITNO: {neighbor} javlja da {name} treba pomoć. Pozovi hitnu pomoć 194.",
        "nobody_reachable": (
            "HITNO: {name} se ne javlja i nijedan komšija nije dostupan. Pozovi je ili nekoga ko je blizu."
        ),
        "neighbor_unconfirmed": (
            "HITNO: {neighbor} ide da proveri kako je {name}, ali se više ne javlja na telefon. "
            "Ne znamo kako je {name}. Pozovi nekoga ko je blizu."
        ),
        "headsup_ok": "Najava za sutra je preneta. {name} kaže da je dobro.",
        "headsup_missed": "Najava za sutra nije preneta: {name} se ne javlja. Sutra ide redovna provera.",
        "consent_yes": "{name}: pristanak je dat. Pozivi su uključeni.",
        "consent_no": "{name}: pristanak nije dat. Pozivi su isključeni.",
        "consent_missing": (
            "{name}: nema odgovora na uvodni poziv posle tri pokušaja. "
            "Razgovarajte prvo, pa pokreni uvodni poziv ponovo."
        ),
        "call_blocked": "HITNO: poziv za {name} nije obavljen jer je sistem zaustavio pozive. Proveri je sam.",
        "lost_track": "HITNO: provera za {name} je zastala. Ne znamo kako je {name}. Pozovi nekoga ko je blizu.",
        "intro_lost": "{name}: uvodni poziv nije završen. Pokreni ga ponovo sa svoje stranice.",
    },
    "en": {
        "alert_created": "There will be {hazard} where {name} lives. Kuc-kuc will call and tell you how it went.",
        "call_ok": "{name} answered and says everything is fine.",
        "call_unwell": "URGENT: {name} says they are not well. We are calling the neighbour.",
        "neighbor_called": "{neighbor} is being called and asked to check on {name}.",
        "neighbor_going": "{neighbor} says they are going to check on {name}. We will ask shortly how it went.",
        "resolved_ok": "{neighbor} reports that {name} is fine. The alert is closed.",
        "needs_help_194": "URGENT: {neighbor} reports that {name} needs help. Call the emergency number now.",
        "nobody_reachable": (
            "URGENT: {name} is not answering and no neighbour could be reached. Call them or someone nearby."
        ),
        "neighbor_unconfirmed": (
            "URGENT: {neighbor} went to check on {name} but is no longer answering the phone. "
            "We do not know how {name} is. Call someone nearby."
        ),
        "headsup_ok": "Tomorrow's warning was delivered. {name} says everything is fine.",
        "headsup_missed": "Tomorrow's warning was not delivered: {name} did not answer. The regular check runs tomorrow.",
        "consent_yes": "{name}: consent given. Calls are on.",
        "consent_no": "{name}: consent not given. Calls are off.",
        "consent_missing": (
            "{name}: no answer to the introduction call after three tries. Talk to them first, then start it again."
        ),
        "call_blocked": "URGENT: the call to {name} was not made because calls are stopped. Check on them yourself.",
        "lost_track": "URGENT: the check on {name} stalled. We do not know how {name} is. Call someone nearby.",
        "intro_lost": "{name}: the introduction call did not finish. Start it again from your page.",
    },
}
_FALLBACK = {"sr": "Kuc-kuc: novo stanje za {name} ({key}).", "en": "Kuc-kuc: an update about {name} ({key})."}


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


def render_message(key: str, lang: Lang, params: dict[str, str]) -> str:
    """The message text. A key nobody wrote a sentence for still produces something readable."""
    values = _Blank(params)
    values["hazard"] = _HAZARD_WORDS[lang].get(params.get("hazard", ""), _HAZARD_UNKNOWN[lang])
    values["key"] = key
    return _MESSAGES[lang].get(key, _FALLBACK[lang]).format_map(values)


LINKED = {
    "sr": "Kuc-kuc je povezan. Ovde ćeš dobijati poruke o pozivima.",
    "en": "Kuc-kuc is connected. Messages about the calls will arrive here.",
}
NOT_LINKED = "Kuc-kuc: this link is not valid any more. Open your page on the site and use the Telegram link there."


class TelegramNotifier:
    """Sends the family their notices through a Telegram bot.

    ``http_post(url, json_body)`` does the request. A user who has not connected a chat gets
    nothing. Urgent notices ring; the others arrive silently. A failed send is logged and
    reported as not delivered, never raised: a message that cannot be delivered must not stop
    the calls it is about.
    """

    def __init__(self, store: Store, token: str, http_post: Callable[[str, dict], object]) -> None:
        self._store = store
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._http_post = http_post

    def send(self, owner_uid: str, key: str, params: dict[str, str], urgent: bool) -> bool:
        user = self._store.get_user(owner_uid) if owner_uid else None
        if user is None or not user.telegram_chat_id:
            return False
        body = {
            "chat_id": user.telegram_chat_id,
            "text": render_message(key, user.lang, params),
            "disable_notification": not urgent,
        }
        try:
            self._http_post(self._url, body)
        except Exception as exc:
            # The address contains the bot's token, so neither it nor the error text is logged.
            log.error("family notice %s not delivered (%s)", key, type(exc).__name__)
            return False
        return True


def register_telegram_webhook(token: str, web_url: str, secret: str, http_post: Callable[[str, dict], object]) -> bool:
    """Tell Telegram to deliver what people write to the bot to our web service.

    Returns False when nothing was registered: no bot, no secret address, or Telegram unreachable.
    """
    if not token or not secret:
        return False
    body = {"url": f"{web_url.rstrip('/')}/telegram/webhook/{secret}", "allowed_updates": ["message"]}
    try:
        http_post(f"https://api.telegram.org/bot{token}/setWebhook", body)
    except Exception as exc:
        # Both addresses are secrets, so only the kind of failure is logged.
        log.error("Telegram webhook not registered (%s)", type(exc).__name__)
        return False
    return True


def telegram_post(client) -> Callable[[str, dict], None]:
    """The real ``http_post`` for Telegram, on an ``httpx.Client``. It raises when Telegram refuses."""
    # httpx writes every address it calls to the log, and these addresses contain the bot's token.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def post(url: str, body: dict) -> None:
        client.post(url, json=body).raise_for_status()

    return post
