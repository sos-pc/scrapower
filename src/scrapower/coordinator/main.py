"""Scrapower Coordinator — main entry point.

Start with: python -m scrapower.coordinator.main
"""

from __future__ import annotations

import asyncio
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
from .security import rate_limit, require_auth
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

    # Initialize session manager and zombie watchdog
    manager = SessionManager(
        heartbeat_interval_sec=config.heartbeat_interval_sec,
        heartbeat_miss_threshold=config.heartbeat_miss_threshold,
    )
    import scrapower.coordinator.worker_gateway.router as router_mod

    router_mod.session_manager = manager
    zombie_task = asyncio.create_task(manager.zombie_watchdog())

    # Task manager and scheduler
    task_manager = TaskManager(db)
    app.state.task_manager = task_manager
    router_mod.task_manager = task_manager  # type: ignore[assignment]
    task_service = TaskService(task_manager)
    app.state.task_service = task_service
    router_mod.task_service = task_service  # type: ignore[assignment]

    # Purge orphaned assignments at startup (workers disconnected during restart)
    await _purge_orphaned_assignments(db, log)

    scheduler = Scheduler(
        task_service=task_service,
        session_manager=manager,
        tick_sec=config.scheduler_tick_sec,
        enforce_segregation=config.enforce_segregation,
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

    try:
        yield
    finally:
        for t in (gc_task, zombie_task, sched_task):
            t.cancel()
        if embed_task is not None:
            embed_task.cancel()
        for t in (gc_task, zombie_task, sched_task):
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


# ──────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="Scrapower Coordinator",
    version="0.1.0",
    lifespan=lifespan,
)

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

log = structlog.get_logger()


# ──────────────────────────────────────────────────────────────
# Blob store endpoints
# ──────────────────────────────────────────────────────────────


@app.put("/blobs", status_code=200)
async def upload_blob(request: Request, _rate=Depends(rate_limit)):
    """Upload a blob. Returns its content hash."""
    body = await request.body()
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
