"""Worker Gateway router — WebSocket + HTTP endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse

from .http_handler import _save_worker_logs, pull, submit
from .session import SessionManager
from .ws_handler import handle_ws

router = APIRouter()

# Singleton — set by coordinator lifespan
session_manager: SessionManager | None = None
task_manager = None  # set by coordinator lifespan
task_service = None  # type: ignore[assignment]


@router.websocket("/worker/ws")
async def worker_ws(ws: WebSocket):
    """Mode A: Persistent WebSocket connection."""
    await handle_ws(ws, session_manager, task_service)  # type: ignore[arg-type]


@router.post("/worker/pull")
async def worker_pull(request: Request):
    """Mode B: Ephemeral HTTP pull."""
    return await pull(request, session_manager)  # type: ignore[arg-type]


@router.post("/worker/submit")
async def worker_submit(request: Request):
    """Worker submits a result via HTTP."""
    return await submit(request, session_manager)  # type: ignore[arg-type]


@router.post("/worker/heartbeat")
async def worker_heartbeat(request: Request):
    """Worker sends heartbeat with current logs during task execution.

    Body:
      { "type": "heartbeat",
        "worker_id": "kaggle-abc123",
        "task_id": "def456...",
        "assignment_token": "...",
        "logs": "recent stderr output..." }

    Side effect: updates assigned_at on the task, keeping the assignment
    alive.  Returns task_valid=false if the task was reassigned or the
    token no longer matches — the worker should abort and re-pull.
    """
    import time

    from ..task_manager import TaskState

    body = await request.json()
    if body.get("type") != "heartbeat":
        return JSONResponse(
            {"type": "error", "code": "INVALID_MESSAGE", "message": "Expected type=heartbeat"},
            status_code=400,
        )

    worker_id = body.get("worker_id", "unknown")
    task_id = body.get("task_id", "")
    token = body.get("assignment_token", "")
    logs = body.get("logs", "")

    # Always save logs for debugging
    if logs:
        save_id = task_id or worker_id
        await _save_worker_logs(save_id, logs, prefix="heartbeat")

    # Verify task assignment is still valid and extend the lease
    task_valid = False
    if task_id and token and task_service:
        try:
            task = await task_service.get(task_id)
            if task and task.state == TaskState.ASSIGNED and task.current_assignment_token == token:
                # Reset assigned_at so requeue_stale doesn't kill active workers
                now = str(time.time())
                await task_service._tm._db.execute(
                    "UPDATE tasks SET assigned_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, task_id),
                )
                await task_service._tm._db.commit()
                task_valid = True
        except Exception:
            pass  # best-effort: logs are already saved

    return JSONResponse(
        {
            "type": "heartbeat_ack",
            "accepted": bool(logs),
            "task_valid": task_valid,
        }
    )
