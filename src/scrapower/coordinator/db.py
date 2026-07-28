"""Database layer — SQLite via aiosqlite."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs (
    hash       TEXT PRIMARY KEY,
    size       INTEGER NOT NULL,
    ref_count  INTEGER NOT NULL DEFAULT 0,
    is_checkpoint BOOLEAN NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id             TEXT PRIMARY KEY,
    client_id      TEXT NOT NULL,
    state          TEXT NOT NULL DEFAULT 'pending',
    definition_json TEXT NOT NULL DEFAULT '{}',
    retries        INTEGER NOT NULL DEFAULT 0,
    current_assignment_token TEXT,
    assigned_worker_id TEXT,
    assigned_at    REAL,
    output_hash    TEXT,
    executable_hash TEXT DEFAULT '',
    input_hash     TEXT DEFAULT '',
    runtime        TEXT DEFAULT 'python',
    gpu_required   INTEGER NOT NULL DEFAULT 0,
    error          TEXT DEFAULT '',
        task_type      TEXT DEFAULT 'whisper',
    requirements_json TEXT DEFAULT '{}',
created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_client ON tasks(client_id);
CREATE INDEX IF NOT EXISTS idx_blobs_created ON blobs(created_at);

-- Channel transcription subsystem (isolated; see coordinator/channel/)
CREATE TABLE IF NOT EXISTS channel_jobs (
    id          TEXT PRIMARY KEY,
    channel_url TEXT NOT NULL,
    model       TEXT NOT NULL DEFAULT 'turbo',
    config_json TEXT NOT NULL DEFAULT '{}',
    state       TEXT NOT NULL DEFAULT 'discovering',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_videos (
    job_id         TEXT NOT NULL,
    video_id       TEXT NOT NULL,
    url            TEXT NOT NULL,
    title          TEXT,
    duration       INTEGER,
    playlists_json TEXT NOT NULL DEFAULT '[]',
    task_id        TEXT,
    delivered      INTEGER NOT NULL DEFAULT 0,
    delivered_at   TEXT,
    PRIMARY KEY (job_id, video_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_videos_task ON channel_videos(task_id);

-- Bootstrap tokens: short-lived, opaque credentials embedded in a launched
-- worker (e.g. a Kaggle notebook, whose source Kaggle stores and versions).
-- The worker exchanges one for the real API key + proxy at startup, so no
-- long-lived secret is ever written into a provider-stored artifact.
CREATE TABLE IF NOT EXISTS bootstrap_tokens (
    token        TEXT PRIMARY KEY,
    provider     TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    expires_at   REAL NOT NULL,
    used_count   INTEGER NOT NULL DEFAULT 0,
    last_used_at REAL,
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bootstrap_expires ON bootstrap_tokens(expires_at);
"""


async def init_db(db_path: str | Path) -> aiosqlite.Connection:
    """Initialize database, run migrations, return connection."""
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(SCHEMA)
    await _migrate(db)
    await db.commit()
    return db


async def _migrate(db: aiosqlite.Connection) -> None:
    """Apply incremental schema migrations (safe to run repeatedly)."""
    migrations = [
        # Drop legacy tables (reputation, GitHub OAuth, challenges, unused schema)
        "DROP TABLE IF EXISTS results",
        "DROP TABLE IF EXISTS workers",
        "DROP TABLE IF EXISTS events",
        "DROP TABLE IF EXISTS challenges",
        "DROP TABLE IF EXISTS oauth_states",
        "DROP TABLE IF EXISTS provider_tokens",
        "DROP INDEX IF EXISTS idx_results_task",
        "DROP INDEX IF EXISTS idx_events_task",
        # Add deadline_ms for long-running tasks (Mode B)
        "ALTER TABLE tasks ADD COLUMN deadline_ms INTEGER NOT NULL DEFAULT 60000",
        # Add max_retries column (used by task lifecycle)
        "ALTER TABLE tasks ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 3",
        # Add error column for task failure diagnostics
        "ALTER TABLE tasks ADD COLUMN error TEXT DEFAULT ''",
        # Add task_type and requirements_json for matching
        "ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'wasm'",
        "ALTER TABLE tasks ADD COLUMN requirements_json TEXT DEFAULT '{}'",
    ]
    for sql in migrations:
        try:
            await db.execute(sql)
        except Exception:
            pass  # Column already exists — safe to ignore
