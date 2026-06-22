"""Scrapower Coordinator — main entry point.

Start with: python -m scrapower.coordinator.main
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import structlog
import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.client_api import create_client_router
from .blob_store import blob_exists, get_blob, run_gc, store_blob
from .config import Config, load_config
from .db import init_db
from .domain import TaskService
from .embedded_worker import EmbeddedWorker
from .scheduler import Scheduler
from .security import rate_limit, require_auth, verify_api_key
from .task_manager import TaskManager
from .worker_gateway.router import router as worker_router
from .worker_gateway.session import SessionManager

# ──────────────────────────────────────────────────────────────
# State (shared across the app)
# ──────────────────────────────────────────────────────────────
config: Config
db: aiosqlite.Connection  # type: ignore[assignment]


# ──────────────────────────────────────────────────────────────
# Lifespan
# ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global config, db
    config = load_config()
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=getattr(logging, config.log_level.upper()))
    log = structlog.get_logger()
    log.info("scrapower coordinator starting", host=config.host, port=config.port)

    app.state.config = config

    db = await init_db(config.db_path)
    app.state.db = db
    log.info("database initialized", path=config.db_path)

    # Crypto: Fernet with per-deployment salt from SQLite
    from .crypto_utils import init_fernet

    init_fernet(config.db_path)
    log.info("fernet initialized")

    # Seed blob store with whisper_runner.py so tasks can reference it
    from pathlib import Path as _Path

    from .blob_store import store_blob as _store_blob

    whisper_path = _Path(__file__).parent.parent / "worker" / "runtimes" / "whisper_runner.py"
    if whisper_path.exists():
        whisper_hash = await _store_blob(db, config.blob_dir, whisper_path.read_bytes())
        log.info("whisper runner seeded", hash=whisper_hash[:12])
    else:
        log.warning("whisper runner not found at %s", whisper_path)

    # VPN pre-flight check (retry � VPN container may still be booting)
    vpn_proxy = os.environ.get("SCRAPOWER_VPN_PROXY", "")
    if vpn_proxy:
        for attempt in range(5):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "curl",
                    "-s",
                    "--socks5",
                    "127.0.0.1:1080",
                    "--max-time",
                    "5",
                    "https://ifconfig.me",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
                if proc.returncode == 0:
                    log.info("vpn check ok", vpn_ip=stdout.decode().strip())
                    break
                elif attempt < 4:
                    await asyncio.sleep(2)
                else:
                    log.warning("vpn check failed after 5 attempts (curl rc=%d)", proc.returncode)
            except Exception as e:
                if attempt < 4:
                    await asyncio.sleep(2)
                else:
                    log.warning("vpn check failed: %s", str(e)[:100])

    # ── OAuth configuration ──────────────────────────────────────
    oauth_client_id = os.environ.get("SCRAPOWER_GITHUB_CLIENT_ID", "")
    oauth_client_secret = os.environ.get("SCRAPOWER_GITHUB_CLIENT_SECRET", "")
    coordinator_url = os.environ.get(
        "SCRAPOWER_COORDINATOR_URL",
        "https://scrapower.talos-int.com",
    )
    if oauth_client_id and oauth_client_secret:
        from .auth_oauth import configure_oauth

        configure_oauth(oauth_client_id, oauth_client_secret, coordinator_url)
        log.info(
            "oauth configured",
            provider="github",
            callback=f"{coordinator_url}/auth/github/callback",
        )
    else:
        log.warning(
            "oauth not configured",
            hint="set SCRAPOWER_GITHUB_CLIENT_ID and SCRAPOWER_GITHUB_CLIENT_SECRET",
        )

    # Initialize session manager and zombie watchdog
    manager = SessionManager(
        heartbeat_interval_sec=config.heartbeat_interval_sec,
        heartbeat_miss_threshold=config.heartbeat_miss_threshold,
    )
    import scrapower.coordinator.worker_gateway.router as router_mod

    router_mod.session_manager = manager
    zombie_task = asyncio.create_task(manager.zombie_watchdog())

    # Configure Mode B HTTP pull rate limit
    from .worker_gateway.http_handler import configure_rate_limit

    configure_rate_limit(config.pull_rate_limit_per_ip)

    # Task manager and scheduler
    task_manager = TaskManager(db)
    app.state.task_manager = task_manager
    router_mod.task_manager = task_manager  # type: ignore[assignment]

    # Reputation service (tracks worker trust based on challenge results)
    from .reputation import ReputationService

    reputation_service = ReputationService(db)
    router_mod.reputation_service = reputation_service  # type: ignore[assignment]

    task_service = TaskService(task_manager, db, config)
    app.state.task_service = task_service
    router_mod.task_service = task_service  # type: ignore[assignment]

    # Register fallback handlers for tasks that may need coordinator-side prep
    from .api.transcribe_api import WHISPER_RUNNER_HASH, prepare_audio_fallback

    task_service.register_fallback(WHISPER_RUNNER_HASH, prepare_audio_fallback)

    # Purge orphaned assignments at startup (workers disconnected during restart)
    await _purge_orphaned_assignments(db, log)

    scheduler = Scheduler(
        task_service=task_service,
        session_manager=manager,
        tick_sec=config.scheduler_tick_sec,
        enforce_segregation=config.enforce_segregation,
        verification_mode=config.default_verification_mode,
        reputation_service=reputation_service,
        ws_assign_enabled=config.ws_assign_enabled,
    )
    sched_task = asyncio.create_task(scheduler.run())

    # Embedded worker (can be disabled via SCRAPOWER_EMBEDDED_WORKER=0 for tests)
    if os.environ.get("SCRAPOWER_EMBEDDED_WORKER", "1") not in ("0", "false", "no"):
        from ..worker.runtimes.wasm import WasmRuntime

        embedded = EmbeddedWorker(f"ws://127.0.0.1:{config.port}/worker/ws", WasmRuntime())
        embed_task = asyncio.create_task(embedded.start())
    else:
        embed_task = None

    # Start GC background task
    gc_task = asyncio.create_task(_gc_loop(config, db))

    # Start cleanup loop (release expired tasks, free blob refs)
    cleanup_task = asyncio.create_task(_cleanup_loop(task_service, log))

    # Kaggle GPU harvester (auto-start kernels when GPU tasks are waiting)
    kaggle_accounts_raw = os.environ.get("KAGGLE_ACCOUNTS", "")
    kaggle_accounts = []
    if kaggle_accounts_raw:
        try:
            kaggle_accounts = json.loads(kaggle_accounts_raw)
        except json.JSONDecodeError:
            log.warning("kaggle_accounts: invalid JSON, skipping harvester")
    if kaggle_accounts:
        from .harvester.kaggle import KaggleHarvester

        coordinator_url = os.environ.get(
            "SCRAPOWER_COORDINATOR_URL", "https://scrapower.talos-int.com"
        )
        kaggle_harvester = KaggleHarvester(
            accounts=kaggle_accounts,
            coordinator_url=coordinator_url,
            api_key=os.environ.get("SCRAPOWER_API_KEY", ""),
        )
        kaggle_task = asyncio.create_task(kaggle_harvester.run())
        log.info("kaggle harvester started", accounts=len(kaggle_accounts))
    else:
        kaggle_task = None
        kaggle_harvester = None

    try:
        yield
    finally:
        for t in (gc_task, zombie_task, sched_task, kaggle_task, cleanup_task):
            t.cancel()
        if embed_task is not None:
            embed_task.cancel()
        for t in (gc_task, zombie_task, sched_task, kaggle_task, cleanup_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        if embed_task is not None:
            try:
                await embed_task
            except asyncio.CancelledError:
                pass
        if db:
            await db.close()
        log.info("scrapower coordinator shut down")


async def _verify_assignment_token(db, token: str) -> bool:
    """Check if an assignment_token is valid (belongs to an ASSIGNED task)."""
    if not token:
        return False
    cursor = await db.execute(
        "SELECT id FROM tasks WHERE current_assignment_token = ? AND state = ?",
        (token, "assigned"),
    )
    row = await cursor.fetchone()
    return row is not None


async def _purge_orphaned_assignments(db, log) -> int:
    """Reset ASSIGNED tasks back to QUEUED at startup — they're orphaned after restart."""
    import time as _time

    cursor = await db.execute("SELECT id FROM tasks WHERE state = ?", ("assigned",))
    purged = 0
    async for row in cursor:
        await db.execute(
            "UPDATE tasks SET state = ?, current_assignment_token = NULL,"
            " assigned_worker_id = NULL, assigned_at = NULL, updated_at = ?"
            " WHERE id = ?",
            ("queued", str(_time.time()), row["id"]),
        )
        purged += 1
    if purged:
        await db.commit()
        log.info("purged %d orphaned assignments at startup", purged)
    return purged


