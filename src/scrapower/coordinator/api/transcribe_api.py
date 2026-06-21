"""Transcription API — submit video URLs for distributed Whisper transcription.

Audio download happens async on the coordinator (which has real internet),
then the task is queued for workers that don't need external network access.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/transcribe", tags=["transcribe"])

WHISPER_RUNNER_HASH = "51cda979f6e95e8e04fa80d3dcabd140937570c4464430c97702ef807edcd858"

log = logging.getLogger(__name__)

# Limit concurrent yt-dlp downloads to avoid saturating Oracle bandwidth
_download_sem = asyncio.Semaphore(2)


@router.post("")
async def transcribe(request: Request):
    """Submit a video for transcription. Returns immediately.

    Body:
      { "url": "https://youtube.com/watch?v=...",
        "model": "tiny",
        "language": "fr",
        "format": "srt" }

    The audio is downloaded asynchronously on the coordinator side.
    Poll GET /results/{task_id} for the transcript.
    """
    body = await request.json()
    url = body.get("url", "")
    if not url:
        raise HTTPException(400, {"error": "url is required"})

    model = body.get("model", "large-v3")
    language = body.get("language") or None
    fmt = body.get("format", "json")
    cookies_hash = body.get("cookies_hash") or os.environ.get("SCRAPOWER_YT_COOKIES_HASH", "")

    task_service = request.app.state.task_service
    task_id = uuid.uuid4().hex

    # Create task in PENDING state (audio not downloaded yet)
    await task_service.submit(
        task_id=task_id,
        client_id="anonymous",
        runtime="python",
        executable_hash=WHISPER_RUNNER_HASH,
        input_hash="",  # placeholder, will be set after download
        gpu_required=True,
        deadline_ms=900000,
        initial_state="pending",
    )

    # Launch background prepare (download audio → blob → queue)
    db = request.app.state.db
    config = request.app.state.config

    async def _prepare():
        return await _prepare_whisper_input(
            url, model, language, fmt, cookies_hash, db, config.blob_dir
        )

    asyncio.create_task(task_service.run_prepare(task_id, _prepare, log))

    return JSONResponse(
        {
            "task_id": task_id,
            "status": "pending",
            "model": model,
            "language": language or "auto",
            "format": fmt,
            "hint": f"GET /results/{task_id} for transcript",
        }
    )


async def _prepare_whisper_input(
    url: str,
    model: str,
    language: str | None,
    fmt: str,
    cookies_hash: str,
    db,
    blob_dir: str,
) -> str:
    """Download YouTube audio, build input config, return input_hash."""
    audio_bytes = await _download_audio(url, cookies_hash, db, blob_dir)

    from ..blob_store import store_blob

    audio_hash = await store_blob(db, blob_dir, audio_bytes)

    import json as _json

    input_bytes = _json.dumps(
        {
            "audio_hash": audio_hash,
            "coordinator_url": "https://scrapower.talos-int.com",
            "model": model,
            "language": language,
            "format": fmt,
        }
    ).encode()

    return await store_blob(db, blob_dir, input_bytes)


async def _download_audio(url: str, cookies_hash: str, db, blob_dir: str) -> bytes:
    """Download audio from a URL. Uses yt-dlp for YouTube, direct for .wav/.mp3."""
    import urllib.request

    DIRECT_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".opus", ".aac", ".weba")
    is_direct = any(url.lower().endswith(e) for e in DIRECT_EXTS)

    if is_direct:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp:
            urllib.request.urlretrieve(url, tmp.name)
            data = Path(tmp.name).read_bytes()
            Path(tmp.name).unlink()
            return data
    async with _download_sem:
        from ..blob_store import get_blob

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)

            # Write cookies if provided
            cookies_path = None
            if cookies_hash:
                cookies_path = str(workdir / "cookies.txt")
                cookies_bytes = await get_blob(db, blob_dir, cookies_hash)
                if cookies_bytes:
                    Path(cookies_path).write_bytes(cookies_bytes)

            tmpl = str(workdir / "%(id)s.%(ext)s")
            args = [
                "yt-dlp",
                "-x",
                "--audio-format",
                "mp3",
                "--audio-quality",
                "0",
                "-o",
                tmpl,
                "--no-playlist",
                "--no-warnings",
                "--extractor-retries",
                "3",
                "--retries",
                "3",
            ]
            # Route YouTube through VPN proxy to avoid datacenter IP ban
            vpn_proxy = os.environ.get("SCRAPOWER_VPN_PROXY", "")
            if vpn_proxy:
                args += ["--proxy", vpn_proxy]
            if cookies_path:
                args += ["--cookies", cookies_path]
            args.append(url)

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                raise Exception("yt-dlp timed out after 300s")

            if proc.returncode != 0:
                err = stderr.decode()[:1000] if stderr else "unknown error"
                raise Exception(f"yt-dlp failed: {err}")

            for f in workdir.iterdir():
                if f.suffix in (".mp3", ".m4a", ".opus", ".webm", ".wav"):
                    return f.read_bytes()

            raise Exception("No audio file found after yt-dlp download")


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
