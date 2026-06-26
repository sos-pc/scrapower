"""HTTP handler for Worker Protocol Mode B.

Stateless pull/submit cycle for ephemeral workers (Kaggle, Modal, HF Spaces).
This is the ONLY task dispatch protocol.

Flow:
  Worker                     Coordinator
    |-- POST /worker/pull --->|  "Any queued task matching my capabilities?"
    |<-- {task, token} -------|  Atomic assign, one worker wins
    |                          |
    |   [execute 2-15 min]     |
    |   [upload blob via PUT]  |
    |                          |
    |-- POST /worker/submit ->|  "Done. Hash: abc123, Token: xyz"
    |<-- {accepted: true} -----|  Token verified, task → COMPLETED
"""

from __future__ import annotations

import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from ..security import verify_api_key
from ..task_manager import TaskState
from .session import SessionManager

log = logging.getLogger(__name__)

# ── Rate limiting ──────────────────────────────────────────────
# Per-worker_id sliding window. Every worker must authenticate
# with X-API-Key header. No anonymous access.
_RATE_WINDOW: dict[str, list[float]] = {}
_RATE_AUTH_LIMIT = 30  # pulls/min per worker
_RATE_WINDOW_SEC = 60


def _check_pull_rate(key: str, max_per_minute: int) -> bool:
    """Return True if this key is under the rate limit."""
    now = time.time()
    window = _RATE_WINDOW.get(key, [])
    cutoff = now - _RATE_WINDOW_SEC
    window = [t for t in window if t > cutoff]
    if window:
        _RATE_WINDOW[key] = window
    else:
        _RATE_WINDOW.pop(key, None)  # Cleanup stale entries
    if len(window) >= max_per_minute:
        return False
    window.append(now)
    return True


# ── Matching logic (stateless, same rules as SchedulingPolicy) ──


# ── Endpoints ──────────────────────────────────────────────────


async def pull(request: Request, sessions: SessionManager):
    """Worker pulls for a task. Stateless — no WebSocket needed.

    Body:
      { "type": "pull",
        "worker_id": "kaggle-a1b2c3d4",
        "capabilities": { "runtimes": ["wasm","python"], "resources": {...} } }

    Returns:
      { "type": "pull_response",
        "task": { "id": "...", "runtime": "...", "assignment_token": "...",
                  "payload": {"executable_hash": "...", "input_hash": "..."} }
      }
      — or {"task": None} if nothing queued that matches.
    """
    body = await request.json()

    if body.get("type") != "pull":
        return JSONResponse(
            {"type": "error", "code": "INVALID_MESSAGE", "message": "Expected type=pull"},
            status_code=400,
        )

    client_ip = request.client.host if request.client else "unknown"
    authenticated = verify_api_key(request)

    if not authenticated:
        return JSONResponse(
            {
                "type": "error",
                "code": "UNAUTHORIZED",
                "message": "API key required — add X-API-Key header",
            },
            status_code=401,
        )

    worker_id = body.get("worker_id", f"auth-{client_ip}")
    rate_key = worker_id
    rate_max = _RATE_AUTH_LIMIT

    if not _check_pull_rate(rate_key, rate_max):
        return JSONResponse(
            {
                "type": "error",
                "code": "RATE_LIMITED",
                "message": f"Max {rate_max} pulls/min",
            },
            status_code=429,
        )
    capabilities = body.get("capabilities", {})

    # Save any logs the worker sent along with the pull (heartbeat-style)
    worker_logs = body.get("logs", "")
    if worker_logs:
        await _save_worker_logs(worker_id, worker_logs, prefix="pull")

    # Track Mode B liveness so the harvester knows this worker is alive
    if sessions:
        sessions.touch_mode_b(worker_id)

    task_service = getattr(request.app.state, "task_service", None)
    if not task_service:
        return JSONResponse({"type": "pull_response", "task": None})

    # Walk queued tasks (FIFO) and try to assign the first compatible one.
    queued = await task_service.get_queued(limit=100)
    for task in queued:
        from ..domain import _match_capabilities  # lazy (circular import avoidance)

        if not _match_capabilities(task, capabilities):
            continue

        success, token = await task_service.assign(task.id, worker_id)
        if not success:
            continue  # Raced with another worker — try next task

        log.info(
            "mode-b pull: assigned %s → %s",
            task.id[:12],
            worker_id[:16],
        )
        return JSONResponse(
            {
                "type": "pull_response",
                "task": {
                    "id": task.id,
                    "runtime": task.runtime,
                    "client_id": task.client_id,
                    "assignment_token": token,
                    "deadline_ms": task.deadline_ms,
                    "gpu_required": task.gpu_required,
                    "payload": {
                        "executable_hash": task.executable_hash,
                        "input_hash": task.input_hash,
                    },
                },
            }
        )

    return JSONResponse({"type": "pull_response", "task": None})


