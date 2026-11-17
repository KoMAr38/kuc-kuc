"""Cloud Run entrypoint: ``uvicorn kuckuc.voice.main:app``."""

import logging

from kuckuc.cloud import google_id_token_decoder, verify_cloud_tasks_oidc
from kuckuc.config import Settings
from kuckuc.deps import build_cloud_deps
from kuckuc.voice.app import create_voice_app

logging.basicConfig(level=logging.INFO)

settings = Settings()
_decode = google_id_token_decoder()

app = create_voice_app(
    build_cloud_deps(settings, "voice"),
    verify_oidc=lambda request: verify_cloud_tasks_oidc(
        request, settings.voice_public_url, settings.invoker_email, _decode
    ),
)
