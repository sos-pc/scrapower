"""Worker Protocol v2.1 — typed message definitions for Mode A (WebSocket).

Used by: scheduler (WS push), ws_handler (WS messages),
embedded worker (local WASM).

NOT used by Mode B (HTTP pull/submit) — that protocol uses
raw JSON dicts in worker_gateway/http_handler.py because
HTTP requests are stateless and don't need typed sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ──────────────────────────────────────────────────────────────
# Worker → Coordinator
# ──────────────────────────────────────────────────────────────


@dataclass
class Hello:
    """Worker introduces itself after connecting."""

    type: Literal["hello"] = "hello"
    version: str = "2.1"
    mode: Literal["persistent", "ephemeral"] = "persistent"
    worker_id: str = ""
    auth: dict = field(default_factory=lambda: {"method": "none"})


@dataclass
class Capabilities:
    """Worker declares its capabilities."""

    type: Literal["capabilities"] = "capabilities"
    session_id: str = ""
    payload: dict = field(default_factory=dict)


@dataclass
class TaskAccept:
    """Worker accepts an assigned task."""

    type: Literal["task_accept"] = "task_accept"
    session_id: str = ""
    task_id: str = ""
    assignment_token: str = ""


@dataclass
class TaskResult:
    """Worker submits a task result."""

    type: Literal["task_result"] = "task_result"
    session_id: str = ""
    task_id: str = ""
    assignment_token: str = ""
    status: Literal["success", "error"] = "success"
    result: dict = field(default_factory=dict)
    verification_data: dict | None = None


@dataclass
class Heartbeat:
    """Worker sends periodic heartbeat."""

    type: Literal["heartbeat"] = "heartbeat"
    session_id: str = ""
    current_load_pct: float = 0.0
    tasks_in_progress: int = 0
    uptime_sec: int = 0
    expected_remaining_sec: int | None = None


@dataclass
class Bye:
    """Worker disconnects gracefully."""

    type: Literal["bye"] = "bye"
    session_id: str = ""
    reason: str = "user_disconnect"


# ──────────────────────────────────────────────────────────────
# Coordinator → Worker
# ──────────────────────────────────────────────────────────────


@dataclass
class SessionCreated:
    """Response after successful hello."""

    type: Literal["session"] = "session"
    session_id: str = ""
    heartbeat_interval_ms: int = 10000
    coordinator_version: str = "0.1.0"
    config: dict = field(default_factory=dict)


@dataclass
class TaskPayload:
    """Task description embedded in TaskAssign."""

    id: str = ""
    runtime: str = "wasm"
    client_id: str = ""
    assignment_token: str = ""
    deadline_ms: int = 60000
    gpu_required: bool = False
    payload: dict = field(default_factory=dict)  # {executable_hash, input_hash}


@dataclass
class TaskAssign:
    """Coordinator assigns a task to the worker."""

    type: Literal["task_assign"] = "task_assign"
    task: TaskPayload = field(default_factory=TaskPayload)


@dataclass
class HeartbeatAck:
    """Coordinator acknowledges heartbeat."""

    type: Literal["heartbeat_ack"] = "heartbeat_ack"
    lease_renewed_until: str = ""


@dataclass
class ErrorMessage:
    """Coordinator reports an error."""

    type: Literal["error"] = "error"
    code: str = ""
    message: str = ""


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

# Map message type string → dataclass for deserialization
_INCOMING: dict[str, Any] = {
    "hello": Hello,
    "capabilities": Capabilities,
    "task_accept": TaskAccept,
    "task_result": TaskResult,
    "heartbeat": Heartbeat,
    "bye": Bye,
}

_OUTGOING: dict[str, Any] = {
    "session": SessionCreated,
    "task_assign": TaskAssign,
    "heartbeat_ack": HeartbeatAck,
    "error": ErrorMessage,
}


def parse_message(data: dict) -> object:
    """Parse a raw JSON dict into a typed protocol message."""
    msg_type = data.get("type", "")
    cls = _INCOMING.get(msg_type)
    if cls is None:
        raise ValueError(f"Unknown message type: {msg_type}")
    # Only pass fields the dataclass knows about
    known = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in known}
    return cls(**filtered)


def to_dict(msg: object) -> dict:
    """Serialize a protocol message to a JSON-compatible dict."""
    result = {}
    for k, v in msg.__dict__.items():
        if k.startswith("_"):
            continue
        if hasattr(v, "__dataclass_fields__"):
            result[k] = to_dict(v)
        else:
            result[k] = v
    return result
