"""Shared fixtures.

The API key must be in the environment *before* scrapower.coordinator.security
is imported (it reads SCRAPOWER_API_KEY at module level and otherwise generates
a random one), so it is set here at collection time.
"""

from __future__ import annotations

import os
import sys
import types

API_KEY = "test-api-key"
os.environ.setdefault("SCRAPOWER_API_KEY", API_KEY)
# Keep provider harvesters out of the way in app-level tests.
os.environ.setdefault("KAGGLE_ENABLED", "false")
os.environ.setdefault("MODAL_ENABLED", "false")
for _var in (
    "KAGGLE_ACCOUNTS",
    "MODAL_ACCOUNTS",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "HF_TOKEN",
    "HF_SPACE_ID",
    "SCRAPOWER_VPN_PROXY",
    "SCRAPOWER_DRIVE_TOKEN",
    "SCRAPOWER_YT_COOKIES_HASH",
):
    os.environ.pop(_var, None)

import pytest  # noqa: E402

from scrapower.coordinator.db import init_db  # noqa: E402
from scrapower.coordinator.domain import TaskService  # noqa: E402
from scrapower.coordinator.task_manager import TaskManager  # noqa: E402

IS_WINDOWS = sys.platform.startswith("win")


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.fixture
def blob_dir(tmp_path):
    d = tmp_path / "blobs"
    d.mkdir()
    return str(d)


@pytest.fixture
def config(tmp_path, blob_dir):
    """Minimal duck-typed Config: only the attributes the code under test reads."""
    return types.SimpleNamespace(
        blob_dir=blob_dir,
        data_dir=str(tmp_path),
        coordinator_url="http://localhost:8777",
        max_blob_size_mb=1,
        transcripts_dir=str(tmp_path / "transcripts"),
        drive_token_path="",  # Drive delivery disabled
        drive_root_folder_id="",
        delivery_interval_sec=30,
    )


@pytest.fixture
async def db(tmp_path):
    conn = await init_db(str(tmp_path / "test.db"))
    yield conn
    await conn.close()


@pytest.fixture
def task_manager(db):
    return TaskManager(db)


@pytest.fixture
def task_service(task_manager, db, config):
    return TaskService(task_manager, db, config)
