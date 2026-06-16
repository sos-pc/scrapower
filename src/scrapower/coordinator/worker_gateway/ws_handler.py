"""WebSocket handler for Worker Protocol Mode A.

Handles hello, capabilities, heartbeat, bye, and error responses.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import WebSocket, WebSocketDisconnect

from .session import SessionManager

log = logging.getLogger(__name__)


async def handle_ws(
    ws: WebSocket,
    sessions: SessionManager,
    task_manager=None,
):
    """Handle a single WebSocket connection from a worker."""
    await ws.accept()
    session = None

    try:
        while True:
            raw = await ws.receive_text()
            msg = _parse_message(raw)

            if msg is None:
                await ws.send_json(
                    {
                        "type": "error",
                        "code": "INVALID_MESSAGE",
                        "message": "Message must be valid JSON",
                    }
                )
                continue

            msg_type = msg.get("type", "")
            session_id = msg.get("session_id", "")

            if msg_type == "hello":
                session = sessions.create(
                    msg.get("worker_id", "unknown"), ws=ws, auth_level=_auth_level(msg)
                )
                log.info(
                    "worker connected: %s (session=%s)",
                    session.worker_id,
                    session.session_id[:8],
                )
                await ws.send_json(
                    {
                        "type": "session",
                        "session_id": session.session_id,
                        "heartbeat_interval_ms": sessions._heartbeat_interval * 1000,
                        "coordinator_version": "0.1.0",
                        "config": {"max_task_queue": 2, "keepalive_enabled": True},
                    }
                )

            elif msg_type == "capabilities":
                if not session or session_id != session.session_id:
                    await ws.send_json(
                        {"type": "error", "code": "SESSION_EXPIRED", "message": "Invalid session"}
                    )
                    continue
                sessions.set_capabilities(session_id, msg.get("payload", {}))

            elif msg_type == "task_accept":
                if session:
                    _handle_task_accept(session, msg)

            elif msg_type == "task_result":
                if session:
                    _handle_task_result(session, msg)
                    if task_manager:
                        output_hash = msg.get("result", {}).get("output_hash", "")
                        await task_manager.complete(msg["task_id"], output_hash)

            elif msg_type == "heartbeat":
                if not session or not sessions.heartbeat(session_id):
                    await ws.send_json(
                        {"type": "error", "code": "SESSION_EXPIRED", "message": "Session expired"}
                    )
                    continue
                session.tasks_in_progress = msg.get("tasks_in_progress", 0)
                await ws.send_json(
                    {
                        "type": "heartbeat_ack",
                        "lease_renewed_until": datetime.now(UTC).isoformat(),
                    }
                )

            elif msg_type == "bye":
                if session:
                    sessions.remove(session.session_id)
                await ws.close()
                return

            else:
                await ws.send_json(
                    {
                        "type": "error",
                        "code": "INVALID_MESSAGE",
                        "message": f"Unknown message type: {msg_type}",
                    }
                )

    except WebSocketDisconnect:
        pass
    finally:
        if session:
            sessions.remove(session.session_id)


def _parse_message(raw: str) -> dict | None:
    """Parse a JSON message. Returns None on invalid JSON."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _handle_task_accept(session, msg: dict):
    """Record task accept — token verification is handled by scheduler."""
    session.tasks_in_progress = msg.get("tasks_in_progress", session.tasks_in_progress + 1)


def _handle_task_result(session, msg: dict):
    """Record task result — verification is handled by task manager."""
    session.tasks_in_progress = max(0, session.tasks_in_progress - 1)


def _auth_level(msg: dict) -> int:
    """Determine auth level from hello message."""
    auth_method = msg.get("auth", {}).get("method", "none")
    if auth_method == "token":
        return 1
    elif auth_method == "signed_nonce":
        return 2
    return 0
