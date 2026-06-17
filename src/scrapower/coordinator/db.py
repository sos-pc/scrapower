"""Database layer — SQLite via aiosqlite."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs (
    hash       TEXT PRIMARY KEY,
    size       INTEGER NOT NULL,
    ref_count  INTEGER NOT NULL DEFAULT 1,
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
    runtime        TEXT DEFAULT 'wasm',
    gpu_required   INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS results (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    worker_id   TEXT NOT NULL,
    status      TEXT NOT NULL,
    output_hash TEXT,
    metadata_json TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS workers (
    id               TEXT PRIMARY KEY,
    identity_key     TEXT,
    auth_level       INTEGER NOT NULL DEFAULT 0,
    reputation_score REAL NOT NULL DEFAULT 0.0,
    capabilities_json TEXT,
    first_seen       TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_client ON tasks(client_id);
CREATE INDEX IF NOT EXISTS idx_results_task ON results(task_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_blobs_created ON blobs(created_at);

CREATE TABLE IF NOT EXISTS provider_tokens (
    visitor_id     TEXT NOT NULL,
    provider       TEXT NOT NULL,
    token_encrypted TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (visitor_id, provider)
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state      TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);
"""


async def init_db(db_path: str | Path) -> aiosqlite.Connection:
    """Initialize database, run migrations, return connection."""
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(SCHEMA)
    await db.commit()
    return db
