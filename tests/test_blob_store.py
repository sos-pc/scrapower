"""Blob ref_count accounting, pinning, reconciliation and GC.

Regression cover for the leak where blobs were stored at ref_count=1 with
nothing ever releasing that reference, so run_gc() never reclaimed anything
(prod had grown to 746MB of blobs, 0 of 231 collectable).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scrapower.coordinator import blob_store as bs
from scrapower.coordinator.task_manager import TaskState


async def _ref(db, h: str) -> tuple[int, int] | None:
    cur = await db.execute("SELECT ref_count, is_checkpoint FROM blobs WHERE hash = ?", (h,))
    row = await cur.fetchone()
    return None if row is None else (row["ref_count"], row["is_checkpoint"])


def _path(blob_dir: str, h: str) -> Path:
    return Path(blob_dir) / h[:2] / h


async def test_store_registers_unreferenced(db, blob_dir):
    """An upload must not take a reference: tasks own references, not uploads."""
    h = await bs.store_blob(db, blob_dir, b"payload")
    assert await _ref(db, h) == (0, 0)
    assert _path(blob_dir, h).exists()


async def test_store_is_idempotent(db, blob_dir):
    """Re-uploading identical content is a no-op, not a ref bump (content-addressed)."""
    h1 = await bs.store_blob(db, blob_dir, b"same bytes")
    h2 = await bs.store_blob(db, blob_dir, b"same bytes")
    assert h1 == h2
    assert await _ref(db, h1) == (0, 0)


async def test_task_owns_references_across_lifecycle(db, blob_dir, task_manager, task_service):
    exe = await bs.store_blob(db, blob_dir, b"#!/usr/bin/env python")
    inp = await bs.store_blob(db, blob_dir, b'{"url": "x"}')

    task_id = "a" * 32
    await task_service.submit(
        task_id=task_id,
        client_id="c",
        runtime="python",
        executable_hash=exe,
        input_hash="",
        initial_state=TaskState.PENDING,
    )
    assert await _ref(db, exe) == (1, 0), "creating a task takes a reference"

    await task_manager.transition(task_id, TaskState.DOWNLOADING)
    await task_service.set_queued(task_id, inp)
    assert await _ref(db, inp) == (1, 0), "queueing takes a reference on the input"

    ok, token = await task_service.assign(task_id, "worker-1")
    assert ok
    out = await bs.store_blob(db, blob_dir, b"transcript")
    assert await task_service.complete(task_id, out, token)
    assert await _ref(db, out) == (1, 0), "completing takes a reference on the output"

    # Age the task out and clean up: every reference must be released.
    await db.execute("UPDATE tasks SET updated_at = '1000000000.0' WHERE id = ?", (task_id,))
    await db.commit()
    assert await task_service.cleanup_expired(completed_ttl_sec=0) == 1
    assert await _ref(db, exe) == (0, 0)
    assert await _ref(db, inp) == (0, 0)
    assert await _ref(db, out) == (0, 0)


async def test_reconcile_releases_legacy_leaked_blob(db, blob_dir):
    """The historical leak: ref_count stuck at 1 with no task referencing it."""
    leaked = await bs.store_blob(db, blob_dir, b"orphan from the old accounting")
    await db.execute("UPDATE blobs SET ref_count = 1 WHERE hash = ?", (leaked,))
    await db.commit()

    await bs.reconcile_ref_counts(db)
    assert await _ref(db, leaked) == (0, 0), "reconcile must drop it to collectable"


async def test_reconcile_preserves_pins(db, blob_dir):
    """Pinned blobs (whisper runner, active cookies) keep a standing reference."""
    pinned = await bs.store_blob(db, blob_dir, b"whisper runner source")
    await db.execute("UPDATE blobs SET is_checkpoint = 1 WHERE hash = ?", (pinned,))
    await db.commit()

    await bs.reconcile_ref_counts(db)
    assert await _ref(db, pinned) == (1, 1)


async def test_gc_collects_orphans_but_never_pins(db, blob_dir):
    orphan = await bs.store_blob(db, blob_dir, b"nobody references me")
    pinned = await bs.store_blob(db, blob_dir, b"pinned executable")
    await db.execute("UPDATE blobs SET is_checkpoint = 1 WHERE hash = ?", (pinned,))
    await bs.reconcile_ref_counts(db)
    # Age every blob past its TTL (prod blobs are days old; SQLite stores seconds).
    await db.execute("UPDATE blobs SET created_at = '2020-01-01 00:00:00'")
    await db.commit()

    deleted = await bs.run_gc(db, blob_dir, ttl_days=0, checkpoint_ttl_days=0)

    assert deleted == 1, "only the orphan is collectable"
    assert not _path(blob_dir, orphan).exists()
    assert _path(blob_dir, pinned).exists(), "a pin must survive GC even when aged"
    assert await _ref(db, pinned) == (1, 1)


async def test_small_blobs_round_trip_inline(db, blob_dir, monkeypatch):
    """Below the threshold nothing is dispatched to a thread: at the real blob
    sizes (0.2KB median, 198KB max in production) that would only add overhead."""
    calls = []
    real = asyncio.to_thread

    async def spy(fn, *a, **kw):
        calls.append(getattr(fn, "__name__", str(fn)))
        return await real(fn, *a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", spy)

    payload = b"x" * 200_000  # ~200KB, the observed maximum
    h = await bs.store_blob(db, blob_dir, payload)
    assert await bs.get_blob(db, blob_dir, h) == payload
    assert calls == [], f"small I/O must stay inline, dispatched: {calls}"


async def test_large_blobs_go_off_the_event_loop(db, blob_dir, monkeypatch):
    """max_blob_size_mb still allows 50MB, where a sync read/write stalls every
    other pull/heartbeat/submit for tens of milliseconds."""
    calls = []
    real = asyncio.to_thread

    async def spy(fn, *a, **kw):
        calls.append(getattr(fn, "__name__", str(fn)))
        return await real(fn, *a, **kw)

    monkeypatch.setattr(asyncio, "to_thread", spy)

    payload = b"y" * (bs.OFFLOAD_THRESHOLD_BYTES + 1)
    h = await bs.store_blob(db, blob_dir, payload)
    assert len(calls) == 1, "the write must be offloaded"

    assert await bs.get_blob(db, blob_dir, h) == payload
    assert len(calls) == 2, "the read must be offloaded too"


async def test_missing_blob_returns_none_without_reading(db, blob_dir):
    assert await bs.get_blob(db, blob_dir, "d" * 64) is None


async def test_invalid_hash_is_rejected(db, blob_dir):
    """Path-traversal guard on the content-addressed layout."""
    for bad in ("../../etc/passwd", "zz" * 32, "abc", "a" * 63, "A" * 64):
        with pytest.raises(ValueError):
            bs._blob_path(blob_dir, bad)
        # The public read path must refuse it too, not just the internal helper.
        with pytest.raises(ValueError):
            await bs.get_blob(db, blob_dir, bad)
