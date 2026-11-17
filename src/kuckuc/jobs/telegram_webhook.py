"""Run once after a deploy: tell Telegram where to deliver what people write to the bot.

It runs in the cloud, as a job, so the bot's token never leaves Secret Manager.

    python -m kuckuc.jobs.telegram_webhook
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from kuckuc.config import Settings
from kuckuc.engine.notify import register_telegram_webhook, telegram_post

log = logging.getLogger(__name__)


def run(settings: Settings, http_post: Callable[[str, dict], object]) -> int:
    """The exit code: 0 when registered or when there is no bot to register, 1 when it failed."""
    if not settings.telegram_bot_token or not settings.telegram_webhook_secret:
        log.info("Telegram is not set up; nothing to register")
        return 0
    registered = register_telegram_webhook(
        settings.telegram_bot_token, settings.web_public_url, settings.telegram_webhook_secret, http_post
    )
    log.info("Telegram webhook %s", "registered" if registered else "NOT registered")
    return 0 if registered else 1


def main() -> int:
    import httpx

    logging.basicConfig(level=logging.INFO)
    with httpx.Client(timeout=10) as client:
        return run(Settings(), telegram_post(client))


if __name__ == "__main__":
    raise SystemExit(main())
