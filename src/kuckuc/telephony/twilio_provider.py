"""Twilio behind the Telephony and Verify interfaces.

Plain REST calls through an injected HTTP client, so everything except the network is testable.
Error messages carry Twilio's error code, never its message text: that text can quote the
dialled number, and phone numbers must not reach the logs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
from collections.abc import Callable
from typing import Literal
from urllib.parse import urlsplit, urlunsplit


log = logging.getLogger(__name__)

API = "https://api.twilio.com"
VERIFY_API = "https://verify.twilio.com/v2"
RECORDING_RETRIES = 3
RECORDING_RETRY_WAIT_S = 0.5
CALL_SID = re.compile(r"CA[0-9a-f]{32}")
RECORDING_SID = re.compile(r"RE[0-9a-zA-Z]{1,40}")
# Twilio's errors about the dialled number itself: invalid, unreachable, not allowed for that country,
# blocked, not a number, not verified. Every other refusal is about our own account or request.
NUMBER_REFUSALS = frozenset({"21211", "21214", "21215", "21216", "21217", "21219"})


class TelephonyError(RuntimeError):
    """``status`` is the provider's HTTP status (0 if none came back) and ``code`` its error code.
    Neither ever contains the dialled number."""

    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def refused(self) -> bool:
        """The provider refused the dialled number (invalid, unreachable, no permission for its
        country): trying again changes nothing, and the family should check the number."""
        return self.status == 400 and self.code in NUMBER_REFUSALS

    @property
    def ours(self) -> bool:
        """The provider refused our own account or request (credentials, account id, our number):
        the family can do nothing about the number, and trying again changes nothing."""
        return 400 <= self.status < 500 and self.status != 429 and not self.refused


def _error(what: str, reply) -> TelephonyError:
    code = ""
    try:
        code = str(reply.json().get("code", ""))
    except Exception:
        pass
    return TelephonyError(
        f"{what}: HTTP {reply.status_code}" + (f", provider code {code}" if code else ""), reply.status_code, code
    )


class TwilioTelephony:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        http,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sid = account_sid
        self._auth = (account_sid, auth_token)
        self._from = from_number
        self._http = http
        self._sleep = sleep
        self._token = auth_token

    def place_call(
        self, to: str, twiml_url: str, status_url: str, ring_timeout_s: int = 25, time_limit_s: int = 60
    ) -> str:
        reply = self._http.post(
            f"{API}/2010-04-01/Accounts/{self._sid}/Calls.json",
            data={
                "To": to,
                "From": self._from,
                "Url": twiml_url,
                "Method": "POST",
                "StatusCallback": status_url,
                "StatusCallbackMethod": "POST",
                "Timeout": str(ring_timeout_s),
                "TimeLimit": str(time_limit_s),
            },
            auth=self._auth,
        )
        if reply.status_code >= 300:
            raise _error("call not placed", reply)
        return str(reply.json()["sid"])

    def fetch_recording(self, recording_url: str) -> bytes:
        """The recording as WAV. A recording is not always ready the moment the call reports it."""
        self._require_provider_url(recording_url)
        reply = None
        for attempt in range(RECORDING_RETRIES + 1):
            if attempt:
                self._sleep(RECORDING_RETRY_WAIT_S)
            reply = self._http.get(f"{recording_url}.wav", auth=self._auth)
            if reply.status_code == 200:
                return reply.content
        raise _error("recording not available", reply)

    def delete_recording(self, recording_url: str) -> None:
        """Best effort: our own copy is already stored, so a failure here must not fail the call.
        A failure is logged; the sweep when the call ends tries again."""
        try:
            self._require_provider_url(recording_url)
            reply = self._http.delete(f"{recording_url}.json", auth=self._auth)
        except Exception as exc:
            log.warning("recording not deleted at the provider (%s)", type(exc).__name__)
            return
        if reply.status_code not in (200, 204, 404):
            log.warning("recording not deleted at the provider (HTTP %s)", reply.status_code)

    def delete_call_recordings(self, call_sid: str) -> None:
        """Deletes every recording of a finished call at the provider; each failure is logged."""
        if not CALL_SID.fullmatch(call_sid):
            return
        base = f"{API}/2010-04-01/Accounts/{self._sid}"
        reply = self._http.get(f"{base}/Calls/{call_sid}/Recordings.json", auth=self._auth)
        if reply.status_code != 200:
            log.warning("recordings of call %s not listed (HTTP %s)", call_sid, reply.status_code)
            return
        for recording in reply.json().get("recordings") or []:
            sid = str(recording.get("sid", ""))
            if not RECORDING_SID.fullmatch(sid):
                continue
            gone = self._http.delete(f"{base}/Recordings/{sid}.json", auth=self._auth)
            if gone.status_code not in (200, 204, 404):
                log.warning("recording %s not deleted at the provider (HTTP %s)", sid, gone.status_code)

    def valid_signature(self, url: str, params: dict[str, str], signature: str) -> bool:
        """Twilio's X-Twilio-Signature: base64 HMAC-SHA1, keyed with the auth token, of the address
        followed by every posted name and value in name order. Twilio may sign the address with or
        without its default port, so both are tried (as its own SDK does)."""
        if not signature:
            return False
        tail = "".join(f"{name}{params[name]}" for name in sorted(params))
        given = signature.encode()  # as bytes: a forged header with any character is a "no", never an error
        for address in _with_and_without_default_port(url):
            digest = hmac.new(self._token.encode(), f"{address}{tail}".encode(), hashlib.sha1).digest()
            if hmac.compare_digest(base64.b64encode(digest), given):
                return True
        return False

    @staticmethod
    def _require_provider_url(url: str) -> None:
        # The account credentials go out with this request, so it may only go to Twilio.
        if not url.startswith(f"{API}/"):
            raise TelephonyError("recording address is not at the provider")


def _with_and_without_default_port(url: str) -> list[str]:
    """The address exactly as given, and the same with its default port added or taken away. Only the
    port changes: the rest is kept letter for letter, as Twilio signs what it was given."""
    parts = urlsplit(url)
    default = {"https": 443, "http": 80}.get(parts.scheme)
    try:
        port = parts.port
    except ValueError:
        return [url]
    if default is None or port not in (None, default):
        return [url]
    if port is None:
        other = parts._replace(netloc=f"{parts.netloc}:{default}")
    else:
        other = parts._replace(netloc=parts.netloc[: parts.netloc.rindex(":")])
    return [url, urlunsplit(other)]


class TwilioVerify:
    def __init__(self, account_sid: str, auth_token: str, verify_sid: str, http) -> None:
        self._sid = account_sid
        self._auth = (account_sid, auth_token)
        self._service = f"{VERIFY_API}/Services/{verify_sid}"
        self._http = http

    def start(self, phone: str, channel: Literal["sms", "call"]) -> None:
        reply = self._http.post(
            f"{self._service}/Verifications", data={"To": phone, "Channel": channel}, auth=self._auth
        )
        if reply.status_code >= 300:
            raise _error("verification not started", reply)

    def check(self, phone: str, code: str) -> bool:
        reply = self._http.post(
            f"{self._service}/VerificationCheck", data={"To": phone, "Code": code}, auth=self._auth
        )
        return reply.status_code == 200 and reply.json().get("status") == "approved"

    def balance_usd(self) -> float:
        reply = self._http.get(f"{API}/2010-04-01/Accounts/{self._sid}/Balance.json", auth=self._auth)
        if reply.status_code != 200:
            raise _error("balance not available", reply)
        return float(reply.json()["balance"])
