import pytest
from fastapi.testclient import TestClient
from helpers import build_sys

from kuckuc.engine.notify import TelegramNotifier
from kuckuc.models import User
from kuckuc.store import MemoryStore


@pytest.fixture
def web():
    sys = build_sys()
    sys.deps.settings.telegram_webhook_secret = "s3cret"
    client = TestClient(sys.web)
    client.deps = sys.deps
    return client


def linked_store(lang="sr"):
    store = MemoryStore()
    store.put_user(User(uid="u1", email="a@b.c", lang=lang, telegram_chat_id="777"))
    return store


# --- linking a chat --------------------------------------------------------------------------


def test_start_command_links_chat(web):
    web.deps.store.put_user(User(uid="u1", email="a@b.c", telegram_link_code="abc123"))
    r = web.post("/telegram/webhook/s3cret", json={"message": {"chat": {"id": 777}, "text": "/start abc123"}})
    assert r.status_code == 200 and web.deps.store.get_user("u1").telegram_chat_id == "777"


def test_linking_is_confirmed_in_the_chat_in_the_users_language(web):
    web.deps.store.put_user(User(uid="u1", email="a@b.c", lang="sr", telegram_link_code="abc123"))
    reply = web.post(
        "/telegram/webhook/s3cret", json={"message": {"chat": {"id": 777}, "text": "/start abc123"}}
    ).json()
    assert reply["method"] == "sendMessage" and reply["chat_id"] == 777
    assert "Kuc-kuc" in reply["text"] and "povezan" in reply["text"]


def test_unknown_code_links_nobody_and_says_so(web):
    web.deps.store.put_user(User(uid="u1", email="a@b.c", telegram_link_code="abc123"))
    reply = web.post(
        "/telegram/webhook/s3cret", json={"message": {"chat": {"id": 777}, "text": "/start nope"}}
    ).json()
    assert web.deps.store.get_user("u1").telegram_chat_id is None
    assert reply["method"] == "sendMessage" and reply["chat_id"] == 777


def test_start_without_a_code_and_other_messages_change_nothing(web):
    web.deps.store.put_user(User(uid="u1", email="a@b.c", telegram_link_code=""))
    for text in ("/start", "/start ", "hello", ""):
        r = web.post("/telegram/webhook/s3cret", json={"message": {"chat": {"id": 777}, "text": text}})
        assert r.status_code == 200
    assert web.post("/telegram/webhook/s3cret", json={"edited_message": {}}).status_code == 200
    assert web.deps.store.get_user("u1").telegram_chat_id is None


def test_wrong_secret_is_404(web):
    assert web.post("/telegram/webhook/wrong", json={}).status_code == 404


def test_webhook_is_closed_when_no_secret_is_configured(web):
    web.deps.settings.telegram_webhook_secret = ""
    assert web.post("/telegram/webhook/", json={}).status_code == 404
    assert web.post("/telegram/webhook/s3cret", json={}).status_code == 404


# --- sending ---------------------------------------------------------------------------------


def test_send_uses_user_language_and_urgency():
    posts = []
    store = MemoryStore()
    store.put_user(User(uid="u1", email="a@b.c", lang="sr", telegram_chat_id="777"))
    TelegramNotifier(store, "T", lambda url, body: posts.append(body)).send(
        "u1", "needs_help_194", {"name": "baba Milena", "neighbor": "Dragan"}, True
    )
    assert posts[0]["chat_id"] == "777" and "194" in posts[0]["text"] and posts[0]["disable_notification"] is False


def test_ordinary_notice_arrives_silently_and_in_english_for_an_english_user():
    posts = []
    TelegramNotifier(linked_store("en"), "T", lambda url, body: posts.append((url, body))).send(
        "u1", "call_ok", {"name": "Ann"}, False
    )
    url, body = posts[0]
    assert url == "https://api.telegram.org/botT/sendMessage"
    assert body == {"chat_id": "777", "text": "Ann answered and says everything is fine.", "disable_notification": True}


def test_send_without_linked_chat_does_nothing():
    posts = []
    store = MemoryStore()
    store.put_user(User(uid="u1", email="a@b.c"))
    TelegramNotifier(store, "T", lambda u, b: posts.append(b)).send("u1", "call_ok", {"name": "N"}, False)
    TelegramNotifier(store, "T", lambda u, b: posts.append(b)).send("ghost", "call_ok", {"name": "N"}, False)
    TelegramNotifier(store, "T", lambda u, b: posts.append(b)).send("", "call_ok", {"name": "N"}, False)
    assert posts == []


def test_a_failed_message_never_breaks_the_caller_and_never_logs_the_token(caplog):
    def down(url, body):
        raise RuntimeError(f"cannot reach {url}")

    TelegramNotifier(linked_store(), "SECRET-TOKEN", down, sleep=lambda s: None).send("u1", "call_ok", {"name": "N"}, False)
    assert "SECRET-TOKEN" not in caplog.text and "not delivered" in caplog.text


# --- telling Telegram where to deliver -------------------------------------------------------


def test_webhook_registration_points_telegram_at_our_secret_address():
    from kuckuc.engine.notify import register_telegram_webhook

    posts = []
    done = register_telegram_webhook("TOKEN", "https://web.example", "s3cret", lambda url, body: posts.append((url, body)))
    assert done is True
    assert posts == [
        (
            "https://api.telegram.org/botTOKEN/setWebhook",
            {"url": "https://web.example/telegram/webhook/s3cret", "allowed_updates": ["message"]},
        )
    ]


