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

# Whisper runner hash — computed at startup from the deployed file
WHISPER_RUNNER_PATH = (
    Path(__file__).parent.parent.parent / "worker" / "runtimes" / "whisper_runner.py"
)


def _compute_whisper_hash() -> str:
    """Compute SHA-256 hash of the deployed whisper_runner.py."""
    import hashlib

    if WHISPER_RUNNER_PATH.exists():
        return hashlib.sha256(WHISPER_RUNNER_PATH.read_bytes()).hexdigest()
    # Fallback: hash of empty (will fail at runtime but won't crash at import)
    return hashlib.sha256(b"").hexdigest()


WHISPER_RUNNER_HASH = _compute_whisper_hash()

log = logging.getLogger(__name__)

# === TEMPORARY BANDAGE (v0.4-v0.5) ===================================
# Coordinator-side audio download via yt-dlp. NOT the target architecture.
# TARGET: Workers download via homelab WireGuard SOCKS5 proxy (WG_PROXY).
# The coordinator is a lightweight orchestrator, not a download node.
# Remove _download_audio() and prepare_audio_fallback() once WireGuard
# is confirmed stable on all worker types (Modal, Kaggle, HF Spaces).
# =======================================================================
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

    model = body.get("model", "turbo")
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
        task_type="whisper",
        requirements_json='{"gpu": true, "network": "outbound"}',
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
    """Build input config for worker. Worker downloads audio + runs whisper."""
    import json as _json

    from ..blob_store import store_blob

    coordinator_url = os.environ.get("SCRAPOWER_COORDINATOR_URL", "https://scrapower.talos-int.com")
    input_bytes = _json.dumps(
        {
            "url": url,
            "cookies_hash": cookies_hash,
            "coordinator_url": coordinator_url,
            "model": model,
            "language": language,
            "format": fmt,
        }
    ).encode()

    return await store_blob(db, blob_dir, input_bytes)


async def _download_audio(url: str, cookies_hash: str, db, blob_dir: str) -> bytes:
    """[TEMPORARY BANDAGE] Download audio on coordinator via yt-dlp.

    TARGET: Workers download themselves via homelab WireGuard proxy.
    This function will be REMOVED once WG_PROXY is stable on all workers.
    """
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
                "-f",
                "bestaudio/best",
                "-o",
                tmpl,
                "--no-playlist",
                "--no-warnings",
                "--extractor-retries",
                "3",
                "--retries",
                "3",
            ]
            # Route YouTube through homelab WireGuard VPN (primary) or CyberGhost (fallback)
            wg_proxy = os.environ.get("SCRAPOWER_WG_PROXY", "")
            vpn_proxy = os.environ.get("SCRAPOWER_VPN_PROXY", "")
            proxy = wg_proxy or vpn_proxy
            if proxy:
                args += ["--proxy", proxy]
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
                if f.suffix in (".m4a", ".opus", ".webm", ".mp3", ".wav"):
                    return f.read_bytes()

            raise Exception("No audio file found after yt-dlp download")


async def prepare_audio_fallback(task, db, config):
    """[TEMPORARY BANDAGE] Download audio on coordinator when worker fails.

    Called on exit_code=2 (DOWNLOAD_FAILED). Downloads audio via VPN,
    stores as blob, updates task input_hash.

    TARGET: Workers download themselves via homelab WireGuard proxy.
    This function will be REMOVED once WG_PROXY is stable on all workers.
    """
    import json as _json

    from ..blob_store import get_blob, store_blob

    log = logging.getLogger(__name__)

    # 1. Parse current input to extract URL and cookies_hash
    input_data = await get_blob(db, config.blob_dir, task.input_hash)
    if not input_data:
        raise Exception(f"Input blob not found: {task.input_hash[:12]}")
    config_in = _json.loads(input_data.decode())
    url = config_in.get("url", "")
    cookies_hash = config_in.get("cookies_hash", "")
    if not url:
        raise Exception("No URL in task input — cannot prepare fallback")

    log.info("fallback: downloading audio for %s", task.id[:12])

    # 2. Download audio via coordinator (VPN + native format, no ffmpeg)
    audio_bytes = await _download_audio(url, cookies_hash, db, config.blob_dir)

    # 3. Store audio as blob
    audio_hash = await store_blob(db, config.blob_dir, audio_bytes)
    log.info("fallback: audio stored hash=%s", audio_hash[:12])

    # 4. Create new input JSON with audio_hash (no url — worker uses Mode A)
    coordinator_url = os.environ.get("SCRAPOWER_COORDINATOR_URL", "https://scrapower.talos-int.com")
    new_input = _json.dumps(
        {
            "audio_hash": audio_hash,
            "coordinator_url": coordinator_url,
            "model": config_in.get("model", "turbo"),
            "language": config_in.get("language"),
            "format": config_in.get("format", "json"),
        }
    ).encode()

    # 5. Store new input and update task
    new_input_hash = await store_blob(db, config.blob_dir, new_input)
    import time

    await db.execute(
        "UPDATE tasks SET input_hash = ?, updated_at = ? WHERE id = ?",
        (new_input_hash, str(time.time()), task.id),
    )
    await db.commit()
    log.info("fallback: task %s updated with audio_hash=%s", task.id[:12], audio_hash[:12])


