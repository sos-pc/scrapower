"""Content-addressed blob store.

All blobs are identified by their SHA256 hash. Immutable by design.
Storage layout: data/blobs/XX/XXXXXX... (2-char prefix for filesystem friendliness)
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import aiosqlite


def _blob_path(blob_dir: str, hash_hex: str) -> Path:
    """Convert hash to filesystem path: data/blobs/ab/abcdef...

    Validates that hash_hex is exactly 64 hex characters to prevent
    path traversal attacks via ../ or %2e%2e%2f encoding.
    """
    if not _is_valid_hash(hash_hex):
        raise ValueError(f"Invalid blob hash: {hash_hex[:20]}...")
    return Path(blob_dir) / hash_hex[:2] / hash_hex


def _is_valid_hash(hash_hex: str) -> bool:
    """Check if string is a valid 64-char hex SHA-256 hash."""
    return len(hash_hex) == 64 and all(c in "0123456789abcdef" for c in hash_hex)


def compute_hash(data: bytes) -> str:
    """Compute SHA256 hex digest of data."""
    return hashlib.sha256(data).hexdigest()


async def store_blob(
    db: aiosqlite.Connection,
    blob_dir: str,
    data: bytes,
    is_checkpoint: bool = False,
) -> str:
    """Store a blob (content-addressed, idempotent); return its SHA256 hash.

    References are owned by *tasks* (ref_count is bumped in
    TaskManager.create/complete and TaskService.set_queued) and by explicit
    pins (is_checkpoint). Uploading does NOT take a reference: a new blob is
    registered at ref_count=0 and re-uploading an existing blob is a no-op.
    This keeps ``ref_count == (task references + pin)`` so it reliably reaches
    0 once nothing points at the blob, letting run_gc() reclaim it. (Storing
    at ref_count=1 was the historical leak: nothing ever released that ref.)
    """
    hash_hex = compute_hash(data)
    file_path = _blob_path(blob_dir, hash_hex)

    cursor = await db.execute("SELECT hash FROM blobs WHERE hash = ?", (hash_hex,))
    existing = await cursor.fetchone()
    if existing:
        return hash_hex  # already stored — idempotent, no ref bump

    # Write to disk atomically (temp + rename)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_bytes(data)
    os.replace(tmp_path, file_path)

    # Register at ref_count=0 — unreferenced until a task (or pin) adopts it.
    await db.execute(
        "INSERT INTO blobs (hash, size, ref_count, is_checkpoint) VALUES (?, ?, 0, ?)",
        (hash_hex, len(data), 1 if is_checkpoint else 0),
    )
    await db.commit()
    return hash_hex


async def reconcile_ref_counts(db: aiosqlite.Connection) -> None:
    """Recompute every blob's ref_count from the references that actually exist.

    ``ref_count = (# tasks referencing it via executable/input/output_hash)
                 + (1 if pinned via is_checkpoint else 0)``

    Idempotent and safe to run at every startup. It repairs drift from the
    historical double-counting bug (blobs stuck at ref_count>=1 forever, so
    run_gc never reclaimed them) — after reconciliation, orphaned blobs drop
    to 0 and become eligible for GC once past their TTL.
    """
    await db.execute(
        """
        UPDATE blobs SET ref_count =
            (SELECT COUNT(*) FROM tasks WHERE tasks.executable_hash = blobs.hash)
          + (SELECT COUNT(*) FROM tasks WHERE tasks.input_hash = blobs.hash)
          + (SELECT COUNT(*) FROM tasks WHERE tasks.output_hash = blobs.hash)
          + (CASE WHEN is_checkpoint = 1 THEN 1 ELSE 0 END)
        """
    )
    await db.commit()


async def get_blob(
    db: aiosqlite.Connection,
    blob_dir: str,
    hash_hex: str,
) -> bytes | None:
    """Retrieve a blob by hash. Returns None if not found."""
    file_path = _blob_path(blob_dir, hash_hex)
    if not file_path.exists():
        return None
    return file_path.read_bytes()


async def blob_exists(
    db: aiosqlite.Connection,
    blob_dir: str,
    hash_hex: str,
) -> bool:
    """Check if a blob exists."""
    return _blob_path(blob_dir, hash_hex).exists()


async def run_gc(
    db: aiosqlite.Connection,
    blob_dir: str,
    ttl_days: int = 7,
    checkpoint_ttl_days: int = 30,
) -> int:
    """Garbage-collect blobs with ref_count=0 older than their TTL.

    Only deletes blobs that are NOT referenced by any task (ref_count=0)
    AND have passed their age threshold.
    """
    deleted = 0

    # Checkpoint blobs (ref_count=0, older than checkpoint_ttl_days)
    cursor = await db.execute(
        """SELECT hash FROM blobs
           WHERE is_checkpoint = 1 AND ref_count = 0
             AND datetime(created_at, '+' || ? || ' days') < datetime('now')""",
        (checkpoint_ttl_days,),
    )
    async for row in cursor:
        file_path = _blob_path(blob_dir, row["hash"])
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass
        deleted += 1
    await db.execute(
        """DELETE FROM blobs
           WHERE is_checkpoint = 1 AND ref_count = 0
             AND datetime(created_at, '+' || ? || ' days') < datetime('now')""",
        (checkpoint_ttl_days,),
    )

    # Regular blobs (ref_count=0, older than ttl_days)
    cursor = await db.execute(
        """SELECT hash FROM blobs
           WHERE is_checkpoint = 0 AND ref_count = 0
             AND datetime(created_at, '+' || ? || ' days') < datetime('now')""",
        (ttl_days,),
    )
    async for row in cursor:
        file_path = _blob_path(blob_dir, row["hash"])
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass
        deleted += 1
    await db.execute(
        """DELETE FROM blobs
           WHERE is_checkpoint = 0 AND ref_count = 0
             AND datetime(created_at, '+' || ? || ' days') < datetime('now')""",
        (ttl_days,),
    )

    await db.commit()
    return deleted
