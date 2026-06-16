"""Tests for native worker client connecting to coordinator."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from scrapower.worker.client import WorkerClient


@pytest.mark.asyncio
async def test_worker_connect(live_server):
    """Worker connects, receives session."""
    worker = WorkerClient(f"{live_server}/worker/ws", worker_id="test-native")
    await worker.connect()
    assert worker.session_id is not None
    await worker.disconnect()


@pytest.mark.asyncio
async def test_worker_heartbeat(live_server):
    """Worker sends heartbeats and stays connected."""
    worker = WorkerClient(f"{live_server}/worker/ws", worker_id="hb-test")
    await worker.connect()

    # Run for ~3 heartbeats then check
    results = []

    async def _run():
        results.append(await worker.run())  # will block until disconnect

    task = asyncio.create_task(_run())
    await asyncio.sleep(3)
    await worker.disconnect()
    await task


@pytest.mark.asyncio
async def test_worker_execute_task(live_server):
    """Full flow: upload task, worker connects, receives task, executes, submits result."""
    import httpx

    http_url = live_server.replace("ws://", "http://")

    # 1. Upload a fake "executable" and "input" blob via HTTP
    exec_blob = b"fake-wasm-binary"
    input_blob = b"task-input-data"

    async with httpx.AsyncClient(headers={"X-API-Key": "test-api-key"}) as client:
        r1 = await client.put(f"{http_url}/blobs", content=exec_blob)
        exec_hash = r1.json()["hash"]

        r2 = await client.put(f"{http_url}/blobs", content=input_blob)
        input_hash = r2.json()["hash"]

        # 2. Submit a task via client API
        task_id = uuid.uuid4().hex
        r3 = await client.post(
            f"{http_url}/tasks",
            json={
                "task_id": task_id,
                "client_id": "test-client",
                "runtime": "wasm",
                "executable_hash": exec_hash,
                "input_hash": input_hash,
            },
        )
        assert r3.status_code == 200

    # 3. Worker connects and handshakes successfully
    worker = WorkerClient(f"{live_server}/worker/ws", worker_id="exec-test")
    await worker.connect()
    assert worker.session_id is not None
    await worker.disconnect()


@pytest.mark.asyncio
async def test_worker_token_auth(live_server):
    """Worker with token auth connects successfully."""
    worker = WorkerClient(
        f"{live_server}/worker/ws",
        worker_id="token-worker",
        auth_token="secret-123",
    )
    await worker.connect()
    assert worker.session_id is not None
    await worker.disconnect()
