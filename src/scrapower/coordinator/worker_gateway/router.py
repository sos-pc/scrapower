"""Worker Gateway router — WebSocket + HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, WebSocket

from .http_handler import pull, submit
from .session import SessionManager
from .ws_handler import handle_ws

router = APIRouter()

# Singleton — set by coordinator lifespan
session_manager: SessionManager | None = None
task_manager = None  # set by coordinator lifespan


@router.websocket("/worker/ws")
async def worker_ws(ws: WebSocket):
    """Mode A: Persistent WebSocket connection."""
    await handle_ws(ws, session_manager, task_manager)  # type: ignore[arg-type]


@router.post("/worker/pull")
async def worker_pull(request: Request):
    """Mode B: Ephemeral HTTP pull."""
    return await pull(request, session_manager)  # type: ignore[arg-type]


@router.post("/worker/submit")
async def worker_submit(request: Request):
    """Worker submits a result via HTTP."""
    return await submit(request, session_manager)  # type: ignore[arg-type]
