"""The family's side: sign in, enrol someone, see what happened, call now, pause.

Everything under ``/api/family`` belongs to the signed-in user. A person who is not theirs
does not exist as far as they can tell: the answer is 404, never "forbidden". Phone numbers
go in, but only their last three digits ever come back out.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.resources
import logging
import re
import secrets
import uuid
from collections.abc import Callable
from functools import lru_cache
from zoneinfo import ZoneInfo
from typing import Literal

import phonenumbers
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from kuckuc.deps import Deps
from kuckuc.engine.schedule import daytime_start
from kuckuc.engine.notify import LINKED, NOT_LINKED
from kuckuc.budget import budget_caps
from kuckuc.models import (
    OWN_PAYER,
    EpisodeKind,
    EpisodeState,
    Lang,
    Neighbor,
    PageLang,
    Person,
    PersonStatus,
    User,
    finished_for_pages,
)
from kuckuc.rules import hazards_for
from kuckuc.weather import cell_of, fetch_forecast

log = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
MAX_NEIGHBORS = 3
MAX_PERSONS_PER_USER = 5
_IN_PROGRESS = (EpisodeState.CALLING_PERSON, EpisodeState.CALLING_NEIGHBOR, EpisodeState.WAITING_FOLLOWUP)
# Never a paid or special line: every enrolment rings the number, and the checks ring it again.
_NOT_A_PHONE = frozenset(
    {
        phonenumbers.PhoneNumberType.PREMIUM_RATE,
        phonenumbers.PhoneNumberType.TOLL_FREE,
        phonenumbers.PhoneNumberType.SHARED_COST,
        phonenumbers.PhoneNumberType.UAN,
        phonenumbers.PhoneNumberType.VOICEMAIL,
        phonenumbers.PhoneNumberType.PAGER,
        phonenumbers.PhoneNumberType.PERSONAL_NUMBER,  # UK 070: charged at a high rate
    }
)


def verify_google_credential(credential: str, client_id: str) -> dict:
    """Checks a Google sign-in token and returns its claims. Raises ValueError if it is not valid for us."""
    if not client_id:
        # Without our client id the check below would have nothing to compare the token's audience with.
        raise ValueError("Google sign-in is not set up")
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)


class Credential(BaseModel):
    credential: str


class NeighborIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    phone: str


class PersonIn(BaseModel):
    """What the family tells us. There is deliberately no street address field, and extra fields are refused."""

    model_config = ConfigDict(extra="forbid")
    invite_code: str
    name: str = Field(min_length=1, max_length=60)  # as spoken in the call: "baba Vera"
    family_name: str = Field(min_length=1, max_length=80)  # as said to the person: "vaš unuk Petar"
    gender: Literal["f", "m"]
    phone: str
    lang: Lang  # the language of the calls; the page itself stays in Serbian or English
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    place: str = Field(min_length=1, max_length=80)
    neighbors: list[NeighborIn] = Field(default_factory=list, max_length=MAX_NEIGHBORS)


class Pause(BaseModel):
    paused: bool


class IntroRequest(BaseModel):
    now: bool = False  # the family confirmed on the page that it should ring even though it is night there
    # For someone recorded as "no": the family has talked to them since, and they agreed to be asked again.
    agreed: bool = False


class Language(BaseModel):
    lang: PageLang


@lru_cache(maxsize=1)
def _timezone_finder():
    from timezonefinder import TimezoneFinder

    return TimezoneFinder()


@lru_cache(maxsize=1)
def _country_by_timezone() -> dict[str, str]:
    table = importlib.resources.files("tzdata.zoneinfo").joinpath("zone.tab").read_text(encoding="utf-8")
    rows = (line.split("\t") for line in table.splitlines() if line and not line.startswith("#"))
    return {row[2]: row[0] for row in rows if len(row) >= 3}


def timezone_at(lat: float, lon: float) -> str:
    return _timezone_finder().timezone_at(lat=lat, lng=lon) or "UTC"


def to_e164(raw: str, tz: str) -> str | None:
    """A dialable number in international form, or None. A number written the local way
    ("018 4 123 456") is read as a number of the country the person lives in."""
    try:
        number = phonenumbers.parse(raw, _country_by_timezone().get(tz))
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number) or phonenumbers.number_type(number) in _NOT_A_PHONE:
        return None
    if phonenumbers.region_code_for_number(number) == "001":
        return None  # satellite and other numbers of no country
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def mask(phone: str) -> str:
    return f"••• {phone[-3:]}"


def family_router(
    deps: Deps, verify_credential: Callable[[str, str], dict], http_get: Callable[[str, dict], dict]
) -> APIRouter:
    """``http_get(url, params)`` returns decoded JSON; it reaches Open-Meteo for place names and forecasts,
    so the visitor's browser never talks to anyone but this site."""
    router = APIRouter()

    def current_user(request: Request) -> User:
        uid = request.session.get("uid")
        user = deps.store.get_user(uid) if uid else None
        if user is None:
            raise HTTPException(status_code=401)
        return user

    def owned_person(person_id: str, user: User) -> Person:
        person = deps.store.get_person(person_id)
        if person is None or person.owner_uid != user.uid or person.demo or person.sim:
            raise HTTPException(status_code=404)
        return person

    def refused(code: str, status: int) -> JSONResponse:
        return JSONResponse({"code": code}, status_code=status)

    def after(episodes: list) -> str:
        """Names a new episode by the newest one before it. Two quick clicks see the same newest
        episode and name the same new one, which the engine starts once; a click after that one
        has finished names another. (A name by the minute failed at both edges.)"""
        newest = episodes[0].id if episodes else ""
        return hashlib.sha1(newest.encode()).hexdigest()[:12]

    def running(
        person_id: str, kinds: tuple[EpisodeKind, ...] | None = None, states: tuple[EpisodeState, ...] = _IN_PROGRESS
    ) -> bool:
        return any(
            e.state in states and (kinds is None or e.kind in kinds)
            for e in deps.store.list_episodes(person_id, limit=20)
        )

    def intro_at_local(tz: str) -> str | None:
        """The engine plans introductions asked for at night for the morning; this says when, on the
        clock there. None means the calls start now."""
        now = deps.clock.now()
        when = daytime_start(tz, now, deps.settings.quiet_start_hour, deps.settings.intro_end_hour)
        return when.astimezone(ZoneInfo(tz)).strftime("%H:%M") if when > now else None

    # --- signing in ---------------------------------------------------------------------------

    @router.post("/api/auth/google")
    def sign_in(body: Credential, request: Request):
        try:
            claims = verify_credential(body.credential, deps.settings.google_oauth_client_id)
        except Exception:
            return refused("sign_in", 401)
        if not claims.get("sub") or not claims.get("email") or claims.get("email_verified") is not True:
            return refused("sign_in", 401)
        uid = str(claims["sub"])
        user = deps.store.get_user(uid)
        if user is None:
            lang = "sr" if str(claims.get("locale", "")).lower().startswith(("sr", "hr", "bs")) else "en"
            user = User(uid=uid, email=str(claims["email"]), lang=lang, telegram_link_code=secrets.token_urlsafe(18))
            deps.store.put_user(user)
        request.session["uid"] = uid
        return {"ok": True, "email": user.email}

    @router.post("/api/auth/logout")
    def sign_out(request: Request):
        request.session.clear()
        return {"ok": True}

    # --- what the family sees -----------------------------------------------------------------

    def person_view(person: Person) -> dict:
        return {
            "id": person.id,
            "name": person.name,
            "status": person.status.value,
            "place": person.place,
            "lang": person.lang,
            "tz": person.tz,
            "phone": mask(person.phone),
            "neighbors": [
                {"id": n.id, "name": n.name, "status": n.status.value, "phone": mask(n.phone)}
                for n in deps.store.list_neighbors(person.id)
            ],
        }

    def budgets_for(user: User) -> list[dict]:
        """What each payer this user works for has spent and holds, against each of its caps."""
        out = []
        for payer in deps.store.list_payers_for_staff(user.uid):
            for key, cap in budget_caps(payer, deps.clock.now()):
                period, label = key.split(":", 2)[1:]
                if period == "season" and payer.season_end is not None:
                    label = f"{label} – {payer.season_end.isoformat()}"
                used = deps.store.get_budget(key)
                out.append({"payer": payer.name, "period": period, "label": label, "cap_usd": cap,
                            "spent_usd": used["spent"], "reserved_usd": used["reserved"]})  # fmt: skip
        return out

    @router.get("/api/family/me")
    def me(user: User = Depends(current_user)):
        bot = deps.settings.telegram_bot_name
        link = f"https://t.me/{bot}?start={user.telegram_link_code}" if bot and user.telegram_link_code else None
        persons = deps.store.list_persons_by_owner(user.uid)  # demo and simulated people have no owner
        return {
            "uid": user.uid,
            "email": user.email,
            "lang": user.lang,
            "budgets": budgets_for(user),
            "telegram_linked": bool(user.telegram_chat_id),
            "telegram_link_url": link,
            "persons": [person_view(p) for p in persons],
        }

    @router.get("/api/family/persons/{person_id}/episodes")
    def episodes(person_id: str, user: User = Depends(current_user)):
        person = owned_person(person_id, user)
        out = []
        for episode in deps.store.list_episodes(person.id, limit=20):
            rows = deps.store.list_timeline(episode.id)
            out.append(
                {
                    "id": episode.id,
                    "kind": episode.kind.value,
                    "state": episode.state.value,
                    "result": episode.result.value if episode.result else None,
                    "hazard": episode.hazard,
                    "created_at": episode.created_at.isoformat(),
                    # The page keeps watching while this is true; planned checks are not watched.
                    "running": episode.state != EpisodeState.SCHEDULED
                    and not finished_for_pages(episode, rows, deps.clock.now()),
                    "events": [{"seq": e.seq, "at": e.at.isoformat(), "type": e.type, "public": e.public} for e in rows],
                    "calls": [
                        {
                            "id": c.id,
                            "role": c.target.value,
                            "answer": c.answer.value if c.answer else None,
                            "transcript": c.transcript,
                            "has_recording": bool(c.recording_key),
                        }
                        for c in deps.store.list_calls_for_episode(episode.id)
                    ],
                }
            )
        return out

    @router.get("/api/family/recordings/{call_id}")
    def recording(call_id: str, request: Request, user: User = Depends(current_user)):
        call = deps.store.get_call(call_id)
        episode = deps.store.get_episode(call.episode_id) if call else None
        if call is None or episode is None or not call.recording_key:
            raise HTTPException(status_code=404)
        owned_person(episode.person_id, user)
        audio = deps.blobs.get(call.recording_key)
        if audio is None:  # recordings are deleted after 7 days
            raise HTTPException(status_code=404)
        # Safari on an iPhone plays audio only from a server that answers a request for part of it.
        wanted = re.fullmatch(r"bytes=(\d+)-(\d*)", request.headers.get("range", ""))
        if wanted and int(wanted.group(1)) < len(audio):
            first = int(wanted.group(1))
            last = min(int(wanted.group(2) or len(audio) - 1), len(audio) - 1)
            return Response(
                content=audio[first : last + 1],
                status_code=206,
                media_type="audio/wav",
                headers={"Content-Range": f"bytes {first}-{last}/{len(audio)}", "Accept-Ranges": "bytes"},
            )
        return Response(content=audio, media_type="audio/wav", headers={"Accept-Ranges": "bytes"})

    @router.post("/api/family/lang")
    def set_language(body: Language, user: User = Depends(current_user)):
        deps.store.put_user(user.model_copy(update={"lang": body.lang}))
        return {"lang": body.lang}

    @router.get("/api/family/places")
    def places(q: str = "", user: User = Depends(current_user)):
        """Villages and towns by name, for the enrolment form. Only a name and rough coordinates come back."""
        query = q.strip()
        if len(query) < 2:
            return []
        try:
            reply = http_get(GEOCODING_URL, {"name": query, "count": 6, "language": "en", "format": "json"})
            found = reply.get("results") or []
        except Exception as exc:
            log.warning("place search failed (%s)", type(exc).__name__)
            return []
        return [
            {
                "name": str(r.get("name", "")),
                "region": str(r.get("admin1") or ""),
                "country": str(r.get("country") or ""),
                "lat": r["latitude"],
                "lon": r["longitude"],
            }
            for r in found
            if isinstance(r, dict) and "latitude" in r and "longitude" in r
        ]

    @router.get("/api/family/persons/{person_id}/forecast")
    def forecast(person_id: str, user: User = Depends(current_user)):
        person = owned_person(person_id, user)
        try:
            days = fetch_forecast(*cell_of(person.lat, person.lon), http_get)
        except Exception as exc:
            log.warning("forecast for the family page failed (%s)", type(exc).__name__)
            return {"days": []}
        hazards = {h.day: h.hazard for h in hazards_for(days, deps.settings)}
        return {
            "days": [
                {
                    "day": d.day.isoformat(),
                    "hazard": hazards.get(d.day),
                    "temp_min_c": d.temp_min_c,
                    "apparent_max_c": d.apparent_max_c,
                    "gust_max_kmh": d.gust_max_kmh,
                    "snow_cm": d.snow_cm,
                }
                for d in days
            ]
        }

    # --- enrolling ----------------------------------------------------------------------------

    @router.post("/api/family/persons", status_code=201)
    def enrol(body: PersonIn, user: User = Depends(current_user)):
        # The invite code decides who pays for the calls: an organisation's own code puts the person on
        # its budget; the codes in the settings put them on the service's own account.
        if body.invite_code in deps.settings.invite_codes:
            payer_id = OWN_PAYER
        elif (payer := deps.store.find_payer_by_invite(body.invite_code)) is not None:
            payer_id = payer.id
        else:
            return refused("invite", 403)
        if deps.settings.telegram_bot_name and not user.telegram_chat_id:
            # Every notice about the calls goes to Telegram; with no chat it would reach nobody.
            return refused("telegram_first", 409)
        if len(deps.store.list_persons_by_owner(user.uid)) >= MAX_PERSONS_PER_USER:
            return refused("too_many", 409)
        tz = timezone_at(body.lat, body.lon)
        phone = to_e164(body.phone, tz)
        neighbor_phones = [to_e164(n.phone, tz) for n in body.neighbors]
        if phone is None or None in neighbor_phones:
            return refused("bad_number", 422)
        # A neighbour with her own number, a number twice, or the service's own number: "calling the
        # neighbour" would ring her again, or nobody who can go to the door.
        ours = {phone, deps.settings.twilio_from_number} - {""}
        if (
            phone == deps.settings.twilio_from_number  # the service would call itself
            or len(set(neighbor_phones)) < len(neighbor_phones)
            or ours & set(neighbor_phones)
        ):
            return refused("same_number", 422)
        if any(p.phone == phone for p in deps.store.list_persons_by_owner(user.uid)):
            # A second enrolment of the same number would make every call come in pairs.
            return refused("already_enrolled", 409)

        person = Person(
            id=uuid.uuid4().hex,
            owner_uid=user.uid,
            name=body.name.strip(),
            family_name=body.family_name.strip(),
            gender=body.gender,
            phone=phone,
            lang=body.lang,
            lat=round(body.lat, 2),  # enough for a forecast; not enough to find a house
            lon=round(body.lon, 2),
            tz=tz,
            place=body.place.strip(),
            status=PersonStatus.PENDING_CONSENT,
            payer_id=payer_id,
        )
        deps.store.put_person(person)
        neighbors = [
            Neighbor(
                id=uuid.uuid4().hex,
                person_id=person.id,
                order=i,
                name=n.name.strip(),
                phone=neighbor_phone,
                status=PersonStatus.PENDING_CONSENT,
            )
            for i, (n, neighbor_phone) in enumerate(zip(body.neighbors, neighbor_phones, strict=True))
        ]
        for neighbor in neighbors:
            deps.store.put_neighbor(neighbor)
        # Nobody is called about the weather until they have said yes on an introduction call.
        deps.bus.publish("intro.requested", {"person_id": person.id})
        for neighbor in neighbors:
            deps.bus.publish("intro.requested", {"neighbor_id": neighbor.id})
        log.info("person %s enrolled with %d neighbours", person.id, len(neighbors))
        return {"id": person.id, "intro_at_local": intro_at_local(person.tz)}

    # --- acting -------------------------------------------------------------------------------

    @router.post("/api/family/persons/{person_id}/call-now", status_code=202)
    def call_now(person_id: str, user: User = Depends(current_user)):
        person = owned_person(person_id, user)
        if person.status != PersonStatus.ACTIVE:
            return refused("not_active", 409)
        episodes = deps.store.list_episodes(person.id, limit=20)
        # A neighbour's introduction never calls her, so it does not stand in the way of a check.
        if any(e.state in _IN_PROGRESS and e.kind != EpisodeKind.INTRO_NEIGHBOR for e in episodes):
            return refused("busy", 409)
        # The engine creates the episode a few seconds later. Until then a second click passes the
        # check above, so both clicks name the same episode, and the engine starts it once.
        episode_id = f"{person.id}:manual:{after(episodes)}"
        deps.bus.publish("check.requested", {"person_id": person.id, "kind": "manual", "episode_id": episode_id})
        return {"episode_id": episode_id}

    @router.post("/api/family/persons/{person_id}/pause")
    def pause(person_id: str, body: Pause, user: User = Depends(current_user)):
        person = owned_person(person_id, user)
        if person.status not in (PersonStatus.ACTIVE, PersonStatus.PAUSED):
            return refused("not_active", 409)  # pausing and resuming never stand in for consent
        status = PersonStatus.PAUSED if body.paused else PersonStatus.ACTIVE
        deps.store.put_person(person.model_copy(update={"status": status}))
        return {"status": status.value}

    @router.post("/api/family/persons/{person_id}/intro", status_code=202)
    def intro_again(person_id: str, user: User = Depends(current_user)):
        person = owned_person(person_id, user)
        if person.status != PersonStatus.PENDING_CONSENT:
            return refused("not_pending", 409)
        if running(person.id, (EpisodeKind.INTRO,), (*_IN_PROGRESS, EpisodeState.SCHEDULED)):
            return refused("busy", 409)  # one is under way, or planned for the morning
        episodes = deps.store.list_episodes(person.id, limit=20)
        deps.bus.publish("intro.requested", {"person_id": person.id, "episode_id": f"{person.id}:intro:{after(episodes)}"})
        return {"ok": True, "intro_at_local": intro_at_local(person.tz)}

    @router.post("/api/family/persons/{person_id}/neighbors/{neighbor_id}/intro", status_code=202)
    def neighbor_intro_again(
        person_id: str, neighbor_id: str, body: IntroRequest | None = None, user: User = Depends(current_user)
    ):
        """For a neighbour who missed the introduction: the family talks to them, then asks again."""
        person = owned_person(person_id, user)
        neighbor = deps.store.get_neighbor(neighbor_id)
        if neighbor is None or neighbor.person_id != person.id:
            raise HTTPException(status_code=404)
        if neighbor.status not in (PersonStatus.PENDING_CONSENT, PersonStatus.DECLINED):
            return refused("not_pending", 409)
        if neighbor.status == PersonStatus.DECLINED and not (body and body.agreed):
            # A recorded "no" is asked again only once the family says the neighbour agreed to it.
            return refused("agreed_needed", 409)
        planned_or_running = (*_IN_PROGRESS, EpisodeState.SCHEDULED)
        episodes = deps.store.list_episodes(person.id, limit=20)
        if any(
            e.kind == EpisodeKind.INTRO_NEIGHBOR and e.neighbor_id == neighbor.id and e.state in planned_or_running
            for e in episodes
        ):
            return refused("busy", 409)
        now = bool(body and body.now)
        intro_id = f"{neighbor.id}:intro:{after(episodes)}"  # two quick clicks start one introduction
        deps.bus.publish("intro.requested", {"neighbor_id": neighbor.id, "now": now, "episode_id": intro_id})
        log.info("neighbour %s asked again (was %s)", neighbor.id, neighbor.status.value)
        return {"ok": True, "intro_at_local": None if now else intro_at_local(person.tz)}

    # --- Telegram -----------------------------------------------------------------------------

    @router.post("/telegram/webhook/{secret}")
    def telegram_webhook(secret: str, update: dict = Body(default_factory=dict)):
        """Telegram calls this when someone writes to the bot. "/start <code>" connects that chat
        to the user whose page showed the code. The reply travels back in the response itself,
        so this service never needs the bot's token."""
        expected = deps.settings.telegram_webhook_secret
        if not expected or not hmac.compare_digest(secret, expected):
            raise HTTPException(status_code=404)
        message = update.get("message") if isinstance(update, dict) else None
        chat_id = (message or {}).get("chat", {}).get("id")
        words = str((message or {}).get("text") or "").split()
        if chat_id is None or len(words) < 2 or words[0] != "/start":
            return {}
        user = deps.store.find_user_by_link_code(words[1])
        if user is None:
            return {"method": "sendMessage", "chat_id": chat_id, "text": NOT_LINKED}
        # A link works once: a new code replaces it, so a forwarded or reused link can never move the
        # family's messages, urgent ones included, to another chat.
        fresh = secrets.token_urlsafe(18)
        deps.store.put_user(user.model_copy(update={"telegram_chat_id": str(chat_id), "telegram_link_code": fresh}))
        return {"method": "sendMessage", "chat_id": chat_id, "text": LINKED[user.lang]}

    return router
