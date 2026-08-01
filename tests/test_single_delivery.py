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

    monkeypatch.setattr("scrapower.coordinator.channel.discovery.fetch_video_meta", fake_meta)
    return fake_meta


async def test_registration_makes_the_task_deliverable(db, no_metadata_lookup):
    await transcribe_api._register_delivery(
        db,
        "t" * 32,
        "https://www.bilibili.com/video/BV1xK4y1J77Q/?p=1",
        "Cours",
        "large-v3",
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

    cur = await db.execute("SELECT COUNT(*) AS n FROM channel_videos WHERE task_id = ?", (task_id,))
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
        db,
        task_id,
        "https://www.bilibili.com/video/BV1xK4y1J77Q/?p=1",
        "Cours",
        "large-v3",
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


async def test_delivery_of_a_single_video_is_idempotent(db, blob_dir, config, no_metadata_lookup):
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


# ── Delivered formats ──────────────────────────────────────────────────────


def test_markdown_only_by_default():
    """The raw JSON doubles the file count for something nobody opens."""
    assert delivery.DEFAULT_FORMATS == ["md"]
    assert transcribe_api.DEFAULT_FORMATS is delivery.DEFAULT_FORMATS, "one authority, not two"


async def test_a_job_without_formats_gets_markdown_only(db, blob_dir, config, no_metadata_lookup):
    """The fallback path: rows written before formats was recorded."""
    h = await bs.store_blob(db, blob_dir, json.dumps({"segments": []}).encode())
    task_id = "z" * 32
    await db.execute(
        "INSERT INTO tasks (id, client_id, state, output_hash, created_at, updated_at)"
        " VALUES (?, 'anonymous', 'completed', ?, '1', '1')",
        (task_id, h),
    )
    await db.commit()
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", "Cours", "large-v3", ["md"]
    )
    # Simulate a job row that never recorded a formats key at all.
    await db.execute(
        "UPDATE channel_jobs SET config_json = '{}' WHERE id = ?", (f"single-{task_id[:16]}",)
    )
    await db.commit()

    assert await delivery.deliver_completed(db, blob_dir, config) == 1

    out_dir = Path(config.transcripts_dir) / "Cours"
    assert list(out_dir.glob("*.md")), "markdown is always written"
    assert not list(out_dir.glob("*.json")), "json must not appear unless asked for"


async def test_json_is_still_delivered_when_asked_for(db, blob_dir, config, no_metadata_lookup):
    """Opting in must keep working -- the default changed, not the capability."""
    h = await bs.store_blob(db, blob_dir, json.dumps({"segments": []}).encode())
    task_id = "0" * 32
    await db.execute(
        "INSERT INTO tasks (id, client_id, state, output_hash, created_at, updated_at)"
        " VALUES (?, 'anonymous', 'completed', ?, '1', '1')",
        (task_id, h),
    )
    await db.commit()
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", "AvecJson", "large-v3", ["md", "json"]
    )

    assert await delivery.deliver_completed(db, blob_dir, config) == 1

    out_dir = Path(config.transcripts_dir) / "AvecJson"
    assert list(out_dir.glob("*.md"))
    assert list(out_dir.glob("*.json"))


# ── Gemini structure + written course rendering ─────────────────────────────


