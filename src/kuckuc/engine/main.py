"""Cloud Run entrypoint: ``uvicorn kuckuc.engine.main:app``.

The service is deployed without public access and with internal ingress, so only Pub/Sub and
Cloud Tasks, calling as our invoker account, can reach it. Cloud Run checks that before any
request gets here.
"""

import logging

from kuckuc.config import Settings
from kuckuc.deps import build_cloud_deps
from kuckuc.engine.app import create_engine_app

logging.basicConfig(level=logging.INFO)

app = create_engine_app(build_cloud_deps(Settings(), "engine"))
