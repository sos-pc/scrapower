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
    """Store a blob, return its SHA256 hash. If already exists, bump ref_count."""
    hash_hex = compute_hash(data)
    file_path = _blob_path(blob_dir, hash_hex)

    # Check if already in DB
    cursor = await db.execute("SELECT hash, ref_count FROM blobs WHERE hash = ?", (hash_hex,))
    existing = await cursor.fetchone()

    if existing:
        await db.execute(
            "UPDATE blobs SET ref_count = ref_count + 1 WHERE hash = ?",
            (hash_hex,),
        )
        await db.commit()
        return hash_hex

    # Write to disk
    file_path.parent.mkdir(parents=True, exist_ok=True)
    # Use atomic write: write to temp, then rename
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_bytes(data)
    os.replace(tmp_path, file_path)

    # Register in DB
    await db.execute(
        "INSERT INTO blobs (hash, size, is_checkpoint) VALUES (?, ?, ?)",
        (hash_hex, len(data), 1 if is_checkpoint else 0),
    )
    await db.commit()
    return hash_hex


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


async def delete_blob(
    db: aiosqlite.Connection,
    blob_dir: str,
    hash_hex: str,
) -> bool:
    """Decrement ref_count. If 0, delete from disk and DB. Returns True if fully deleted."""
    cursor = await db.execute("SELECT ref_count FROM blobs WHERE hash = ?", (hash_hex,))
    row = await cursor.fetchone()
    if not row:
        return False

    new_count = row["ref_count"] - 1
    if new_count <= 0:
        await db.execute("DELETE FROM blobs WHERE hash = ?", (hash_hex,))
        file_path = _blob_path(blob_dir, hash_hex)
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass
        await db.commit()
        return True
    else:
        await db.execute(
            "UPDATE blobs SET ref_count = ? WHERE hash = ?",
            (new_count, hash_hex),
        )
        await db.commit()
        return False


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
