"""Tests for the blob store."""

from __future__ import annotations

import pytest

from scrapower.coordinator.blob_store import (
    blob_exists,
    compute_hash,
    delete_blob,
    get_blob,
    store_blob,
)
from scrapower.coordinator.db import init_db


@pytest.fixture
async def db(tmp_path):
    """Create a test database."""
    db_path = tmp_path / "test.db"
    conn = await init_db(db_path)
    yield conn
    await conn.close()


@pytest.fixture
def blob_dir(tmp_path):
    """Temporary blob storage directory."""
    d = tmp_path / "blobs"
    d.mkdir()
    return str(d)


@pytest.mark.asyncio
async def test_compute_hash():
    data = b"hello world"
    h = compute_hash(data)
    assert len(h) == 64
    assert h == compute_hash(b"hello world")  # deterministic
    assert h != compute_hash(b"hello world!")  # different


@pytest.mark.asyncio
async def test_store_and_get(db, blob_dir):
    data = b"test blob content"
    h = await store_blob(db, blob_dir, data)

    # Retrieve
    result = await get_blob(db, blob_dir, h)
    assert result == data

    # Exists
    assert await blob_exists(db, blob_dir, h) is True
    assert await blob_exists(db, blob_dir, "a" * 64) is False


@pytest.mark.asyncio
async def test_store_duplicate(db, blob_dir):
    data = b"duplicate test"
    h1 = await store_blob(db, blob_dir, data)
    h2 = await store_blob(db, blob_dir, data)
    assert h1 == h2  # same hash

    # Check ref_count incremented

    cursor = await db.execute("SELECT ref_count FROM blobs WHERE hash = ?", (h1,))
    row = await cursor.fetchone()
    assert row["ref_count"] == 2


@pytest.mark.asyncio
async def test_delete_blob(db, blob_dir):
    data = b"to be deleted"
    h = await store_blob(db, blob_dir, data)

    # Delete (ref_count goes 1 -> 0, file removed)
    deleted = await delete_blob(db, blob_dir, h)
    assert deleted is True

    # Gone from disk
    result = await get_blob(db, blob_dir, h)
    assert result is None

    # Gone from DB
    assert await blob_exists(db, blob_dir, h) is False


@pytest.mark.asyncio
async def test_delete_duplicate(db, blob_dir):
    data = b"multi-ref"
    h = await store_blob(db, blob_dir, data)
    h = await store_blob(db, blob_dir, data)  # ref_count = 2

    # First delete: ref_count 2 -> 1, file remains
    deleted = await delete_blob(db, blob_dir, h)
    assert deleted is False

    result = await get_blob(db, blob_dir, h)
    assert result == data  # still there

    # Second delete: ref_count 1 -> 0, file removed
    deleted = await delete_blob(db, blob_dir, h)
    assert deleted is True

    result = await get_blob(db, blob_dir, h)
    assert result is None