async def _gc_loop(config: Config, db) -> None:
    """Run garbage collection every 6 hours."""
    log = structlog.get_logger()
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            deleted = await run_gc(
                db, config.blob_dir, config.blob_ttl_days, config.checkpoint_ttl_days
            )
            if deleted:
                log.info("gc completed", deleted_blobs=deleted)
        except Exception:
            log.exception("gc failed")


async def _cleanup_loop(task_service, log) -> None:
    """Release expired tasks and their blob references every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        try:
            cleaned = await task_service.cleanup_expired()
            if cleaned:
                log.info("cleanup completed", cleaned_tasks=cleaned)
        except Exception:
            log.exception("cleanup failed")


# ──────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="Scrapower Coordinator",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS � allows cross-origin embed on any website
from .cors_middleware import CORSMiddleware

app.add_middleware(CORSMiddleware)

app.include_router(worker_router)

# OAuth endpoints for connecting visitor accounts
from .auth_oauth import router as oauth_router

app.include_router(oauth_router)

# Serve static files (browser worker)
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# Serve Service Worker from root (required for scope "/")
@app.get("/sw.js")
async def service_worker():
    from fastapi.responses import FileResponse

    sw_path = static_dir / "sw.js"
    return FileResponse(sw_path, media_type="application/javascript")


# Client API router needs task_manager from app.state
client_api_router = create_client_router(require_auth)
app.include_router(client_api_router)

# Stats endpoint
from .api.stats_api import router as stats_router

app.include_router(stats_router)

# Transcription endpoint
from .api.transcribe_api import router as transcribe_router

app.include_router(transcribe_router)

log = structlog.get_logger()


# ──────────────────────────────────────────────────────────────
# Blob store endpoints
# ──────────────────────────────────────────────────────────────


@app.put("/blobs", status_code=200)
async def upload_blob(request: Request, _rate=Depends(rate_limit)):
    """Upload a blob. Workers use assignment_token, admins use API key."""
    body = await request.body()
    assignment_token = request.query_params.get("assignment_token", "")
    is_worker = assignment_token and await _verify_assignment_token(db, assignment_token)
    is_admin = verify_api_key(request)
    if not is_worker and not is_admin:
        return JSONResponse(
            {"error": "UNAUTHORIZED", "hint": "Use API key or assignment_token"},
            status_code=401,
        )
    if len(body) > config.max_blob_size_mb * 1024 * 1024:
        return JSONResponse(
            {"error": "PAYLOAD_TOO_LARGE", "max_size_mb": config.max_blob_size_mb},
            status_code=413,
        )
    hash_hex = await store_blob(db, config.blob_dir, body)
    return {"hash": hash_hex}


@app.get("/blobs/{hash_hex}")
async def download_blob(hash_hex: str):
    """Download a blob by its content hash."""
    from fastapi.responses import Response

    try:
        data = await get_blob(db, config.blob_dir, hash_hex)
    except ValueError:
        return JSONResponse({"error": "INVALID_HASH"}, status_code=400)
    if data is None:
        return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
    return Response(content=data, media_type="application/octet-stream")


@app.head("/blobs/{hash_hex}")
async def check_blob(hash_hex: str):
    """Check if a blob exists."""
    try:
        exists = await blob_exists(db, config.blob_dir, hash_hex)
    except ValueError:
        return JSONResponse(None, status_code=400)
    return JSONResponse(None, status_code=200 if exists else 404)


# ──────────────────────────────────────────────────────────────
# Health check
# ──────────────────────────────────────────────────────────────


@app.get("/embed")
@app.get("/worker")
async def embed_page(request: Request):
    """Serve the embeddable worker page (iframe-compatible)."""
    from fastapi.responses import FileResponse

    embed_path = Path(__file__).parent / "static" / "embed.html"
    resp = FileResponse(embed_path)
    # No X-Frame-Options = allow framing from any origin
    # CSP frame-ancestors is the modern equivalent (set via Caddy)
    return resp


@app.get("/")
async def homepage():
    """Serve the browser worker page."""
    from fastapi.responses import FileResponse

    index_path = Path(__file__).parent / "static" / "index.html"
    return FileResponse(index_path)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────


def main():
    """Entry point for `scrapower serve`."""
    c = load_config()
    uvicorn.run(
        "scrapower.coordinator.main:app",
        host=c.host,
        port=c.port,
        log_level=c.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
