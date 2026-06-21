"""HTTP handler for Worker Protocol Mode B.

Stateless pull/submit cycle for ephemeral workers (Kaggle, Lambda, Cloud Run).
This is now the PRIMARY task dispatch protocol. WebSocket Mode A is retained
for browser workers and challenge double-execution.

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

from ..task_manager import TaskState
from .session import SessionManager

log = logging.getLogger(__name__)

# ── Rate limiting ──────────────────────────────────────────────
# Per-IP sliding window: max N pulls per minute (configurable via
# pull_rate_limit_per_ip in config.toml / Config dataclass).
_RATE_WINDOW: dict[str, list[float]] = {}
_RATE_LIMIT = 12  # pulls per minute per IP (default, overridden at startup)
_RATE_WINDOW_SEC = 60


def configure_rate_limit(max_per_minute: int = 12) -> None:
    """Set the global pull rate limit (called from coordinator lifespan)."""
    global _RATE_LIMIT
    _RATE_LIMIT = max_per_minute


def _check_pull_rate(ip: str) -> bool:
    """Return True if this IP is under the rate limit."""
    now = time.time()
    window = _RATE_WINDOW.get(ip, [])
    # Prune expired entries
    cutoff = now - _RATE_WINDOW_SEC
    window = [t for t in window if t > cutoff]
    _RATE_WINDOW[ip] = window
    # Purge stale IPs from the dict (lazy, every 100 checks)
    if len(_RATE_WINDOW) > 1000:
        _RATE_WINDOW.clear()
    if len(window) >= _RATE_LIMIT:
        return False
    window.append(now)
    return True


# ── Matching logic (stateless, same rules as SchedulingPolicy) ──


def _is_compatible(task, capabilities: dict) -> bool:
    """Check if a worker's capabilities can execute this task."""
    runtimes = capabilities.get("runtimes", ["wasm"])
    if task.runtime not in runtimes:
        return False

    resources = capabilities.get("resources", {})
    if resources.get("ram_mb", 0) < 128:
        return False

    if task.gpu_required and not resources.get("gpu", {}).get("supported", False):
        return False

    lifecycle = capabilities.get("lifecycle", {})
    remaining = lifecycle.get("expected_remaining_sec")
    if remaining and remaining < task.deadline_ms / 1000:
        return False

    return True


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
    if not _check_pull_rate(client_ip):
        return JSONResponse(
            {
                "type": "error",
                "code": "RATE_LIMITED",
                "message": f"Max {_RATE_LIMIT} pulls/min per IP",
            },
            status_code=429,
        )

    worker_id = body.get("worker_id", f"anon-{client_ip}")
    capabilities = body.get("capabilities", {})

    task_service = getattr(request.app.state, "task_service", None)
    if not task_service:
        return JSONResponse({"type": "pull_response", "task": None})

    # Walk queued tasks (FIFO) and try to assign the first compatible one.
    queued = await task_service.get_queued(limit=100)
    for task in queued:
        if not _is_compatible(task, capabilities):
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

    # Check if this task is part of a challenge (double-execution verification).
    # resolve_challenge returns True if: no challenge exists, OR challenge matched.
    try:
        resolved = await task_service._tm.resolve_challenge(
            task_id,
            assignment_token,
            output_hash,
        )
    except Exception:
        log.exception("challenge resolution failed for %s", task_id[:12])
        resolved = True  # Fallback: complete normally

    if not resolved:
        # Challenge still pending — second worker hasn't responded yet.
        return JSONResponse(
            {
                "type": "submit_ack",
                "task_id": task_id,
                "accepted": False,
                "reason": "challenge_pending",
            }
        )

    # Reject empty/error results — mark TIMEOUT so they requeue (same as WS handler)
    status = body.get("result", {}).get("execution_metadata", {}).get("exit_code", 0)
    if not output_hash or status != 0:
        log.warning(
            "mode-b submit: empty/error result, marking TIMEOUT: task=%s",
            task_id[:12],
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

    return JSONResponse(
        {
            "type": "submit_ack",
            "task_id": task_id,
            "accepted": success,
            "credit_earned": 1 if success else 0,
        }
    )
