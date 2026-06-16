"""HTTP handler for Worker Protocol Mode B.

Stateless pull/submit cycle for ephemeral workers (Lambda, Cloud Run, etc.).
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from .session import SessionManager

log = logging.getLogger(__name__)


async def pull(request: Request, sessions: SessionManager):
    """Worker pulls for a task. Returns task or no_task."""
    body = await request.json()

    # Validate minimal structure
    if body.get("type") != "pull":
        return JSONResponse({"type": "error", "code": "INVALID_MESSAGE"}, status_code=400)

    # Future: use body.get("worker_id") and body.get("capabilities") for pull matching
    return JSONResponse({"type": "pull_response", "task": None})


async def submit(request: Request, sessions: SessionManager):
    """Worker submits a result."""
    body = await request.json()

    if body.get("type") != "submit":
        return JSONResponse({"type": "error", "code": "INVALID_MESSAGE"}, status_code=400)

    task_id = body.get("task_id")

    # No task manager yet — any submit is for a non-existent task
    return JSONResponse(
        {"type": "submit_ack", "task_id": task_id, "accepted": False, "credit_earned": 0},
        status_code=404,
    )
