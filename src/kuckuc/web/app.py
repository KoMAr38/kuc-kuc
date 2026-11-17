"""The public web service: the landing page, the demo and, later, the family page."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from kuckuc.deps import Deps
from kuckuc.web.demo import demo_router
from kuckuc.web.family import family_router, verify_google_credential

WEB_DIR = Path(__file__).resolve().parent

# Everything the pages load comes from this service: no outside scripts, fonts or trackers.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


# The family page alone may also load Google's sign-in button, and only from Google's sign-in address.
GOOGLE_SIGN_IN = "https://accounts.google.com/gsi/"
CONTENT_SECURITY_POLICY_SIGN_IN = (
    f"default-src 'self'; script-src 'self' {GOOGLE_SIGN_IN}client; style-src 'self' {GOOGLE_SIGN_IN}style; "
    f"frame-src {GOOGLE_SIGN_IN}; connect-src 'self' {GOOGLE_SIGN_IN}; img-src 'self' data:; "
    "media-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)

SESSION_DAYS = 14


def _httpx_get(url: str, params: dict) -> dict:
    import httpx

    reply = httpx.get(url, params=params, timeout=10)
    reply.raise_for_status()
    return reply.json()


def create_web_app(
    deps: Deps,
    verify_credential: Callable[[str, str], dict] = verify_google_credential,
    http_get: Callable[[str, dict], dict] = _httpx_get,
) -> FastAPI:
    """``verify_credential(token, client_id)`` checks a Google sign-in token and returns its claims.
    ``http_get(url, params)`` fetches JSON from Open-Meteo (place names and forecasts)."""
    app = FastAPI(title="kuc-kuc web", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    # Pages ask for /static/x.js?v=<this>: a new release has a new name, so no browser keeps the old file.
    asset_version = hashlib.sha256(
        b"".join(f.read_bytes() for f in sorted((WEB_DIR / "static").rglob("*")) if f.is_file())
    ).hexdigest()[:10]
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(demo_router(deps))
    app.include_router(family_router(deps, verify_credential, http_get))
    # The sign-in cookie is signed, not readable by scripts, and sent over HTTPS only once the site is on HTTPS.
    app.add_middleware(
        SessionMiddleware,
        secret_key=deps.settings.session_secret,
        session_cookie="kuckuc_session",
        max_age=SESSION_DAYS * 86400,
        same_site="lax",
        https_only=deps.settings.web_public_url.startswith("https://"),
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        google = request.url.path == "/family" and deps.settings.google_oauth_client_id
        policy = CONTENT_SECURITY_POLICY_SIGN_IN if google else CONTENT_SECURITY_POLICY
        response.headers.setdefault("Content-Security-Policy", policy)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/static/"):
            # Ask the server each time (a cheap 304 when nothing changed): a browser must never keep
            # running yesterday's script after a release.
            response.headers.setdefault("Cache-Control", "no-cache")
        # Google's sign-in button learns which site is asking from the Referer header, so the sign-in
        # page sends its origin (never the path) to other sites; every other page sends nothing.
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin" if google else "no-referrer")
        return response

    def page(request: Request, name: str, **extra):
        context = {
            "github_url": deps.settings.github_url,
            "features": deps.settings.features,
            # Only a signed-in family sees the way to the family page: nobody else could sign in there.
            "signed_in": bool(request.session.get("uid")),
            "v": asset_version,
            **extra,
        }
        return templates.TemplateResponse(request, name, context)

    def latest_loadtest() -> dict | None:
        return deps.store.get_stat_doc("loadtest_latest")

    @app.get("/")
    def index(request: Request):
        return page(request, "index.html")

    @app.get("/family")
    def family(request: Request):
        """The same page for everyone: it holds no data. What a signed-in user sees comes from the API."""
        return page(
            request,
            "family.html",
            google_client_id=deps.settings.google_oauth_client_id,
            intro_hours=(deps.settings.quiet_start_hour, deps.settings.intro_end_hour),
            # Typing an email instead of signing in exists only where the Google check was replaced (preview, tests).
            local_sign_in=verify_credential is not verify_google_credential,
        )

    @app.get("/architecture")
    def architecture(request: Request):
        """Describes only what is live: sections for parts not in ``settings.features`` are left out."""
        return page(request, "architecture.html", stats=deps.store.stats(), loadtest=latest_loadtest())

    @app.get("/api/stats")
    def stats() -> dict:
        return {**deps.store.stats(), "loadtest": latest_loadtest()}

    # Cloud Run's front end keeps /healthz for itself, so the check is also served at /health.
    @app.get("/health")
    @app.get("/healthz")
    def health() -> dict:
        return {"ok": True}

    return app