@router.post("/update-cookies")
async def update_cookies(request: Request):
    """Update YouTube cookies hash at runtime (no restart needed).

    Body: { "hash": "sha256hex..." }

    The cookies blob must already exist in the blob store (uploaded via PUT /blobs).
    This endpoint only updates the env var so new tasks use the fresh cookies.
    """
    body = await request.json()
    new_hash = body.get("hash", "")
    if not new_hash or len(new_hash) != 64:
        raise HTTPException(400, {"error": "Valid 64-char SHA-256 hash required"})

    # Verify the blob exists
    db = request.app.state.db
    config = request.app.state.config
    from ..blob_store import blob_exists

    if not await blob_exists(db, config.blob_dir, new_hash):
        raise HTTPException(404, {"error": "Blob not found in store. Upload via PUT /blobs first."})

    os.environ["SCRAPOWER_YT_COOKIES_HASH"] = new_hash
    log.info("cookies hash updated to %s", new_hash[:12])

    return JSONResponse(
        {"status": "ok", "hash": new_hash, "hint": "New tasks will use these cookies."}
    )


@router.post("/batch")
async def batch_transcribe(request: Request):
    """Submit a YouTube playlist/channel for batch transcription.

    Body:
      { "url": "https://youtube.com/playlist?list=...",
        "model": "turbo", "language": "fr",
        "max_videos": 10 }

    Extracts video URLs via yt-dlp --flat-playlist (no download),
    creates one task per video, returns all task IDs.
    """
    body = await request.json()
    playlist_url = body.get("url", "")
    if not playlist_url:
        raise HTTPException(400, {"error": "url is required"})

    model = body.get("model", "turbo")
    language = body.get("language") or None
    fmt = body.get("format", "json")
    max_videos = min(body.get("max_videos", 10), 50)
    cookies_hash = body.get("cookies_hash") or os.environ.get("SCRAPOWER_YT_COOKIES_HASH", "")

    task_service = request.app.state.task_service
    db = request.app.state.db
    config = request.app.state.config

    # 1. Extract video URLs (flat, no download)
    videos = await _extract_playlist_urls(playlist_url, cookies_hash, db, config.blob_dir)
    if not videos:
        raise HTTPException(400, {"error": "No videos found in playlist"})

    videos = videos[:max_videos]

    # 2. Create a task per video
    tasks = []
    for v in videos:
        task_id = uuid.uuid4().hex
        await task_service.submit(
            task_id=task_id,
            client_id="anonymous",
            runtime="python",
            executable_hash=WHISPER_RUNNER_HASH,
            input_hash="",
            task_type="whisper",
            requirements_json='{"gpu": true, "network": "outbound"}',
            gpu_required=True,
            deadline_ms=900000,
            initial_state="pending",
        )

        async def _prepare(url=v["url"]):
            return await _prepare_whisper_input(
                url, model, language, fmt, cookies_hash, db, config.blob_dir
            )

        asyncio.create_task(task_service.run_prepare(task_id, _prepare, log))
        tasks.append({"task_id": task_id, "url": v["url"], "title": v.get("title", "")})

    return JSONResponse(
        {
            "batch_id": uuid.uuid4().hex[:12],
            "video_count": len(tasks),
            "model": model,
            "language": language or "auto",
            "tasks": tasks,
        }
    )


async def _extract_playlist_urls(
    playlist_url: str, cookies_hash: str, db, blob_dir: str
) -> list[dict]:
    """Extract video URLs from a playlist/channel via yt-dlp --flat-playlist."""
    import json as _json

    from ..blob_store import get_blob

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        cookies_path = None
        if cookies_hash:
            cookies_path = str(workdir / "cookies.txt")
            cookies_bytes = await get_blob(db, blob_dir, cookies_hash)
            if cookies_bytes:
                Path(cookies_path).write_bytes(cookies_bytes)

        args = ["yt-dlp", "--flat-playlist", "-j", "--no-warnings"]
        wg_proxy = os.environ.get("SCRAPOWER_WG_PROXY", "")
        vpn_proxy = os.environ.get("SCRAPOWER_VPN_PROXY", "")
        proxy = wg_proxy or vpn_proxy
        if proxy:
            args += ["--proxy", proxy]
        if cookies_path:
            args += ["--cookies", cookies_path]
        args.append(playlist_url)

        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(500, {"error": "Playlist extraction timed out"})

        if proc.returncode != 0:
            err = stderr.decode()[:500] if stderr else "unknown"
            raise HTTPException(400, {"error": f"yt-dlp: {err}"})

        videos = []
        for line in stdout.decode().strip().split("\n"):
            if not line:
                continue
            try:
                info = _json.loads(line)
                vid_url = (
                    info.get("url")
                    or info.get("webpage_url")
                    or f"https://youtube.com/watch?v={info.get('id', '')}"
                )
                if vid_url:
                    videos.append(
                        {
                            "url": vid_url,
                            "title": info.get("title", ""),
                            "duration": info.get("duration", 0),
                        }
                    )
            except _json.JSONDecodeError:
                pass

    return videos


@router.get("/models")
async def list_models():
    """List available Whisper models."""
    return JSONResponse(
        {
            "models": [
                {"name": "tiny", "size_mb": 75, "speed": "fastest", "accuracy": "lowest"},
                {"name": "base", "size_mb": 145, "speed": "fast", "accuracy": "low"},
                {"name": "small", "size_mb": 488, "speed": "medium", "accuracy": "medium"},
                {"name": "medium", "size_mb": 1536, "speed": "slow", "accuracy": "good"},
                {
                    "name": "turbo",
                    "size_mb": 1600,
                    "speed": "fast",
                    "accuracy": "excellent",
                    "recommended": True,
                },
                {"name": "large-v3", "size_mb": 3100, "speed": "slowest", "accuracy": "best"},
            ],
        }
    )
