"""Transcription API — submit video URLs for distributed Whisper transcription."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/transcribe", tags=["transcribe"])

# Hardcoded hash of whisper_runner.py (pre-computed, updated when script changes)
WHISPER_RUNNER_HASH = "71c70d6c243e30a9e1c2f046011bd0a0f7935090915cb5edae84c91d0fa01781"


@router.post("")
async def transcribe(request: Request):
    """Submit a video for transcription.

    Body:
      { "url": "https://youtube.com/watch?v=...",
        "model": "large-v3",       // optional, default large-v3
        "language": "fr",          // optional, auto-detect if omitted
        "format": "srt" }          // optional: srt, txt, json (default json)

    Returns:
      { "task_id": "...", "status": "queued" }

    Poll GET /results/{task_id} for the transcript.
    """
    body = await request.json()
    url = body.get("url", "")
    if not url:
        raise HTTPException(400, {"error": "url is required"})

    model = body.get("model", "large-v3")
    language = body.get("language") or None
    fmt = body.get("format", "json")
    cookies_hash = body.get("cookies_hash", "") or ""

    task_service = request.app.state.task_service
    task_id = uuid.uuid4().hex

    # Input is the config JSON
    import json as _json

    input_bytes = _json.dumps(
        {
            "url": url,
            "model": model,
            "language": language,
            "format": fmt,
            "cookies_hash": cookies_hash,
        }
    ).encode()

    # Upload input to blob store
    from ..blob_store import store_blob

    config = request.app.state.config
    db = request.app.state.db
    input_hash = await store_blob(db, config.blob_dir, input_bytes)

    # Submit task
    await task_service.submit(
        task_id=task_id,
        client_id="anonymous",
        runtime="python",
        executable_hash=WHISPER_RUNNER_HASH,
        input_hash=input_hash,
        gpu_required=False,
        deadline_ms=600000,  # 10 min for Whisper
    )

    return JSONResponse(
        {
            "task_id": task_id,
            "status": "queued",
            "model": model,
            "language": language or "auto",
            "format": fmt,
            "cookies_hash": cookies_hash or None,
            "hint": f"GET /results/{task_id} for transcript",
        }
    )


@router.get("/models")
async def list_models():
    """List available Whisper models."""
    return JSONResponse(
        {
            "models": [
                {"name": "tiny", "size_mb": 75, "speed": "fastest", "accuracy": "lowest"},
                {"name": "base", "size_mb": 145, "speed": "fast", "accuracy": "low"},
                {"name": "small", "size_mb": 488, "speed": "medium", "accuracy": "medium"},
                {"name": "medium", "size_mb": 1536, "speed": "slow", "accuracy": "high"},
                {"name": "large-v3", "size_mb": 3100, "speed": "slowest", "accuracy": "highest"},
            ],
        }
    )
