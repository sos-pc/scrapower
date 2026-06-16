"""Tests for Worker Gateway — Mode A (WebSocket)."""

from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


async def _connect_worker(live_server: str, worker_id: str = "test-worker-1") -> dict:
    ws = await connect(f"{live_server}/worker/ws")
    await ws.send(
        json.dumps(
            {
                "type": "hello",
                "version": "2.1",
                "mode": "persistent",
                "worker_id": worker_id,
                "auth": {"method": "none"},
            }
        )
    )
    session_msg = json.loads(await ws.recv())
    assert session_msg["type"] == "session"
    session_id = session_msg["session_id"]

    await ws.send(
        json.dumps(
            {
                "type": "capabilities",
                "session_id": session_id,
                "payload": {
                    "runtimes": ["wasm"],
                    "resources": {
                        "cpu_cores": 4,
                        "ram_mb": 8192,
                        "disk_mb": 51200,
                        "gpu": {"supported": False},
                    },
                    "lifecycle": {
                        "mode": "persistent",
                        "max_lifetime_sec": None,
                        "expected_remaining_sec": None,
                        "idle_timeout_sec": None,
                    },
                    "verification": {"can_challenge": False, "challenge_timeout_max_sec": 0},
                    "network": {"connectivity": "outgoing_only"},
                    "limits": {"max_task_duration_ms": 60000, "max_concurrent_tasks": 2},
                },
            }
        )
    )
    return {"ws": ws, "session_id": session_id, "worker_id": worker_id}


def _hb(session_id: str) -> str:
    return json.dumps(
        {
            "type": "heartbeat",
            "session_id": session_id,
            "current_load_pct": 0.0,
            "tasks_in_progress": 0,
            "uptime_sec": 0,
            "expected_remaining_sec": None,
        }
    )


@pytest.mark.asyncio
async def test_hello_session_handshake(live_server):
    w = await _connect_worker(live_server)
    assert w["session_id"]
    await w["ws"].close()


@pytest.mark.asyncio
async def test_heartbeat(live_server):
    w = await _connect_worker(live_server)
    await w["ws"].send(_hb(w["session_id"]))
    ack = json.loads(await w["ws"].recv())
    assert ack["type"] == "heartbeat_ack"
    await w["ws"].close()


@pytest.mark.asyncio
async def test_heartbeat_timeout_disconnects(live_server):
    w = await _connect_worker(live_server)
    for _ in range(2):
        await w["ws"].send(_hb(w["session_id"]))
        json.loads(await w["ws"].recv())
    await asyncio.sleep(3.0)
    try:
        await w["ws"].send(_hb(w["session_id"]))
        await asyncio.wait_for(w["ws"].recv(), timeout=2)
    except (TimeoutError, ConnectionClosed):
        pass


@pytest.mark.asyncio
async def test_bye_disconnect(live_server):
    w = await _connect_worker(live_server)
    await w["ws"].send(
        json.dumps(
            {
                "type": "bye",
                "session_id": w["session_id"],
                "reason": "user_disconnect",
            }
        )
    )
    try:
        await asyncio.wait_for(w["ws"].recv(), timeout=2)
    except ConnectionClosed:
        pass


@pytest.mark.asyncio
async def test_invalid_message(live_server):
    w = await _connect_worker(live_server)
    await w["ws"].send("not json")
    response = json.loads(await w["ws"].recv())
    assert response["type"] == "error"
    assert response["code"] == "INVALID_MESSAGE"
    await w["ws"].close()


@pytest.mark.asyncio
async def test_unknown_message_type(live_server):
    w = await _connect_worker(live_server)
    await w["ws"].send(
        json.dumps(
            {
                "type": "nonexistent_message_xyz",
                "session_id": w["session_id"],
            }
        )
    )
    response = json.loads(await w["ws"].recv())
    assert response["type"] == "error"
    await w["ws"].close()


@pytest.mark.asyncio
async def test_multiple_workers(live_server):
    w1 = await _connect_worker(live_server, "worker-a")
    w2 = await _connect_worker(live_server, "worker-b")
    w3 = await _connect_worker(live_server, "worker-c")
    assert w1["session_id"] != w2["session_id"]
    assert w2["session_id"] != w3["session_id"]
    assert w1["session_id"] != w3["session_id"]
    for w in [w1, w2, w3]:
        await w["ws"].close()


@pytest.mark.asyncio
async def test_auth_token(live_server):
    ws = await connect(f"{live_server}/worker/ws")
    await ws.send(
        json.dumps(
            {
                "type": "hello",
                "version": "2.1",
                "mode": "persistent",
                "worker_id": "token-worker",
                "auth": {"method": "token", "value": "test-token-123"},
            }
        )
    )
    session_msg = json.loads(await ws.recv())
    assert session_msg["type"] == "session"
    await ws.close()