def test_write_course_requires_task_transcribe():
    """The pipeline reads the source-language transcript directly; feeding it
    an already-translated (English) one would add a second, avoidable hop."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        transcribe_api._validate_write_course(True, "translate")
    assert exc.value.status_code == 400


def test_write_course_allowed_with_transcribe():
    transcribe_api._validate_write_course(True, "transcribe")  # must not raise


def test_write_course_false_is_never_checked():
    transcribe_api._validate_write_course(False, "translate")  # must not raise


async def test_registration_stores_the_write_course_flag(db, no_metadata_lookup):
    task_id = "1" * 32
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", "Cours", "large-v3", ["md"], write_course=True
    )
    cur = await db.execute(
        "SELECT config_json FROM channel_jobs WHERE id = ?", (f"single-{task_id[:16]}",)
    )
    cfg = json.loads((await cur.fetchone())["config_json"])
    assert cfg["write_course"] is True


async def test_registration_omits_the_flag_when_not_requested(db, no_metadata_lookup):
    task_id = "2" * 32
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", "Cours", "large-v3", ["md"]
    )
    cur = await db.execute(
        "SELECT config_json FROM channel_jobs WHERE id = ?", (f"single-{task_id[:16]}",)
    )
    cfg = json.loads((await cur.fetchone())["config_json"])
    assert "write_course" not in cfg


def _fake_course_pipeline(monkeypatch, *, structure_calls=None, rewrite_calls=None):
    """Stub out the two Gemini call sites so these tests never touch the network."""

    async def fake_structure(api_key, segments):
        if structure_calls is not None:
            structure_calls.append((api_key, segments))
        return [{"start_index": 0, "end_index": len(segments) - 1, "title_fr": "Section unique"}]

    async def fake_full_rewrite(api_key, segments, structure, language="?"):
        if rewrite_calls is not None:
            rewrite_calls.append((api_key, segments, structure))
        return [
            [
                {
                    "start_index": s["start_index"],
                    "title_fr": "",
                    "text_fr": "Texte réécrit intégral.",
                }
            ]
            for s in structure
        ]

    monkeypatch.setattr("scrapower.coordinator.channel.synthesis.build_structure", fake_structure)
    monkeypatch.setattr(
        "scrapower.coordinator.channel.synthesis.build_full_rewrite", fake_full_rewrite
    )


async def _seed_write_course_job(db, blob_dir, task_id, folder="Cours"):
    transcript = json.dumps(
        {
            "language": "zh",
            "duration": 60.0,
            "segments": [{"start": 0, "end": 4, "text": "第一课"}],
        }
    )
    h = await bs.store_blob(db, blob_dir, transcript.encode())
    await db.execute(
        "INSERT INTO tasks (id, client_id, state, output_hash, created_at, updated_at)"
        " VALUES (?, 'anonymous', 'completed', ?, '1', '1')",
        (task_id, h),
    )
    await db.commit()
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", folder, "large-v3", ["md"], write_course=True
    )


async def test_sweep_produces_a_second_file_when_write_course_is_set(
    db, blob_dir, config, no_metadata_lookup, monkeypatch
):
    config.gemini_api_keys = ["test-gemini-key"]
    calls = []
    _fake_course_pipeline(monkeypatch, structure_calls=calls)

    task_id = "3" * 32
    await _seed_write_course_job(db, blob_dir, task_id)

    assert await delivery.deliver_completed(db, blob_dir, config) == 1
    assert calls[0][0] == ["test-gemini-key"], "the configured key pool must reach the Gemini call"

    out_dir = Path(config.transcripts_dir) / "Cours"
    base = f"{delivery.sanitize_name('Lecture 01 - Methods')} [BV1xK4y1J77Q_p1]"
    assert (out_dir / f"{base}.md").exists(), "the transcript is still delivered unconditionally"
    course_file = out_dir / f"{base} - Cours.md"
    assert course_file.exists()
    assert "Texte réécrit intégral." in course_file.read_text(encoding="utf-8")


async def test_no_second_file_when_write_course_is_not_set(
    db, blob_dir, config, no_metadata_lookup, monkeypatch
):
    """Regression: an ordinary job must not grow an extra file."""
    _fake_course_pipeline(monkeypatch)
    h = await bs.store_blob(db, blob_dir, json.dumps({"segments": []}).encode())
    task_id = "4" * 32
    await db.execute(
        "INSERT INTO tasks (id, client_id, state, output_hash, created_at, updated_at)"
        " VALUES (?, 'anonymous', 'completed', ?, '1', '1')",
        (task_id, h),
    )
    await db.commit()
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", "Cours", "large-v3", ["md"]
    )

    assert await delivery.deliver_completed(db, blob_dir, config) == 1
    assert not list((Path(config.transcripts_dir) / "Cours").glob("* - Cours.md"))


async def test_missing_gemini_key_leaves_the_video_undelivered(
    db, blob_dir, config, no_metadata_lookup, monkeypatch
):
    """Config with no key configured: retry automatically once one is added,
    rather than silently giving up or crashing the sweep."""
    _fake_course_pipeline(monkeypatch)  # must not even be reached
    assert getattr(config, "gemini_api_keys", []) == []

    task_id = "5" * 32
    await _seed_write_course_job(db, blob_dir, task_id)

    assert await delivery.deliver_completed(db, blob_dir, config) == 0

    cur = await db.execute("SELECT delivered FROM channel_videos WHERE task_id = ?", (task_id,))
    assert (await cur.fetchone())["delivered"] == 0, "must stay eligible for the next sweep"
    assert not list(Path(config.transcripts_dir).rglob("*.md")), "nothing partial gets written"


async def test_no_segments_to_write_a_course_from_leaves_the_video_undelivered(
    db, blob_dir, config, no_metadata_lookup, monkeypatch
):
    """An empty transcript must not reach the Gemini pipeline at all -- there is
    nothing meaningful to structure or rewrite."""
    config.gemini_api_keys = ["test-gemini-key"]
    calls = []
    _fake_course_pipeline(monkeypatch, structure_calls=calls)

    h = await bs.store_blob(db, blob_dir, json.dumps({"segments": []}).encode())
    task_id = "7" * 32
    await db.execute(
        "INSERT INTO tasks (id, client_id, state, output_hash, created_at, updated_at)"
        " VALUES (?, 'anonymous', 'completed', ?, '1', '1')",
        (task_id, h),
    )
    await db.commit()
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", "Cours", "large-v3", ["md"], write_course=True
    )

    assert await delivery.deliver_completed(db, blob_dir, config) == 0
    assert not calls, "the Gemini pipeline must never be called with zero segments"
    cur = await db.execute("SELECT delivered FROM channel_videos WHERE task_id = ?", (task_id,))
    assert (await cur.fetchone())["delivered"] == 0


async def test_a_failing_gemini_call_leaves_the_video_undelivered(
    db, blob_dir, config, no_metadata_lookup, monkeypatch
):
    """Same retry-on-next-sweep behaviour for a transient Gemini failure as for
    a missing key -- the row-level try/except in deliver_completed is what
    gives us this for free."""
    from scrapower.coordinator.channel import synthesis as synth_mod

    config.gemini_api_keys = ["test-gemini-key"]

    # Structure has its own fallback and never raises (see synthesis.py), so
    # the failure is made to happen in the rewrite, the stage allowed to fail.
    async def fake_structure(api_key, segments):
        return [{"start_index": 0, "end_index": len(segments) - 1, "title_fr": "S"}]

    async def failing_rewrite(api_key, segments, structure, language="?"):
        raise synth_mod.GeminiError("boom")

    monkeypatch.setattr("scrapower.coordinator.channel.synthesis.build_structure", fake_structure)
    monkeypatch.setattr(
        "scrapower.coordinator.channel.synthesis.build_full_rewrite", failing_rewrite
    )

    task_id = "6" * 32
    await _seed_write_course_job(db, blob_dir, task_id)

    assert await delivery.deliver_completed(db, blob_dir, config) == 0
    cur = await db.execute("SELECT delivered FROM channel_videos WHERE task_id = ?", (task_id,))
    assert (await cur.fetchone())["delivered"] == 0
    assert not list(Path(config.transcripts_dir).rglob("*.md")), (
        "the transcript itself must not be written mid-retry either, so a later "
        "successful pass doesn't have to reconcile a half-delivered state"
    )


# ── Reusing an already-written course instead of re-paying for it ──────────
#
# The bug this covers, live: Gemini generation succeeded, an expired Drive
# OAuth token failed the delivery afterwards, and every 30s retry redid the
# entire generation (1 structure call + one per section) to reproduce a
# result that was already sitting on disk, burning a day's quota for nothing.


def test_existing_course_md_is_read_when_present(tmp_path):
    import types

    config = types.SimpleNamespace(transcripts_dir=str(tmp_path))
    meta = {"title": "T", "video_id": "V1", "playlists": ["Cours"]}
    pdir = tmp_path / "Cours"
    pdir.mkdir()
    (pdir / delivery._course_filename(meta)).write_text("Déjà écrit.", encoding="utf-8")

    assert delivery._existing_course_md(config, meta) == "Déjà écrit."


def test_existing_course_md_is_none_when_absent(tmp_path):
    import types

    config = types.SimpleNamespace(transcripts_dir=str(tmp_path))
    meta = {"title": "T", "video_id": "V1", "playlists": ["Cours"]}
    assert delivery._existing_course_md(config, meta) is None


async def test_maybe_write_course_reuses_an_existing_file_without_calling_gemini(
    config, monkeypatch
):
    async def must_not_be_called(*a, **kw):
        raise AssertionError("Gemini must not be called when a course already exists on disk")

    monkeypatch.setattr(
        "scrapower.coordinator.channel.synthesis.build_structure", must_not_be_called
    )
    monkeypatch.setattr(
        "scrapower.coordinator.channel.synthesis.build_full_rewrite", must_not_be_called
    )

    meta = {"title": "T", "video_id": "V1", "playlists": ["Cours"]}
    pdir = Path(config.transcripts_dir) / "Cours"
    pdir.mkdir(parents=True)
    (pdir / delivery._course_filename(meta)).write_text("Déjà écrit.", encoding="utf-8")

    out = await delivery._maybe_write_course(config, meta, "{}", {"write_course": True})
    assert out == "Déjà écrit."


async def test_a_retry_after_a_downstream_failure_reuses_the_file_and_succeeds(
    db, blob_dir, config, no_metadata_lookup, monkeypatch
):
    """The exact scenario that burned quota: Gemini already succeeded once (the
    file is on disk), something unrelated failed delivery, and the next sweep
    must finish the job from the existing file rather than regenerating it."""
    config.gemini_api_keys = ["test-gemini-key"]

    async def must_not_be_called(*a, **kw):
        raise AssertionError("must reuse the existing file, not call Gemini again")

    monkeypatch.setattr(
        "scrapower.coordinator.channel.synthesis.build_structure", must_not_be_called
    )
    monkeypatch.setattr(
        "scrapower.coordinator.channel.synthesis.build_full_rewrite", must_not_be_called
    )

    task_id = "8" * 32
    await _seed_write_course_job(db, blob_dir, task_id)

    # Simulate the prior (successful-at-Gemini) attempt having already written
    # the course file before failing at some later, unrelated step.
    meta = {"title": "Lecture 01 - Methods", "video_id": "BV1xK4y1J77Q_p1", "playlists": ["Cours"]}
    pdir = Path(config.transcripts_dir) / "Cours"
    pdir.mkdir(parents=True)
    (pdir / delivery._course_filename(meta)).write_text("Cours déjà généré.", encoding="utf-8")

    assert await delivery.deliver_completed(db, blob_dir, config) == 1
    course_file = pdir / delivery._course_filename(meta)
    assert course_file.read_text(encoding="utf-8") == "Cours déjà généré."
