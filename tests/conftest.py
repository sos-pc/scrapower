"""Test fixtures for scrapower coordinator.

HTTP tests use httpx.ASGITransport (direct ASGI, no network).
WebSocket tests use a real uvicorn server on a random port.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import sys
import threading
import time
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
        "SCRAPOWER_SCHEDULER_TICK_SEC": "1",
        "SCRAPOWER_MAX_ANONYMOUS_WORKERS": "50",
        "SCRAPOWER_API_KEY": "test-api-key",
        "SCRAPOWER_PULL_RATE_LIMIT_PER_IP": "100",
        "SCRAPOWER_EMBEDDED_WORKER": "0",
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
# Live uvicorn server (for WebSocket tests)
# ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def live_data_dir(tmp_path_factory) -> str:
    """Module-scoped data directory so each test module's live_server
    gets a clean database (avoids cross-module zombie task contamination)."""
    d = tmp_path_factory.mktemp("scrapower_live") / "data"
    d.mkdir()
    (d / "blobs").mkdir()
    return str(d)


@pytest.fixture(scope="module")
def live_server(live_data_dir) -> Generator[str, None, None]:
    """Start a real uvicorn server on a random port. Yields ws:// URL."""
    port = _free_port()

    # Keep _test_env active for the entire server lifetime,
    # otherwise env vars are restored before lifespan runs.
    env_ctx = _test_env(live_data_dir)
    env_ctx.__enter__()
    server = None
    t = None
    try:
        try:
            app = _import_app(live_data_dir)
        except Exception as e:
            pytest.fail(f"Failed to import app: {e}")

        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        ready = threading.Event()

        def _run():
            async def serve():
                ready.set()
                await server.serve()

            asyncio.run(serve())

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        ready.wait(timeout=5)
        time.sleep(0.3)

        yield f"ws://127.0.0.1:{port}"
    finally:
        if server is not None:
            server.should_exit = True
        if t is not None:
            t.join(timeout=3)
        env_ctx.__exit__(None, None, None)


# ──────────────────────────────────────────────────────────────
# DB (for direct verification in tests)
# ──────────────────────────────────────────────────────────────


@pytest.fixture
async def coordinator_db(data_dir):
    from scrapower.coordinator.db import init_db

    conn = await init_db(f"{data_dir}/scrapower.db")
    yield conn
    await conn.close()
