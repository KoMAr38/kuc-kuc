"""The public demo: prove a phone number is yours, then watch the service call it.

Nothing here dials. A run publishes one ``check.requested`` event for a temporary demo
person and the rest of the system does what it does for anyone else. The page then polls
the timeline of that one episode, which only the visitor who started it may read.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kuckuc.deps import Deps
from kuckuc.models import DemoSession, Person, PersonStatus, finished_for_pages
from kuckuc.web.demo_limits import (
    DemoRefused,
    address_key,
    call_cost_usd,
    check_can_start,
    daily_cap_usd,
    demo_day,
    hash_value,
    normalise_phone,
    run_quotas,
    verify_cost_usd,
)

log = logging.getLogger(__name__)

SESSION_HOURS = 24
# secrets.token_urlsafe(32): 43 letters, digits, "-" and "_".
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
VERIFY_STARTS_PER_IP = 10
# Per number, from any address: our account must not be made to ring or text a stranger again and again.
VERIFY_STARTS_PER_NUMBER = 3
# Checking a code is a request to Twilio too: one address may not check without end.
VERIFY_CHECKS_PER_IP = 30
# Spoken by the English call, so Fahrenheit.
SCENARIOS: dict[str, tuple[str, float]] = {"heat": ("heat", 104), "cold": ("cold", 5)}
_STATUS = {
    "bad_number": 400,
    "country": 400,
    "number_type": 400,
    "own_number": 400,
    "phone_limit": 429,
    "ip_limit": 429,
    "budget": 503,
    "busy": 503,
    "ended": 503,
    "low_balance": 503,
}


class VerifyStart(BaseModel):
    phone: str
    channel: Literal["sms", "call"] = "sms"


class VerifyCheck(BaseModel):
    phone: str
    code: str


class Run(BaseModel):
    token: str
    scenario: Literal["heat", "cold"]


def _refused(code: str, status: int | None = None) -> JSONResponse:
    return JSONResponse({"code": code}, status_code=status or _STATUS.get(code, 400))


def demo_router(deps: Deps) -> APIRouter:
    router = APIRouter(prefix="/api/demo")

    def today():
        return demo_day(deps.clock.now())

    def ip_hash(request: Request) -> str:
        # The platform appends the address it saw as the last entry. Earlier entries are
        # whatever the client chose to send, so they are not used.
        forwarded = request.headers.get("x-forwarded-for", "")
        address = forwarded.split(",")[-1].strip() or (request.client.host if request.client else "")
        return hash_value(address_key(address), deps.settings.hash_salt)

    def demo_number(raw: str) -> tuple[str, str]:
        """A number the demo may call, or DemoRefused. The service's own number is never one:
        people sometimes type the number that has just called them."""
        phone, country = normalise_phone(raw, countries=tuple(deps.settings.demo_countries))
        if deps.settings.twilio_from_number and phone == deps.settings.twilio_from_number:
            raise DemoRefused("own_number")
        return phone, country

    def live_session(token: str) -> DemoSession | None:
        # Every token this service hands out has this form; anything else never reaches the store.
        if not TOKEN.fullmatch(token or ""):
            return None
        session = deps.store.get_demo_session_by_token(token)
        if session is None or session.expires_at <= deps.clock.now():
            return None
        return session

    @router.post("/verify/start")
    def verify_start(body: VerifyStart, request: Request):
        try:
            phone, country = demo_number(body.phone)
            phone_hash, address = hash_value(phone, deps.settings.hash_salt), ip_hash(request)
            check_can_start(deps.store, deps.verify, deps.settings, phone_hash, address, country, today())
        except DemoRefused as refused:
            return _refused(refused.code)
        # Sending a code costs money too: neither one address nor one number may ask for codes without end,
        # and every code counts against the day's budget. Counted in one step, so parallel requests
        # cannot all pass on the same count.
        cost = verify_cost_usd(country, body.channel, deps.settings)
        quotas = [
            (f"code-ip:{address}", VERIFY_STARTS_PER_IP, "ip_limit"),
            (f"code:{phone_hash}", VERIFY_STARTS_PER_NUMBER, "phone_limit"),
        ]
        refusal = deps.store.reserve_demo(today(), quotas, cost, daily_cap_usd(today(), deps.settings))
        if refusal:
            return _refused(refusal)
        try:
            deps.verify.start(phone, body.channel)
        except Exception as exc:
            log.error("verification could not be started (%s)", type(exc).__name__)
            deps.store.add_demo_spend(today(), -cost)  # nothing was sent; the attempt still counts
            return _refused("verify_failed", 502)
        return {"ok": True}

    @router.post("/verify/check")
    def verify_check(body: VerifyCheck, request: Request):
        try:
            phone, country = demo_number(body.phone)
        except DemoRefused as refused:
            return _refused(refused.code)
        quota = [(f"check-ip:{ip_hash(request)}", VERIFY_CHECKS_PER_IP, "ip_limit")]
        refusal = deps.store.reserve_demo(today(), quota, 0.0, daily_cap_usd(today(), deps.settings))
        if refusal:
            return _refused(refusal)
        try:
            approved = deps.verify.check(phone, body.code.strip())
        except Exception as exc:
            log.error("verification could not be checked (%s)", type(exc).__name__)
            return _refused("verify_failed", 502)
        if not approved:
            return _refused("wrong_code", 400)

        session = DemoSession(
            id=uuid.uuid4().hex[:16],
            token=secrets.token_urlsafe(32),
            phone_hash=hash_value(phone, deps.settings.hash_salt),
            country=country,
            expires_at=deps.clock.now() + timedelta(hours=SESSION_HOURS),
        )
        deps.store.put_demo_session(session)
        deps.store.put_person(
            Person(
                id=f"demo-{session.id}",
                owner_uid="",
                name="there",
                family_name="",
                gender="f",
                phone=phone,
                lang="en",
                lat=0.0,
                lon=0.0,
                tz="UTC",
                place="demo",
                status=PersonStatus.ACTIVE,
                demo=True,
            )
        )
        return {"token": session.token}

    @router.post("/run")
    def run(body: Run, request: Request):
        session = live_session(body.token)
        if session is None:
            return _refused("session", 401)
        address = ip_hash(request)
        try:
            check_can_start(
                deps.store, deps.verify, deps.settings, session.phone_hash, address, session.country, today()
            )
        except DemoRefused as refused:
            return _refused(refused.code)
        # The run and its calls are counted in one step (the code was counted when it was sent).
        quotas = run_quotas(session.phone_hash, address, deps.settings)
        cost = call_cost_usd(session.country, deps.settings)
        refusal = deps.store.reserve_demo(today(), quotas, cost, daily_cap_usd(today(), deps.settings))
        if refusal:
            return _refused(refusal)

        hazard, value = SCENARIOS[body.scenario]
        episode_id = f"demo-{uuid.uuid4().hex}"
        deps.bus.publish(
            "check.requested",
            {
                "person_id": f"demo-{session.id}",
                "kind": "demo",
                "episode_id": episode_id,
                "demo_session_id": session.id,
                "hazard": hazard,
                "value": value,
            },
        )
        return {"episode_id": episode_id}

    @router.get("/timeline/{episode_id}")
    def timeline(episode_id: str, token: str = "", after: int = 0):
        session = live_session(token)
        if session is None:
            return _refused("forbidden", 403)
        episode = deps.store.get_episode(episode_id)
        if episode is None:
            # Just requested and not picked up yet. Nothing to show, nothing to reveal.
            return {"events": [], "closed": False}
        if episode.demo_session_id != session.id:
            return _refused("forbidden", 403)
        rows = deps.store.list_timeline(episode_id)
        return {
            "events": [
                {"seq": e.seq, "at": e.at.isoformat(), "type": e.type, "public": e.public}
                for e in rows
                if e.seq > max(after, 0)
            ],
            "closed": finished_for_pages(episode, rows, deps.clock.now()),
        }

    return router
