"""Smoke test: end-to-end flow — submit → execute → result."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest


@pytest.mark.asyncio
async def test_full_flow_submit_execute_result(live_server):
    """Complete end-to-end: submit task → worker executes → result retrieved."""
    from scrapower.worker.client import WorkerClient

    http_url = live_server.replace("ws://", "http://")
    task_id = uuid.uuid4().hex

    # 1. Upload executable and input blobs
    exec_data = b"fake-wasm-module"
    input_data = b"input-for-task"

    async with httpx.AsyncClient(headers={"X-API-Key": "test-api-key"}) as client:
        r = await client.put(f"{http_url}/blobs", content=exec_data)
        exec_hash = r.json()["hash"]

        r = await client.put(f"{http_url}/blobs", content=input_data)
        input_hash = r.json()["hash"]

        # 2. Submit task
        r = await client.post(
            f"{http_url}/tasks",
            json={
                "task_id": task_id,
                "client_id": "test",
                "runtime": "wasm",
                "executable_hash": exec_hash,
                "input_hash": input_hash,
            },
        )
        assert r.status_code == 200

    # 3. Connect worker — it will download blobs, execute, upload result
    worker = WorkerClient(f"{live_server}/worker/ws", worker_id="smoke-test")
    await worker.connect()

    # Worker receives task_assign
    msg = await asyncio.wait_for(worker._ws.receive_json(), timeout=10)
    assert msg["type"] == "task_assign"

    # Send task_accept
    await worker._ws.send_json(
        {
            "type": "task_accept",
            "session_id": worker.session_id,
            "task_id": task_id,
            "assignment_token": msg["task"]["assignment_token"],
        }
    )

    # Worker executes and submits result
    await worker._execute(msg["task"])

    # 4. Check task is validated
    async with httpx.AsyncClient(headers={"X-API-Key": "test-api-key"}) as client:
        r = await client.get(f"{http_url}/tasks/{task_id}")
        assert r.json()["status"] == "validated"

        # 5. Retrieve result
        r = await client.get(f"{http_url}/results/{task_id}")
        assert r.status_code == 200
        assert len(r.content) > 0  # got output blob

    await worker.disconnect()
