"""Channel job orchestration — discovery -> submission -> status.

A job discovers the channel's unique videos, persists them, then submits one
whisper task per video (the generic engine transcribes them in parallel).
Resumable: re-running skips videos that already have a live task.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import uuid

from ..api.transcribe_api import WHISPER_RUNNER_HASH, _prepare_whisper_input
from .discovery import discover_channel

log = logging.getLogger(__name__)

# Strong refs to background prepare tasks (asyncio only holds weak refs).
_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


async def create_job(
    db,
    channel_url: str,
    model: str,
    include_shorts: bool,
    formats: list[str],
    drive_folder_id: str | None,
    dry_run: bool,
    max_videos: int | None,
) -> str:
    job_id = uuid.uuid4().hex
    config = {
        "include_shorts": include_shorts,
        "formats": formats,
        "drive_folder_id": drive_folder_id,
        "dry_run": dry_run,
        "max_videos": max_videos,
    }
    await db.execute(
        """INSERT INTO channel_jobs (id, channel_url, model, config_json, state,
           created_at, updated_at) VALUES (?, ?, ?, ?, 'discovering', ?, ?)""",
        (job_id, channel_url, model, json.dumps(config), _now(), _now()),
    )
    await db.commit()
    return job_id


async def _set_state(db, job_id: str, state: str) -> None:
    await db.execute(
        "UPDATE channel_jobs SET state = ?, updated_at = ? WHERE id = ?",
        (state, _now(), job_id),
    )
    await db.commit()


async def run_job(db, task_service, config, job_id: str) -> None:
    """Background: discover, persist videos, then submit one task per video."""
    cur = await db.execute(
        "SELECT channel_url, model, config_json FROM channel_jobs WHERE id = ?", (job_id,)
    )
    row = await cur.fetchone()
    if row is None:
        return
    channel_url = row["channel_url"]
    model = row["model"]
    cfg = json.loads(row["config_json"])
    include_shorts = cfg.get("include_shorts", False)
    dry_run = cfg.get("dry_run", False)
    max_videos = cfg.get("max_videos")

    try:
        videos = await discover_channel(channel_url, include_shorts=include_shorts)
    except Exception:
        log.exception("channel discovery failed for job %s", job_id)
        await _set_state(db, job_id, "failed")
        return

    if max_videos:
        videos = videos[:max_videos]

    for v in videos:
        await db.execute(
            """INSERT OR IGNORE INTO channel_videos
               (job_id, video_id, url, title, duration, playlists_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, v["video_id"], v["url"], v["title"], v.get("duration"),
             json.dumps(v["playlists"], ensure_ascii=False)),
        )
        # Refresh playlist membership if the video was already known (idempotent)
        await db.execute(
            "UPDATE channel_videos SET playlists_json = ? WHERE job_id = ? AND video_id = ?",
            (json.dumps(v["playlists"], ensure_ascii=False), job_id, v["video_id"]),
        )
    await db.commit()

    if dry_run:
        await _set_state(db, job_id, "discovered")
        log.info("channel job %s: dry run, %d videos discovered", job_id, len(videos))
        return

    await _set_state(db, job_id, "submitting")
    coordinator_url = config.coordinator_url
    blob_dir = config.blob_dir
    import os

    cookies_hash = os.environ.get("SCRAPOWER_YT_COOKIES_HASH", "")

    # Submit one task per video that doesn't already have one (resume-safe).
    cur = await db.execute(
        "SELECT video_id, url, task_id FROM channel_videos WHERE job_id = ?", (job_id,)
    )
    to_submit = [(r["video_id"], r["url"]) for r in await cur.fetchall() if not r["task_id"]]

    for video_id, url in to_submit:
        task_id = uuid.uuid4().hex
        await task_service.submit(
            task_id=task_id,
            client_id=f"channel:{job_id}",
            runtime="python",
            executable_hash=WHISPER_RUNNER_HASH,
            input_hash="",
            task_type="whisper",
            requirements_json='{"gpu": true, "network": "outbound"}',
            gpu_required=True,
            deadline_ms=900000,
            initial_state="pending",
        )

        async def _prepare(u=url):
            return await _prepare_whisper_input(
                u, model, None, "json", cookies_hash, coordinator_url, db, blob_dir
            )

        _spawn(task_service.run_prepare(task_id, _prepare, log))
        await db.execute(
            "UPDATE channel_videos SET task_id = ? WHERE job_id = ? AND video_id = ?",
            (task_id, job_id, video_id),
        )
    await db.commit()
    await _set_state(db, job_id, "running")
    log.info("channel job %s: submitted %d task(s)", job_id, len(to_submit))


