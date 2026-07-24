"""Channel transcription API — submit a whole YouTube channel for transcription.

POST /transcribe/channel   -> discover playlists/videos, submit tasks, deliver
GET  /transcribe/channel/{job_id} -> progress

Auth is enforced at registration (Depends(require_auth) in main.py).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..channel.job import create_job, job_status, run_job

router = APIRouter(prefix="/transcribe/channel", tags=["channel"])
log = logging.getLogger(__name__)

_bg_jobs: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_jobs.add(task)
    task.add_done_callback(_bg_jobs.discard)


@router.post("")
async def submit_channel(request: Request):
    """Kick off a channel transcription job.

    Body: { "channel_url": "...", "model": "turbo", "include_shorts": false,
            "formats": ["md","json"], "drive_folder_id": null,
            "dry_run": false, "max_videos": null }
    """
    body = await request.json()
    channel_url = body.get("channel_url", "")
    if not channel_url:
        raise HTTPException(400, {"error": "channel_url is required"})

    model = body.get("model", "turbo")
    include_shorts = bool(body.get("include_shorts", False))
    formats = body.get("formats") or ["md", "json"]
    drive_folder_id = body.get("drive_folder_id")
    dry_run = bool(body.get("dry_run", False))
    max_videos = body.get("max_videos")

    db = request.app.state.db
    task_service = request.app.state.task_service
    config = request.app.state.config

    job_id = await create_job(
        db, channel_url, model, include_shorts, formats, drive_folder_id, dry_run, max_videos
    )
    _spawn(run_job(db, task_service, config, job_id))

    return JSONResponse(
        {
            "job_id": job_id,
            "state": "discovering",
            "dry_run": dry_run,
            "hint": f"GET /transcribe/channel/{job_id} for progress",
        }
    )


@router.get("/{job_id}")
async def get_channel_job(job_id: str, request: Request):
    status = await job_status(request.app.state.db, job_id)
    if status is None:
        raise HTTPException(404, {"error": "NOT_FOUND"})
    return JSONResponse(status)