def test_webhook_registration_is_skipped_without_a_token_or_a_secret_and_never_raises(caplog):
    from kuckuc.engine.notify import register_telegram_webhook

    posts = []
    assert register_telegram_webhook("", "https://web.example", "s3cret", lambda u, b: posts.append(u)) is False
    assert register_telegram_webhook("TOKEN", "https://web.example", "", lambda u, b: posts.append(u)) is False
    assert posts == []

    def down(url, body):
        raise RuntimeError(f"cannot reach {url}")

    assert register_telegram_webhook("SECRET-TOKEN", "https://web.example", "s3cret", down) is False
    assert "SECRET-TOKEN" not in caplog.text and "s3cret" not in caplog.text


def test_deploy_job_registers_the_webhook_from_settings_and_fails_loudly_when_telegram_refuses():
    from kuckuc.config import Settings
    from kuckuc.jobs.telegram_webhook import run

    settings = Settings(telegram_bot_token="TOKEN", telegram_webhook_secret="s3cret", web_public_url="https://web.example")
    posts = []
    assert run(settings, lambda url, body: posts.append(body["url"])) == 0
    assert posts == ["https://web.example/telegram/webhook/s3cret"]

    def refused(url, body):
        raise RuntimeError("401")

    assert run(settings, refused) == 1


def test_deploy_job_does_nothing_and_succeeds_when_telegram_is_not_set_up():
    from kuckuc.config import Settings
    from kuckuc.jobs.telegram_webhook import run

    posts = []
    assert run(Settings(telegram_bot_token="", telegram_webhook_secret=""), lambda u, b: posts.append(u)) == 0
    assert posts == []


def test_the_real_sender_keeps_the_bots_token_out_of_the_log(caplog):
    import logging

    import httpx

    from kuckuc.engine.notify import telegram_post

    seen = []

    def telegram(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    caplog.set_level(logging.INFO)
    post = telegram_post(httpx.Client(transport=httpx.MockTransport(telegram)))
    post("https://api.telegram.org/botSECRET-TOKEN/sendMessage", {"chat_id": "1", "text": "x"})
    assert seen == ["https://api.telegram.org/botSECRET-TOKEN/sendMessage"]
    assert "SECRET-TOKEN" not in caplog.text


def test_the_real_sender_raises_when_telegram_refuses_so_the_caller_can_tell():
    import httpx

    from kuckuc.engine.notify import telegram_post

    post = telegram_post(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401))))
    with pytest.raises(httpx.HTTPStatusError):
        post("https://api.telegram.org/botT/sendMessage", {})


# --- whether a notice arrived ----------------------------------------------------------------


def test_send_says_whether_the_message_was_delivered():
    posts = []
    assert TelegramNotifier(linked_store(), "T", lambda u, b: posts.append(b)).send("u1", "call_ok", {"name": "N"}, False) is True

    store = MemoryStore()
    store.put_user(User(uid="u1", email="a@b.c"))
    assert TelegramNotifier(store, "T", lambda u, b: posts.append(b)).send("u1", "call_ok", {"name": "N"}, False) is False

    def down(url, body):
        raise RuntimeError("blocked by the user")

    assert TelegramNotifier(linked_store(), "T", down, sleep=lambda s: None).send("u1", "call_ok", {"name": "N"}, False) is False


def test_without_a_bot_a_notice_is_an_error_in_the_log_and_counts_as_not_delivered(caplog):
    import logging

    from kuckuc.deps import LogNotifier

    caplog.set_level(logging.INFO)
    assert LogNotifier().send("u1", "needs_help_194", {"name": "N"}, True) is False
    assert any(r.levelno >= logging.ERROR and "needs_help_194" in r.getMessage() for r in caplog.records)


def test_a_message_that_fails_once_is_sent_again():
    posts, waits = [], []

    def hiccup(url, body):
        posts.append(body)
        if len(posts) == 1:
            raise RuntimeError("Telegram did not answer in time")

    notifier = TelegramNotifier(linked_store(), "T", hiccup, sleep=waits.append)
    assert notifier.send("u1", "needs_help_194", {"name": "N", "neighbor": "K"}, True) is True
    assert len(posts) == 2 and waits == [1]


def test_a_message_that_keeps_failing_is_given_up_after_three_tries():
    posts = []

    def down(url, body):
        posts.append(body)
        raise RuntimeError("down")

    notifier = TelegramNotifier(linked_store(), "T", down, sleep=lambda s: None)
    assert notifier.send("u1", "needs_help_194", {"name": "N", "neighbor": "K"}, True) is False
    assert len(posts) == 3


def test_a_link_works_once_so_nobody_can_take_over_the_messages_later(web):
    # A forwarded "Connect Telegram" link used to move every message, urgent ones too, to whoever opened it.
    web.deps.store.put_user(User(uid="u1", email="a@b.c", telegram_link_code="abc123"))
    start = {"message": {"chat": {"id": 777}, "text": "/start abc123"}}
    web.post("/telegram/webhook/s3cret", json=start)
    later = {"message": {"chat": {"id": 999}, "text": "/start abc123"}}
    web.post("/telegram/webhook/s3cret", json=later)
    user = web.deps.store.get_user("u1")
    assert user.telegram_chat_id == "777" and user.telegram_link_code not in ("", "abc123")
