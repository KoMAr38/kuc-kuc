"""Settings read from the environment (prefix ``KUCKUC_``). Every safety number lives here."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # Values pasted into a secret store often arrive with a newline on the end; strip it.
    model_config = SettingsConfigDict(
        env_prefix="KUCKUC_", env_file=".env", extra="ignore", str_strip_whitespace=True
    )

    # Where things run.
    project: str = ""
    region: str = "europe-west1"
    voice_public_url: str = "http://localhost:8082"
    web_public_url: str = "http://localhost:8080"
    engine_url: str = "http://localhost:8081"
    invoker_email: str = ""  # the service account Cloud Tasks and Pub/Sub call us as
    bucket: str = ""

    # Telephony. Filled from Secret Manager on Cloud Run; empty everywhere else.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_verify_sid: str = ""
    twilio_from_number: str = ""

    # Speech. Chirp 3 HD voice names look like "sr-RS-Chirp3-HD-Aoede".
    gemini_model: str = "gemini-3.8-flash"
    gemini_location: str = "eu"
    gemini_thinking_level: str = "LOW"
    tts_voice_sr: str = "sr-RS-Chirp3-HD-Leda"  # chosen by the family from three samples
    tts_voice_en: str = "en-US-Chirp3-HD-Aoede"
    tts_endpoint: str = "eu-texttospeech.googleapis.com"
    tts_rate_sr: float = 0.9  # a little slower than normal, for an older listener on a landline
    tts_rate_en: float = 1.0

    # Family sign-in and enrolment.
    invite_codes: Annotated[tuple[str, ...], NoDecode] = ()
    google_oauth_client_id: str = ""
    hash_salt: str = "dev-only-salt"
    session_secret: str = "dev-only-session-secret"  # signs the sign-in cookie; from Secret Manager in the cloud
    telegram_bot_name: str = ""  # the bot's username, without @; empty until a bot exists
    telegram_bot_token: str = ""  # from Secret Manager; only the engine has it
    telegram_webhook_secret: str = ""  # part of the address Telegram calls; the webhook is closed while empty

    # Sections of the site that are live: "forecast", "family", "loadtest".
    features: Annotated[set[str], NoDecode] = set()
    github_url: str = ""  # shown on the site once the repository is public

    # Limits that protect the person being called.
    max_person_calls_per_day: int = 6
    quiet_start_hour: int = 8
    quiet_end_hour: int = 20

    # Hazard thresholds.
    heat_apparent_c: float = 35.0
    cold_temp_c: float = -10.0
    wind_gust_kmh: float = 80.0
    snow_cm: float = 20.0

    # Public demo guardrails.
    demo_countries: tuple[str, ...] = ("US", "CA", "GB", "NO")
    demo_runs_per_phone: int = 2
    demo_runs_per_ip: int = 6
    demo_cap_launch_usd: float = 15.0
    demo_cap_usd: float = 5.0
    demo_min_balance_usd: float = 10.0
    demo_launch_day: date = date(2026, 11, 20)
    demo_end_day: date = date(2026, 12, 4)
    # Twilio list prices read on 2026-11-13, per minute to a mobile, calling from a US number.
    # GB is the surcharged rate that applies to calls arriving from outside Europe.
    demo_price_per_min_usd: dict[str, float] = {"US": 0.014, "CA": 0.014, "GB": 0.473, "NO": 0.077}
    verify_fee_usd: float = 0.06  # $0.05 per successful verification plus the text message
    demo_retry_s: int = 30
    demo_followup_s: int = 45

    @field_validator("features", "invite_codes", mode="before")
    @classmethod
    def _plain_or_json_list(cls, value):
        """Lists arrive from the environment as text. Accept JSON (["a","b"]) and also the plain
        spellings a shell or a person produces ([a], "a, b", "a b"): a service must never refuse
        to start because a list lost its quotes on the way."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        try:
            parsed = json.loads(text) if text else []
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except ValueError:
            pass
        return [part for part in text.strip("[](){}").replace(",", " ").replace('"', " ").replace("'", " ").split() if part]
