"""WebSocket handler for Worker Protocol Mode A.

Handles hello, capabilities, heartbeat, bye, and error responses.
Uses typed protocol messages from coordinator.protocol.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

from fastapi import WebSocket, WebSocketDisconnect

from ..protocol import (
    ErrorMessage,
    HeartbeatAck,
    SessionCreated,
    to_dict,
)
from ..task_manager import TaskState
from .session import SessionManager

log = logging.getLogger(__name__)


async def handle_ws(
    ws: WebSocket,
    sessions: SessionManager,
    task_service=None,
):
    """Handle a single WebSocket connection from a worker."""
    await ws.accept()
    client_ip = ws.client.host if ws.client else "unknown"
    session = None

    try:
        while True:
            raw = await ws.receive_text()
            # Limit message size (JSON bomb protection)
            if len(raw) > 65536:
                await ws.send_json(
                    {
                        "type": "error",
                        "code": "MESSAGE_TOO_LARGE",
                        "message": "Max 64KB per message",
                    }
                )
                continue
            msg = _parse_json(raw)

            if msg is None:
                await ws.send_json(
                    to_dict(
                        ErrorMessage(
                            code="INVALID_MESSAGE",
                            message="Message must be valid JSON",
                        )
                    )
                )
                continue

            msg_type = msg.get("type", "")
            session_id = msg.get("session_id", "")

            # P2P signalling relay: forward to target worker (same IP only)
            if msg_type.startswith("p2p_"):
                target_id = msg.get("to", "")
                if target_id:
                    target_session = None
                    for s in sessions.active_sessions:
                        if s.worker_id == target_id and s.ws:
                            target_session = s
                            break
                    if target_session and target_session.peer_ip == client_ip:
                        try:
                            await target_session.ws.send_json(msg)
                        except Exception:
                            pass
                continue

            # DHT peer list request (auth_level >= 1 only)
            if msg_type == "dht_peer_list":
                if not session or session.auth_level < 1:
                    await ws.send_json(
                        {
                            "type": "error",
                            "code": "UNAUTHORIZED",
                            "message": "Auth required for peer list",
                        }
                    )
                    continue
                peers = [s.worker_id for s in sessions.active_sessions]
                await ws.send_json(
                    {
                        "type": "dht_peer_list_response",
                        "requestId": msg.get("requestId", ""),
                        "peers": peers,
                    }
                )
                continue

            # DHT find blob
            if msg_type == "dht_find_blob":
                await ws.send_json(
                    {
                        "type": "dht_find_blob_response",
                        "requestId": msg.get("requestId", ""),
                        "peers": [],
                    }
                )
                continue

            if msg_type == "hello":
                # Rate limit: max 5 workers per IP (embedded exempt)
                same_ip = sum(1 for s in sessions.active_sessions if s.peer_ip == client_ip)
                if same_ip >= 5 and msg.get("worker_id", "") != "_embedded":
                    await ws.send_json(
                        {
                            "type": "error",
                            "code": "TOO_MANY_WORKERS",
                            "message": "Max 5 workers per IP",
                        }
                    )
                    await ws.close()
                    return

                session = sessions.create(
                    msg.get("worker_id", "unknown"),
                    ws=ws,
                    peer_ip=client_ip,
                    auth_level=_auth_level(msg),
                )

                log.info(
                    "worker connected: %s (session=%s auth=%d)",
                    session.worker_id,
                    session.session_id[:8],
                    session.auth_level,
                )
                await ws.send_json(
                    to_dict(
                        SessionCreated(
                            session_id=session.session_id,
                            heartbeat_interval_ms=sessions._heartbeat_interval * 1000,
                            coordinator_version="0.1.0",
                            config={"max_task_queue": 2, "keepalive_enabled": True},
                        )
                    )
                )

            elif msg_type == "capabilities":
                if not session or session_id != session.session_id:
                    await ws.send_json(
                        to_dict(
                            ErrorMessage(
                                code="SESSION_EXPIRED",
                                message="Invalid session",
                            )
                        )
                    )
                    continue
                sessions.set_capabilities(session_id, msg.get("payload", {}))

            elif msg_type == "task_accept":
                if session:
                    session.tasks_in_progress += 1
                # Touch assigned_at — worker confirms it took the task
                if task_service:
                    await _touch_task(task_service, msg["task_id"], prefix="ws-accept")

            elif msg_type == "task_result":
                if session:
                    session.tasks_in_progress = max(0, session.tasks_in_progress - 1)
                    if task_service:
                        output_hash = msg.get("result", {}).get("output_hash", "")
                        token = msg.get("assignment_token")
                        status = msg.get("status", "success")
                        # Reject if no token (prevents result spoofing)
                        if not token:
                            await ws.send_json(
                                to_dict(
                                    ErrorMessage(
                                        code="MISSING_TOKEN",
                                        message="assignment_token required",
                                    )
                                )
                            )
                            continue
                        # Reject empty/error results — mark TIMEOUT so they requeue
                        if not output_hash or status == "error":
                            log.warning(
                                "worker returned empty/error result, marking TIMEOUT: task=%s worker=%s",
                                msg["task_id"][:12],
                                session.worker_id[:16],
                            )
                            await task_service._tm.transition(
                                msg["task_id"],
                                TaskState.TIMEOUT,
                            )
                            continue
                        # Check if this is a challenged task
                        resolved = await task_service._tm.resolve_challenge(
                            msg["task_id"], token, output_hash
                        )

                        # Touch assigned_at — result submission is a liveness signal
                        await _touch_task(task_service, msg["task_id"], prefix="ws-result")

                        # Persist stderr logs (parity with Mode B submit)
                        stderr = (
                            msg.get("result", {}).get("execution_metadata", {}).get("stderr", "")
                        )
                        if stderr:
                            await _save_worker_logs_ws(msg["task_id"], stderr, prefix="ws-result")

                        if resolved:
                            await task_service.complete(msg["task_id"], output_hash, token)

            elif msg_type == "heartbeat":
                # Rate limit: max 1 heartbeat per 2 seconds
                now_hb = time.time()
                if session and hasattr(session, "_last_hb") and now_hb - session._last_hb < 2:
                    continue  # Silently drop excessive heartbeats
                if session:
                    session._last_hb = now_hb
                if not session or not sessions.heartbeat(session_id):
                    await ws.send_json(
                        to_dict(
                            ErrorMessage(
                                code="SESSION_EXPIRED",
                                message="Session expired",
                            )
                        )
                    )
                    continue
                session.tasks_in_progress = msg.get("tasks_in_progress", 0)

                # Touch assigned_at on all tasks owned by this worker
                if task_service and session.worker_id:
                    await _touch_worker_tasks(task_service, session.worker_id, prefix="ws-hb")

                # Persist worker logs (parity with Mode B heartbeat)
                worker_logs = msg.get("logs", "")
                if worker_logs:
                    await _save_worker_logs_ws(session.worker_id, worker_logs, prefix="ws-hb")

                await ws.send_json(
                    to_dict(
                        HeartbeatAck(
                            lease_renewed_until=datetime.now(UTC).isoformat(),
                        )
                    )
                )

            elif msg_type == "bye":
                if session:
                    sessions.remove(session.session_id)
                await ws.close()
                return

            else:
                await ws.send_json(
                    to_dict(
                        ErrorMessage(
                            code="INVALID_MESSAGE",
                            message=f"Unknown message type: {msg_type}",
                        )
                    )
                )

    except WebSocketDisconnect:
        pass
    finally:
        if session:
            sessions.remove(session.session_id)


def _parse_json(raw: str) -> dict | None:
    """Parse a JSON string. Returns None on invalid JSON."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _auth_level(msg: dict) -> int:
    """Determine auth level from hello message.

    Levels:
        0 = anonymous (method: "none") — can receive tasks, cannot access DHT
        1 = authenticated (method: "token" with valid API key) — DHT access, trusted
        2 = signed (method: "signed_nonce") — reserved for future challenge-response
    """
    auth = msg.get("auth", {})
    method = auth.get("method", "none")

    if method == "token":
        # Verify token against SCRAPOWER_API_KEY (constant-time comparison)
        import hashlib
        import hmac
        import os

        api_key = os.environ.get("SCRAPOWER_API_KEY", "")
        token = auth.get("value", "")
        if api_key and token:
            if hmac.compare_digest(
                hashlib.sha256(token.encode()).hexdigest(),
                hashlib.sha256(api_key.encode()).hexdigest(),
            ):
                return 1
        # Token missing, API key not configured, or token invalid → stay anonymous
        return 0

    if method == "signed_nonce":
        # Reserved for future challenge-response
        return 2

    return 0


