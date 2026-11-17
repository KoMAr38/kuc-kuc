"""Cloud Run entrypoint: ``uvicorn kuckuc.web.main:app``."""

import logging

from kuckuc.config import Settings
from kuckuc.deps import build_cloud_deps
from kuckuc.web.app import create_web_app

logging.basicConfig(level=logging.INFO)

app = create_web_app(build_cloud_deps(Settings(), "web"))
