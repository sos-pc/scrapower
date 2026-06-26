"""Test fixtures for scrapower coordinator.

HTTP tests use httpx.ASGITransport (direct ASGI, no network).
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _test_env(data_dir: str):
    old = {}
    for k, v in {
        "SCRAPOWER_DATA_DIR": data_dir,
        "SCRAPOWER_DB_PATH": f"{data_dir}/scrapower.db",
        "SCRAPOWER_LOG_LEVEL": "ERROR",
        "SCRAPOWER_HEARTBEAT_INTERVAL_SEC": "1",
        "SCRAPOWER_HEARTBEAT_MISS_THRESHOLD": "2",
        "SCRAPOWER_TASK_ACCEPT_TIMEOUT_SEC": "2",
        "SCRAPOWER_MAX_ANONYMOUS_WORKERS": "50",
        "SCRAPOWER_API_KEY": "test-api-key",
    }.items():
        old[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _import_app(data_dir: str):
    """Import (or reload) the FastAPI app with test env vars set."""
    import importlib

    import scrapower.coordinator.main as main_module

    importlib.reload(main_module)
    return main_module.app


# ──────────────────────────────────────────────────────────────
# Data directory (session-scoped, shared)
# ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> str:
    d = tmp_path_factory.mktemp("scrapower") / "data"
    d.mkdir()
    (d / "blobs").mkdir()
    return str(d)


# ──────────────────────────────────────────────────────────────
# ASGI app (for HTTP tests via httpx)
# ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def app(data_dir):
    with _test_env(data_dir):
        return _import_app(data_dir)


@pytest.fixture
async def http_client(app) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ──────────────────────────────────────────────────────────────
# DB (for direct verification in tests)
# ──────────────────────────────────────────────────────────────


@pytest.fixture
async def coordinator_db(data_dir):
    from scrapower.coordinator.db import init_db

    conn = await init_db(f"{data_dir}/scrapower.db")
    yield conn
    await conn.close()
