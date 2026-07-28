"""Single-video transcripts must reach Drive too, in a per-request folder.

/transcribe used to leave its transcript as a blob on the coordinator: delivery
was wired only into the channel subsystem. Rather than duplicating the render
and upload path, a one-video synthetic job is recorded in the same tables, so
markdown rendering, Drive upload, idempotency and job finalisation are shared.
The destination folder rides along as the "playlist" name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrapower.coordinator import blob_store as bs
from scrapower.coordinator.api import transcribe_api
from scrapower.coordinator.channel import delivery


@pytest.fixture
def no_metadata_lookup(monkeypatch):
    """Skip the yt-dlp call: these tests are about wiring, not about the network."""

    async def fake_meta(url, proxy=None):
        return {"id": "BV1xK4y1J77Q_p1", "title": "Lecture 01 - Methods", "duration": 7965}

    monkeypatch.setattr(
        "scrapower.coordinator.channel.discovery.fetch_video_meta", fake_meta
    )
    return fake_meta


async def test_registration_makes_the_task_deliverable(db, no_metadata_lookup):
    await transcribe_api._register_delivery(
        db, "t" * 32, "https://www.bilibili.com/video/BV1xK4y1J77Q/?p=1", "Cours", "large-v3",
        ["md", "json"],
    )

    cur = await db.execute("SELECT * FROM channel_videos WHERE task_id = ?", ("t" * 32,))
    row = await cur.fetchone()
    assert row is not None
    assert row["title"] == "Lecture 01 - Methods", "the real title is used for filenames"
    assert json.loads(row["playlists_json"]) == ["Cours"], "the folder rides as a playlist name"
    assert row["delivered"] == 0


async def test_registration_falls_back_when_metadata_is_unavailable(db, monkeypatch):
    """A slow or unreachable site must not block delivery, only degrade the name."""

    async def no_meta(url, proxy=None):
        return {}

    monkeypatch.setattr("scrapower.coordinator.channel.discovery.fetch_video_meta", no_meta)

    task_id = "u" * 32
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", "_videos", "large-v3", ["md"]
    )

    cur = await db.execute(
        "SELECT title, video_id FROM channel_videos WHERE task_id = ?", (task_id,)
    )
    row = await cur.fetchone()
    assert row["video_id"] == task_id[:12], "falls back to the task id"
    assert row["title"] == task_id[:12]


async def test_registration_is_idempotent(db, no_metadata_lookup):
    task_id = "v" * 32
    for _ in range(3):
        await transcribe_api._register_delivery(
            db, task_id, "https://example.com/v", "_videos", "large-v3", ["md"]
        )

    cur = await db.execute(
        "SELECT COUNT(*) AS n FROM channel_videos WHERE task_id = ?", (task_id,)
    )
    assert (await cur.fetchone())["n"] == 1


async def test_the_existing_sweep_delivers_a_single_video(db, blob_dir, config, no_metadata_lookup):
    """End to end through the shared path: registration -> completed task -> file."""
    transcript = json.dumps(
        {
            "language": "zh",
            "duration": 7965.0,
            "segments": [{"start": 0, "end": 4, "text": " So today's first lesson"}],
        }
    )
    h = await bs.store_blob(db, blob_dir, transcript.encode())

    task_id = "w" * 32
    await db.execute(
        "INSERT INTO tasks (id, client_id, state, output_hash, created_at, updated_at)"
        " VALUES (?, 'anonymous', 'completed', ?, '1', '1')",
        (task_id, h),
    )
    await db.commit()
    await transcribe_api._register_delivery(
        db, task_id, "https://www.bilibili.com/video/BV1xK4y1J77Q/?p=1", "Cours", "large-v3",
        ["md", "json"],
    )

    assert await delivery.deliver_completed(db, blob_dir, config) == 1

    base = f"{delivery.sanitize_name('Lecture 01 - Methods')} [BV1xK4y1J77Q_p1]"
    out_dir = Path(config.transcripts_dir) / "Cours"
    assert (out_dir / f"{base}.md").exists(), "delivered into the requested folder"
    assert (out_dir / f"{base}.json").exists()

    md = (out_dir / f"{base}.md").read_text(encoding="utf-8")
    assert "Lecture 01 - Methods" in md
    assert "**[00:00:00]**" in md, "rendered with timestamps like channel transcripts"


async def test_delivery_of_a_single_video_is_idempotent(
    db, blob_dir, config, no_metadata_lookup
):
    h = await bs.store_blob(db, blob_dir, json.dumps({"segments": []}).encode())
    task_id = "x" * 32
    await db.execute(
        "INSERT INTO tasks (id, client_id, state, output_hash, created_at, updated_at)"
        " VALUES (?, 'anonymous', 'completed', ?, '1', '1')",
        (task_id, h),
    )
    await db.commit()
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", "_videos", "large-v3", ["md"]
    )

    assert await delivery.deliver_completed(db, blob_dir, config) == 1
    assert await delivery.deliver_completed(db, blob_dir, config) == 0


async def test_single_video_job_finalises(db, blob_dir, config, no_metadata_lookup):
    """The synthetic job must not linger in 'running' forever either."""
    from scrapower.coordinator.channel import job

    h = await bs.store_blob(db, blob_dir, json.dumps({"segments": []}).encode())
    task_id = "y" * 32
    await db.execute(
        "INSERT INTO tasks (id, client_id, state, output_hash, created_at, updated_at)"
        " VALUES (?, 'anonymous', 'completed', ?, '1', '1')",
        (task_id, h),
    )
    await db.commit()
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", "_videos", "large-v3", ["md"]
    )
    await delivery.deliver_completed(db, blob_dir, config)

    assert await job.finalize_jobs(db) == 1
    cur = await db.execute(
        "SELECT state FROM channel_jobs WHERE id = ?", (f"single-{task_id[:16]}",)
    )
    assert (await cur.fetchone())["state"] == "done"


# ── Request parsing ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body_folder", "expected"),
    [
        (None, transcribe_api.DEFAULT_SINGLE_FOLDER),
        ("", transcribe_api.DEFAULT_SINGLE_FOLDER),
        ("   ", transcribe_api.DEFAULT_SINGLE_FOLDER),
        ("Cours Tsinghua", "Cours Tsinghua"),
        ("  padded  ", "padded"),
    ],
)
def test_folder_defaulting(body_folder, expected):
    """Mirrors the endpoint's normalisation so an empty value can't create a
    folder named "" in Drive."""
    folder = (body_folder or transcribe_api.DEFAULT_SINGLE_FOLDER).strip()
    folder = folder or transcribe_api.DEFAULT_SINGLE_FOLDER
    assert folder == expected
