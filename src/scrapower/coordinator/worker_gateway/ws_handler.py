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

            elif msg_type == "task_result":
                if session:
                    session.tasks_in_progress = max(0, session.tasks_in_progress - 1)
                    if task_service:
                        output_hash = msg.get("result", {}).get("output_hash", "")
                        token = msg.get("assignment_token")
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
        # Verify token against SCRAPOWER_API_KEY
        import hashlib
        import os

        api_key = os.environ.get("SCRAPOWER_API_KEY", "")
        token = auth.get("value", "")
        if api_key and token:
            if (
                hashlib.sha256(token.encode()).hexdigest()
                == hashlib.sha256(api_key.encode()).hexdigest()
            ):
                return 1
        # Token present but invalid → stay anonymous (don't grant auth_level)
        return 0

    if method == "signed_nonce":
        # Reserved for future challenge-response
        return 2

    return 0