# ── Liveness helpers (shared by Mode A and Mode B) ──────────────────


async def _touch_task(task_service, task_id: str, prefix: str = "") -> None:
    """Reset assigned_at on a task — any worker signal = liveness proof.

    Called by: task_accept, task_result, heartbeat (WS), submit (HTTP).
    Best-effort: exceptions are silently ignored.
    """
    try:
        now = str(time.time())
        await task_service._tm._db.execute(
            "UPDATE tasks SET assigned_at = ?, updated_at = ? WHERE id = ?",
            (now, now, task_id),
        )
        await task_service._tm._db.commit()
    except Exception:
        pass


async def _touch_worker_tasks(task_service, worker_id: str, prefix: str = "") -> None:
    """Reset assigned_at on ALL tasks assigned to this worker.

    Used by WS heartbeat — the worker doesn't send per-task IDs in
    heartbeat, so we touch every ASSIGNED task owned by this worker.
    """
    try:
        now = str(time.time())
        cursor = await task_service._tm._db.execute(
            """UPDATE tasks SET assigned_at = ?, updated_at = ?
               WHERE assigned_worker_id = ? AND state = ?""",
            (now, now, worker_id, "ASSIGNED"),
        )
        await task_service._tm._db.commit()
        if cursor.rowcount:
            log.debug(
                "ws heartbeat: touched %d tasks for worker %s",
                cursor.rowcount,
                worker_id[:16],
            )
    except Exception:
        pass


async def _save_worker_logs_ws(identifier: str, text: str, prefix: str = "ws") -> None:
    """Persist worker logs to disk — same format as Mode B _save_worker_logs.

    Uses the same data/logs/ directory so GET /tasks/{id}/logs works for
    both Mode A and Mode B workers.
    """
    from pathlib import Path as _Path

    try:
        log_dir = _Path("data/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{identifier}.log"
        import time as _time

        with open(log_path, "a") as f:
            f.write(f"=== {_time.strftime('%Y-%m-%d %H:%M:%S')} [{prefix}] ===\n")
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
        # Truncate to last 1000 lines
        from .http_handler import _truncate_log

        _truncate_log(log_path)
    except Exception:
        pass
