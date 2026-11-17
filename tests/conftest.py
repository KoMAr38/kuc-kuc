import os
import uuid

import pytest

from kuckuc.config import Settings
from kuckuc.store import MemoryStore

# Set KUCKUC_TEST_FIRESTORE_PROJECT to also run every store test against a real Firestore
# database, in throwaway collections that are deleted afterwards. With the local emulator running
# (FIRESTORE_EMULATOR_HOST set) they run against it instead, with no project and no network.
FIRESTORE_PROJECT = os.environ.get("KUCKUC_TEST_FIRESTORE_PROJECT", "") or (
    "demo-kuckuc" if os.environ.get("FIRESTORE_EMULATOR_HOST") else ""
)


@pytest.fixture
def firestore_store():
    if not FIRESTORE_PROJECT:
        pytest.skip("set KUCKUC_TEST_FIRESTORE_PROJECT (or FIRESTORE_EMULATOR_HOST) to run against Firestore")
    from google.cloud import firestore

    from kuckuc.store_firestore import COLLECTIONS, FirestoreStore

    client = firestore.Client(project=FIRESTORE_PROJECT)
    prefix = f"test_{uuid.uuid4().hex[:8]}_"
    yield FirestoreStore(client, prefix=prefix)
    for name in COLLECTIONS:
        client.recursive_delete(client.collection(prefix + name))


@pytest.fixture(params=["memory", "firestore"] if FIRESTORE_PROJECT else ["memory"])
def store(request):
    if request.param == "memory":
        return MemoryStore()
    return request.getfixturevalue("firestore_store")


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def sys():
    """The whole system in memory: one person (p1) and two neighbours (n1, n2)."""
    from helpers import build_sys

    return build_sys()