async def cancel_job(db, job_id: str) -> int:
    """Cancel a job: stop every task of it that hasn't finished yet.

    Bulk UPDATE rather than per-task transition(): one query instead of N
    round-trips for a 200+ video job, and it also covers DOWNLOADING tasks.
    Workers already running a cancelled task get their submit rejected and
    drain themselves via idle timeout — nothing to kill server-side.
    Returns the number of tasks cancelled.
    """
    cur = await db.execute(
        """UPDATE tasks SET state = 'cancelled', updated_at = ?
           WHERE client_id = ?
             AND state NOT IN ('completed', 'failed', 'cancelled')""",
        (_now(), f"channel:{job_id}"),
    )
    cancelled = cur.rowcount
    await db.execute(
        "UPDATE channel_jobs SET state = 'cancelled', updated_at = ? WHERE id = ?",
        (_now(), job_id),
    )
    await db.commit()
    log.info("channel job %s cancelled (%d task(s) stopped)", job_id, cancelled)
    return cancelled


async def finalize_jobs(db) -> int:
    """Move finished jobs out of 'running' (they used to stay there forever).

    A job is finished once no video is still in flight — i.e. none is waiting
    on a task that could still progress, and none is completed-but-undelivered.
    Then: 'done' if every video was delivered, else 'partial' (some videos
    failed permanently, e.g. the video is private or blocked on YouTube).
    Returns how many jobs were finalized.
    """
    cur = await db.execute(
        "SELECT id FROM channel_jobs WHERE state IN ('running', 'submitting')"
    )
    finalized = 0
    for row in await cur.fetchall():
        job_id = row["id"]
        cur2 = await db.execute(
            """SELECT COUNT(*) AS videos,
                      SUM(CASE WHEN cv.delivered = 1 THEN 1 ELSE 0 END) AS delivered,
                      SUM(CASE WHEN cv.task_id IS NULL
                                 OR t.state IN ('pending','downloading','queued','assigned')
                                 OR (t.state = 'completed' AND cv.delivered = 0)
                               THEN 1 ELSE 0 END) AS in_flight
               FROM channel_videos cv LEFT JOIN tasks t ON cv.task_id = t.id
               WHERE cv.job_id = ?""",
            (job_id,),
        )
        t = await cur2.fetchone()
        videos = t["videos"] or 0
        if videos == 0 or (t["in_flight"] or 0) > 0:
            continue
        state = "done" if (t["delivered"] or 0) == videos else "partial"
        await _set_state(db, job_id, state)
        log.info(
            "channel job %s finished: %s (%d/%d delivered)",
            job_id, state, t["delivered"] or 0, videos,
        )
        finalized += 1
    return finalized


async def job_status(db, job_id: str) -> dict | None:
    cur = await db.execute(
        "SELECT channel_url, model, state, config_json FROM channel_jobs WHERE id = ?", (job_id,)
    )
    job = await cur.fetchone()
    if job is None:
        return None

    cur = await db.execute(
        """SELECT COUNT(*) AS videos,
                  SUM(CASE WHEN cv.task_id IS NOT NULL THEN 1 ELSE 0 END) AS submitted,
                  SUM(CASE WHEN t.state = 'completed' THEN 1 ELSE 0 END) AS completed,
                  SUM(CASE WHEN t.state = 'failed' THEN 1 ELSE 0 END) AS failed,
                  SUM(CASE WHEN cv.delivered = 1 THEN 1 ELSE 0 END) AS delivered
           FROM channel_videos cv LEFT JOIN tasks t ON cv.task_id = t.id
           WHERE cv.job_id = ?""",
        (job_id,),
    )
    t = await cur.fetchone()
    return {
        "job_id": job_id,
        "channel_url": job["channel_url"],
        "model": job["model"],
        "state": job["state"],
        "totals": {
            "videos": t["videos"] or 0,
            "submitted": t["submitted"] or 0,
            "completed": t["completed"] or 0,
            "failed": t["failed"] or 0,
            "delivered": t["delivered"] or 0,
        },
    }
