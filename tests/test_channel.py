"""Channel transcription: discovery/dedup, markdown rendering, delivery, job states."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrapower.coordinator import blob_store as bs
from scrapower.coordinator.channel import delivery, discovery, job

# ── Discovery: dedup across playlists + Shorts filtering ───────────────────

PLAYLISTS = [
    (
        "Saison A",
        [
            {"id": "v1", "title": "Conference 1", "duration": 3600},
            {"id": "v2", "title": "A short", "duration": 30},
        ],
    ),
    (
        "Conferences",
        [
            {"id": "v1", "title": "Conference 1", "duration": 3600},  # same video, 2nd playlist
            {"id": "v3", "title": "Shorts-url one", "url": "https://youtube.com/shorts/abc"},
        ],
    ),
]


def test_video_in_two_playlists_is_kept_once_with_both_memberships():
    manifest = discovery.build_video_manifest(PLAYLISTS, include_shorts=False)
    assert len(manifest) == 1
    assert manifest[0]["video_id"] == "v1"
    assert manifest[0]["playlists"] == ["Saison A", "Conferences"]


@pytest.mark.parametrize(
    ("video", "expected_short"),
    [
        ({"id": "a", "duration": 30}, True),
        ({"id": "b", "duration": 60}, True),
        ({"id": "c", "duration": 61}, False),
        ({"id": "d", "url": "https://youtube.com/shorts/x"}, True),
        ({"id": "e", "duration": 3600}, False),
        ({"id": "f"}, False),  # unknown duration: keep it rather than drop content
    ],
)
def test_shorts_detection(video, expected_short):
    assert discovery._is_short(video) is expected_short


def test_include_shorts_keeps_everything():
    manifest = discovery.build_video_manifest(PLAYLISTS, include_shorts=True)
    assert {v["video_id"] for v in manifest} == {"v1", "v2", "v3"}


def test_parse_flat_entries_skips_malformed_lines():
    raw = '{"id": "a"}\nnot json at all\n\n{"id": "b"}\n'
    assert [e["id"] for e in discovery.parse_flat_entries(raw)] == ["a", "b"]


# ── Markdown rendering ─────────────────────────────────────────────────────

META = {
    "video_id": "vid123",
    "url": "https://youtu.be/vid123",
    "title": "Nature ou paysage ?",
    "duration": 5280,
    "playlists": ["Saison A", "Conferences"],
}
TRANSCRIPT = json.dumps(
    {
        "language": "fr",
        "duration": 5280.0,
        "segments": [
            {"start": 0.0, "end": 5.0, "text": " Bonsoir tout le monde"},
            {"start": 65.0, "end": 70.0, "text": " deuxieme segment"},
            {"start": 3725.0, "end": 3730.0, "text": " apres une heure"},
        ],
    }
)


def test_markdown_header_carries_provenance():
    md = delivery.render_markdown(META, TRANSCRIPT, model="large-v3")
    assert md.startswith("# Nature ou paysage ?")
    assert "https://youtu.be/vid123" in md
    assert "Saison A, Conferences" in md
    assert "**Modèle** : large-v3" in md
    assert "**Langue** : fr" in md


@pytest.mark.parametrize(
    ("seconds", "stamp"),
    [(0.0, "00:00:00"), (65.0, "00:01:05"), (3725.0, "01:02:05")],
)
def test_markdown_timestamps(seconds, stamp):
    md = delivery.render_markdown(META, TRANSCRIPT, model="large-v3")
    assert f"**[{stamp}]**" in md


def test_markdown_falls_back_when_transcript_is_not_segmented_json():
    md = delivery.render_markdown(META, "plain text, not json", model="turbo")
    assert "plain text, not json" in md, "content must never be silently dropped"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a/b", "a-b"),
        ('bad:name?"*', "badname"),
        ("   ", "sans-titre"),
        ("Play ▷II Pause", "Play ▷II Pause"),
    ],
)
def test_sanitize_name(raw, expected):
    assert delivery.sanitize_name(raw) == expected


def test_sanitize_name_truncates():
    assert len(delivery.sanitize_name("x" * 500)) <= 120


# ── Delivery: one copy per playlist, idempotent ────────────────────────────


async def _seed_delivered_video(db, blob_dir, config, playlists):
    transcript = json.dumps(
        {"language": "fr", "duration": 60.0, "segments": [{"start": 0, "end": 3, "text": " Salut"}]}
    )
    h = await bs.store_blob(db, blob_dir, transcript.encode())
    await db.execute(
        "INSERT INTO channel_jobs (id,channel_url,model,config_json,state,created_at,updated_at)"
        " VALUES ('j1','chan','large-v3','{\"formats\":[\"md\",\"json\"]}','running','1','1')"
    )
    await db.execute(
        "INSERT INTO tasks (id,client_id,state,output_hash,created_at,updated_at)"
        " VALUES ('task1','channel:j1','completed',?,'1','1')",
        (h,),
    )
    await db.execute(
        "INSERT INTO channel_videos (job_id,video_id,url,title,duration,playlists_json,task_id,"
        "delivered) VALUES ('j1','vX','https://youtu.be/vX','Ma Vidéo',60,?,'task1',0)",
        (json.dumps(playlists, ensure_ascii=False),),
    )
    await db.commit()


async def test_delivery_writes_one_copy_per_playlist(db, blob_dir, config):
    await _seed_delivered_video(db, blob_dir, config, ["Saison A", "Conferences"])

    assert await delivery.deliver_completed(db, blob_dir, config) == 1

    base = f"{delivery.sanitize_name('Ma Vidéo')} [vX]"
    staging = Path(config.transcripts_dir)
    for playlist in ("Saison A", "Conferences"):
        assert (staging / playlist / f"{base}.md").exists()
        assert (staging / playlist / f"{base}.json").exists()


async def test_delivery_is_idempotent(db, blob_dir, config):
    await _seed_delivered_video(db, blob_dir, config, ["Saison A"])

    assert await delivery.deliver_completed(db, blob_dir, config) == 1
    assert await delivery.deliver_completed(db, blob_dir, config) == 0, "no re-delivery"

    cur = await db.execute("SELECT delivered FROM channel_videos WHERE video_id = 'vX'")
    assert (await cur.fetchone())["delivered"] == 1


async def test_delivery_works_without_drive_configured(db, blob_dir, config):
    """Drive is gated: no token => local staging only, no error."""
    assert config.drive_token_path == ""
    await _seed_delivered_video(db, blob_dir, config, ["Saison A"])
    assert await delivery.deliver_completed(db, blob_dir, config) == 1


# ── Job terminal states and cancellation ───────────────────────────────────


async def _seed_job(db, job_id, rows):
    """rows: [(video_id, task_state | None, delivered)]"""
    await db.execute(
        "INSERT INTO channel_jobs (id,channel_url,model,config_json,state,created_at,updated_at)"
        " VALUES (?,'chan','large-v3','{}','running','1','1')",
        (job_id,),
    )
    for i, (vid, tstate, delivered) in enumerate(rows):
        task_id = None
        if tstate:
            task_id = f"{job_id}-t{i}"
            await db.execute(
                "INSERT INTO tasks (id,client_id,state,created_at,updated_at)"
                " VALUES (?,?,?,'1','1')",
                (task_id, f"channel:{job_id}", tstate),
            )
        await db.execute(
            "INSERT INTO channel_videos"
            " (job_id,video_id,url,title,playlists_json,task_id,delivered)"
            " VALUES (?,?,'u','t','[\"P\"]',?,?)",
            (job_id, vid, task_id, delivered),
        )
    await db.commit()


async def _job_state(db, job_id):
    cur = await db.execute("SELECT state FROM channel_jobs WHERE id = ?", (job_id,))
    return (await cur.fetchone())["state"]


async def test_job_becomes_done_when_all_delivered(db):
    await _seed_job(db, "jA", [("v1", "completed", 1), ("v2", "completed", 1)])
    assert await job.finalize_jobs(db) == 1
    assert await _job_state(db, "jA") == "done"


async def test_job_becomes_partial_when_some_failed_permanently(db):
    await _seed_job(db, "jB", [("v1", "completed", 1), ("v2", "failed", 0)])
    await job.finalize_jobs(db)
    assert await _job_state(db, "jB") == "partial"


@pytest.mark.parametrize("in_flight_state", ["queued", "assigned", "pending", "downloading"])
async def test_job_stays_running_while_work_is_in_flight(db, in_flight_state):
    await _seed_job(db, "jC", [("v1", "completed", 1), ("v2", in_flight_state, 0)])
    assert await job.finalize_jobs(db) == 0
    assert await _job_state(db, "jC") == "running"


async def test_job_stays_running_while_a_transcript_awaits_delivery(db):
    await _seed_job(db, "jD", [("v1", "completed", 0)])
    assert await job.finalize_jobs(db) == 0
    assert await _job_state(db, "jD") == "running"


async def test_cancel_stops_pending_work_and_keeps_finished(db):
    await _seed_job(
        db,
        "jE",
        [
            ("v1", "completed", 1),
            ("v2", "queued", 0),
            ("v3", "assigned", 0),
            ("v4", "downloading", 0),
            ("v5", "failed", 0),
        ],
    )

    assert await job.cancel_job(db, "jE") == 3, "queued + assigned + downloading"
    assert await _job_state(db, "jE") == "cancelled"

    cur = await db.execute(
        "SELECT state, COUNT(*) AS n FROM tasks WHERE client_id = 'channel:jE' GROUP BY state"
    )
    counts = {r["state"]: r["n"] for r in await cur.fetchall()}
    assert counts == {"completed": 1, "failed": 1, "cancelled": 3}


async def test_cancel_is_idempotent_and_final(db):
    await _seed_job(db, "jF", [("v1", "queued", 0)])
    assert await job.cancel_job(db, "jF") == 1
    assert await job.cancel_job(db, "jF") == 0

    await job.finalize_jobs(db)
    assert await _job_state(db, "jF") == "cancelled", "finalize must not resurrect it"
