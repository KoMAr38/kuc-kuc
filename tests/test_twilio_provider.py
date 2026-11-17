import pytest
import twilio.request_validator

from kuckuc.telephony.twilio_provider import TelephonyError, TwilioTelephony, TwilioVerify

NUMBER = "+15005550006"
CALLEE = "+15005550009"
API = "https://api.twilio.com/2010-04-01/Accounts/ACx"


class Reply:
    def __init__(self, status_code, content=b"", data=None):
        self.status_code = status_code
        self.content = content
        self._data = data or {}

    def json(self):
        return self._data


class FakeHttp:
    """Answers requests from a prepared list and remembers what was asked."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.requested: list[str] = []
        self.calls: list[tuple] = []

    def _next(self, method, url, data, auth):
        self.requested.append(url)
        self.calls.append((method, url, data, auth))
        reply = self._replies.pop(0)
        if isinstance(reply, int):
            return Reply(reply)
        if isinstance(reply, tuple):
            status, body = reply
            return Reply(status, body) if isinstance(body, bytes) else Reply(status, data=body)
        return reply

    def get(self, url, auth=None):
        return self._next("GET", url, None, auth)

    def post(self, url, data=None, auth=None):
        return self._next("POST", url, data, auth)

    def delete(self, url, auth=None):
        return self._next("DELETE", url, None, auth)


def telephony(http=None, **kw):
    return TwilioTelephony("ACx", "secret", NUMBER, http=http, sleep=kw.get("sleep", lambda s: None))


def test_twilio_signature_roundtrip():
    t = TwilioTelephony(account_sid="ACx", auth_token="secret", from_number="+15005550006", http=None)
    url, params = "https://voice.example/twiml/c1", {"CallSid": "CA1", "CallStatus": "in-progress"}
    sig = twilio.request_validator.RequestValidator("secret").compute_signature(url, params)
    assert t.valid_signature(url, params, sig) is True
    assert t.valid_signature(url, params, "wrong") is False
    assert t.valid_signature(url, {**params, "CallStatus": "completed"}, sig) is False
    assert t.valid_signature(url + "?n=2", params, sig) is False


def test_place_call_sends_the_agreed_limits_and_returns_the_call_sid():
    http = FakeHttp([(201, {"sid": "CA123"})])
    sid = telephony(http).place_call(CALLEE, "https://voice/twiml/c1", "https://voice/status/c1", 25, 60)
    assert sid == "CA123"
    method, url, data, auth = http.calls[0]
    assert (method, url, auth) == ("POST", f"{API}/Calls.json", ("ACx", "secret"))
    assert data == {
        "To": CALLEE,
        "From": NUMBER,
        "Url": "https://voice/twiml/c1",
        "Method": "POST",
        "StatusCallback": "https://voice/status/c1",
        "StatusCallbackMethod": "POST",
        "Timeout": "25",
        "TimeLimit": "60",
    }


def test_refused_call_raises_without_quoting_the_number():
    http = FakeHttp([(400, {"code": 21215, "message": f"Account not authorized to call {CALLEE}."})])
    with pytest.raises(TelephonyError) as e:
        telephony(http).place_call(CALLEE, "https://voice/twiml/c1", "https://voice/status/c1")
    assert CALLEE not in str(e.value) and "21215" in str(e.value)


def test_fetch_recording_retries_until_available():
    http = FakeHttp([404, 404, (200, b"RIFF....")])
    t = TwilioTelephony("ACx", "secret", "+15005550006", http=http, sleep=lambda s: None)
    assert t.fetch_recording("https://api.twilio.com/r1").startswith(b"RIFF")
    assert http.requested == ["https://api.twilio.com/r1.wav"] * 3


def test_fetch_recording_waits_half_a_second_between_tries_and_gives_up_after_four():
    http = FakeHttp([404, 404, 404, 404])
    waits = []
    with pytest.raises(TelephonyError):
        telephony(http, sleep=waits.append).fetch_recording("https://api.twilio.com/r1")
    assert len(http.requested) == 4 and waits == [0.5, 0.5, 0.5]


def test_credentials_are_never_sent_to_a_host_that_is_not_the_provider():
    http = FakeHttp([(200, b"RIFF")])
    with pytest.raises(TelephonyError):
        telephony(http).fetch_recording("https://evil.example/r1")
    with pytest.raises(TelephonyError):
        telephony(http).fetch_recording("https://api.twilio.com.evil.example/r1")
    telephony(http).delete_recording("https://evil.example/r1")
    assert http.requested == []


def test_delete_recording_removes_it_at_the_provider_and_never_raises():
    http = FakeHttp([204])
    telephony(http).delete_recording(f"{API}/Recordings/RE1")
    assert http.calls[0][:2] == ("DELETE", f"{API}/Recordings/RE1.json")
    telephony(FakeHttp([500])).delete_recording(f"{API}/Recordings/RE1")


def verify(http):
    return TwilioVerify("ACx", "secret", "VAx", http=http)


def test_verify_start_sends_code_by_the_chosen_channel():
    http = FakeHttp([(201, {"status": "pending"})])
    verify(http).start(CALLEE, "call")
    method, url, data, auth = http.calls[0]
    assert (method, url) == ("POST", "https://verify.twilio.com/v2/Services/VAx/Verifications")
    assert data == {"To": CALLEE, "Channel": "call"} and auth == ("ACx", "secret")


def test_verify_start_failure_raises_without_the_number():
    http = FakeHttp([(429, {"code": 60203, "message": f"Max send attempts reached for {CALLEE}"})])
    with pytest.raises(TelephonyError) as e:
        verify(http).start(CALLEE, "sms")
    assert CALLEE not in str(e.value)


def test_verify_check_is_true_only_when_approved():
    assert verify(FakeHttp([(200, {"status": "approved"})])).check(CALLEE, "123456") is True
    assert verify(FakeHttp([(200, {"status": "pending"})])).check(CALLEE, "000000") is False
    assert verify(FakeHttp([(404, {"code": 20404})])).check(CALLEE, "123456") is False
    http = FakeHttp([(200, {"status": "approved"})])
    verify(http).check(CALLEE, "123456")
    assert http.calls[0][1:3] == (
        "https://verify.twilio.com/v2/Services/VAx/VerificationCheck",
        {"To": CALLEE, "Code": "123456"},
    )


def test_balance_is_read_as_dollars():
    http = FakeHttp([(200, {"balance": "17.4250", "currency": "USD"})])
    assert verify(http).balance_usd() == 17.425
    assert http.calls[0][:2] == ("GET", f"{API}/Balance.json")


def test_unreadable_balance_raises_so_the_demo_can_close_itself():
    with pytest.raises(TelephonyError):
        verify(FakeHttp([(500, {})])).balance_usd()


def test_only_the_providers_number_errors_count_as_a_refused_number():
    from kuckuc.telephony.twilio_provider import TelephonyError

    assert TelephonyError("x", status=400, code="21215").refused is True  # no permission for that country
    assert TelephonyError("x", status=400, code="21211").refused is True  # not a valid number
    assert TelephonyError("x", status=401, code="20003").refused is False  # our credentials
    assert TelephonyError("x", status=404, code="20404").refused is False  # our account id
    assert TelephonyError("x", status=400, code="21212").refused is False  # our own From number
    assert TelephonyError("x", status=429, code="20429").refused is False
