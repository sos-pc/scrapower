"""Transcription API — submit video URLs for distributed Whisper transcription.

Workers download audio directly via WireGuard SOCKS5 proxy (WG_PROXY).
The coordinator is a lightweight orchestrator — no yt-dlp, no audio download.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

# Single authority for which files a delivery writes.
from ..channel.delivery import DEFAULT_FORMATS

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

# Keep strong references to background prepare tasks: asyncio only holds a weak
# reference to the task, so without this the GC can collect (and cancel) an
# in-flight download, silently stranding the task in PENDING.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Whisper's own task selector. Note it is not an output-language switch:
# "translate" only ever targets English (the model has no other translation
# direction), and `turbo` was not trained for it at all.
VALID_TASKS = ("transcribe", "translate")
NO_TRANSLATE_MODELS = ("turbo",)


DEFAULT_SINGLE_FOLDER = "_videos"


async def _register_delivery(
    db,
    task_id: str,
    url: str,
    folder: str,
    model: str,
    formats: list[str],
    title_override: str = "",
    glossary: dict[str, str] | None = None,
    whisper_task: str = "transcribe",
) -> None:
    """Make a single-video transcript eligible for the existing delivery sweep.

    Rather than duplicating the render/upload path, a one-video synthetic job is
    recorded in the same tables the channel subsystem uses, so delivery,
    markdown rendering, idempotency and job finalisation are all shared. The
    destination folder rides along as the "playlist" name.
    """
    import time as _time

    from ..channel.discovery import fetch_video_meta

    meta = await fetch_video_meta(url)
    video_id = meta.get("id") or task_id[:12]
    original = meta.get("title") or video_id
    # A caller-supplied title (typically a translation) names the file; the
    # provider's own title is kept so the header can point back to the source.
    title = title_override or original

    now = str(_time.time())
    job_id = f"single-{task_id[:16]}"
    config = {"formats": formats, "task": whisper_task}
    if glossary:
        config["glossary"] = glossary
    await db.execute(
        """INSERT OR IGNORE INTO channel_jobs
           (id, channel_url, model, config_json, state, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'running', ?, ?)""",
        (job_id, url, model, json.dumps(config, ensure_ascii=False), now, now),
    )
    await db.execute(
        """INSERT OR IGNORE INTO channel_videos
           (job_id, video_id, url, title, title_original, duration, playlists_json,
            task_id, delivered)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            job_id,
            video_id,
            url,
            title,
            original,
            meta.get("duration"),
            json.dumps([folder], ensure_ascii=False),
            task_id,
        ),
    )
    await db.commit()
    log.info("delivery registered for %s -> folder %r (title=%r)", task_id[:12], folder, title[:60])


def _validate_task(raw, model: str) -> str:
    """Normalise the requested Whisper task, rejecting combinations that can't work."""
    task = (raw or "transcribe").strip().lower()
    if task not in VALID_TASKS:
        raise HTTPException(400, {"error": f"task must be one of {VALID_TASKS}", "got": task})
    if task == "translate" and any(m in model for m in NO_TRANSLATE_MODELS):
        raise HTTPException(
            400,
            {
                "error": "this model is not trained for translation",
                "model": model,
                "hint": "use large-v3 (or another non-turbo model) for task=translate",
            },
        )
    return task


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
    whisper_task = _validate_task(body.get("task"), model)
    folder = (body.get("folder") or DEFAULT_SINGLE_FOLDER).strip() or DEFAULT_SINGLE_FOLDER
    deliver = bool(body.get("deliver", True))
    title_override = (body.get("title") or "").strip()
    glossary = body.get("glossary") or None
    if glossary is not None and not isinstance(glossary, dict):
        raise HTTPException(400, {"error": "glossary must be an object of wrong -> right"})
    # Nudge Whisper towards the right spelling of recurring proper nouns.
    # hotwords is the targeted mechanism; initial_prompt biases style/context.
    initial_prompt = (body.get("initial_prompt") or "").strip()
    hotwords = (body.get("hotwords") or "").strip()
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

    coordinator_url = config.coordinator_url

    async def _prepare():
        return await _prepare_whisper_input(
            url,
            model,
            language,
            fmt,
            cookies_hash,
            coordinator_url,
            db,
            config.blob_dir,
            task=whisper_task,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
        )

    _spawn(task_service.run_prepare(task_id, _prepare, log))

    if deliver:
        # Registered in the background: it costs a yt-dlp metadata lookup (a few
        # seconds through the residential proxy) and must not delay the response.
        formats = body.get("formats") or DEFAULT_FORMATS
        _spawn(
            _register_delivery(
                db,
                task_id,
                url,
                folder,
                model,
                formats,
                title_override=title_override,
                glossary=glossary,
                whisper_task=whisper_task,
            )
        )

    return JSONResponse(
        {
            "task_id": task_id,
            "status": "pending",
            "model": model,
            "language": language or "auto",
            "task": whisper_task,
            "format": fmt,
            "delivery": {"enabled": deliver, "folder": folder} if deliver else {"enabled": False},
            "hint": f"GET /results/{task_id} for transcript",
        }
    )


async def _prepare_whisper_input(
    url: str,
    model: str,
    language: str | None,
    fmt: str,
    cookies_hash: str,
    coordinator_url: str,
    db,
    blob_dir: str,
    task: str = "transcribe",
    initial_prompt: str = "",
    hotwords: str = "",
) -> str:
    """Build input config for worker. Worker downloads audio + runs whisper."""
    import json as _json

    from ..blob_store import store_blob

    input_bytes = _json.dumps(
        {
            "url": url,
            "cookies_hash": cookies_hash,
            "coordinator_url": coordinator_url,
            "model": model,
            "language": language,
            "format": fmt,
            "task": task,
            "initial_prompt": initial_prompt,
            "hotwords": hotwords,
        }
    ).encode()

    return await store_blob(db, blob_dir, input_bytes)


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

    # Pin the cookies blob: workers fetch it by hash embedded in the task
    # input (not via ref_count), so give it a standing reference + checkpoint
    # flag so GC never reclaims the active cookies. Idempotent per hash.
    await db.execute(
        "UPDATE blobs SET ref_count = ref_count + 1, is_checkpoint = 1 "
        "WHERE hash = ? AND is_checkpoint = 0",
        (new_hash,),
    )
    await db.commit()

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
    whisper_task = _validate_task(body.get("task"), model)
    initial_prompt = (body.get("initial_prompt") or "").strip()
    hotwords = (body.get("hotwords") or "").strip()
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

    coordinator_url = config.coordinator_url

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
                url,
                model,
                language,
                fmt,
                cookies_hash,
                coordinator_url,
                db,
                config.blob_dir,
                task=whisper_task,
                initial_prompt=initial_prompt,
                hotwords=hotwords,
            )

        _spawn(task_service.run_prepare(task_id, _prepare, log))
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
        except TimeoutError:
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