async def submit(request: Request, sessions: SessionManager):
    """Worker submits a result after completing a pulled task.

    Body:
      { "type": "submit",
        "task_id": "abc123...",
        "assignment_token": "def456...",
        "result": { "output_hash": "sha256hex...",
                    "execution_metadata": {"exit_code": 0, "stderr": ""} } }

    The output blob MUST already be uploaded via PUT /blobs before calling
    this endpoint.  This endpoint only verifies the token and marks the
    task COMPLETED.
    """
    body = await request.json()

    if body.get("type") != "submit":
        return JSONResponse(
            {"type": "error", "code": "INVALID_MESSAGE", "message": "Expected type=submit"},
            status_code=400,
        )

    task_id = body.get("task_id", "")
    assignment_token = body.get("assignment_token", "")
    output_hash = body.get("result", {}).get("output_hash", "")

    if not task_id or not output_hash:
        return JSONResponse(
            {
                "type": "submit_ack",
                "task_id": task_id,
                "accepted": False,
                "reason": "missing task_id or output_hash",
            },
            status_code=400,
        )

    task_service = getattr(request.app.state, "task_service", None)
    if not task_service:
        return JSONResponse(
            {"type": "submit_ack", "task_id": task_id, "accepted": False},
            status_code=500,
        )

    # Extract result metadata
    status = body.get("result", {}).get("execution_metadata", {}).get("exit_code", 0)
    stderr_info = body.get("result", {}).get("execution_metadata", {}).get("stderr", "")

    # Always save worker stderr for debugging (both success and failure)
    if stderr_info:
        await _save_worker_logs(task_id, stderr_info)

    # Reject empty/error results — mark TIMEOUT so they requeue
    if not output_hash or status != 0:
        log.warning(
            "mode-b submit: empty/error result, marking TIMEOUT: task=%s exit_code=%s",
            task_id[:12],
            status,
        )
        await task_service._tm.transition(task_id, TaskState.TIMEOUT)
        return JSONResponse(
            {
                "type": "submit_ack",
                "task_id": task_id,
                "accepted": False,
                "reason": "error_result_will_retry",
            }
        )

    success = await task_service.complete(task_id, output_hash, assignment_token)
    if success:
        log.info("mode-b submit: completed %s hash=%s", task_id[:12], output_hash[:12])
    else:
        log.warning(
            "mode-b submit: REJECTED task=%s hash=%s token=%s...",
            task_id[:12],
            output_hash[:12],
            assignment_token[:12] if assignment_token else "none",
        )
        # Touch assigned_at — even on reject, the submit proves the worker was alive.
        # If the token was already consumed (race with requeue), this is harmless.
        try:
            await _touch_task(task_service, task_id, prefix="submit")
        except Exception:
            pass

    return JSONResponse(
        {
            "type": "submit_ack",
            "task_id": task_id,
            "accepted": success,
            "credit_earned": 1 if success else 0,
        }
    )


async def _touch_task(task_service, task_id: str, prefix: str = "") -> None:
    """Reset assigned_at on a task — any worker signal = liveness proof.

    Called by: submit (Mode B). Best-effort: exceptions are silently ignored."""
    try:
        now = str(time.time())
        await task_service._tm._db.execute(
            "UPDATE tasks SET assigned_at = ?, updated_at = ? WHERE id = ?",
            (now, now, task_id),
        )
        await task_service._tm._db.commit()
    except Exception:
        pass


async def _save_worker_logs(task_id: str, stderr: str, prefix: str = "submit"):
    """Save worker stderr to a log file for debugging."""
    import os
    from pathlib import Path

    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{task_id}.log"
    with open(log_path, "a") as f:
        import time

        f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} [{prefix}] ===\n")
        f.write(stderr)
        if not stderr.endswith("\n"):
            f.write("\n")
