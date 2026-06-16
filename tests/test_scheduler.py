from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest


async def _create_task(
    http_url: str, task_id: str, runtime: str = "wasm", client_id: str = "alice"
) -> None:
    async with httpx.AsyncClient(headers={"X-API-Key": "test-api-key"}) as client:
        r = await client.post(
            f"{http_url}/tasks",
            json={
                "task_id": task_id,
                "client_id": client_id,
                "runtime": runtime,
                "executable_hash": "abc",
                "input_hash": "def",
            },
        )
        assert r.status_code == 200


async def _cancel_task(http_url: str, task_id: str) -> None:
    async with httpx.AsyncClient(headers={"X-API-Key": "test-api-key"}) as client:
        await client.delete(f"{http_url}/tasks/{task_id}")


@pytest.mark.asyncio
async def test_create_task_and_get_status(live_server):
    """Create a task and check its status."""
    http_url = live_server.replace("ws://", "http://")
    task_id = uuid.uuid4().hex

    await _create_task(http_url, task_id)

    async with httpx.AsyncClient(headers={"X-API-Key": "test-api-key"}) as client:
        r2 = await client.get(f"{http_url}/tasks/{task_id}")
        assert r2.status_code == 200
        assert r2.json()["status"] == "queued"

    await _cancel_task(http_url, task_id)


@pytest.mark.asyncio
async def test_scheduler_assigns_task_to_worker(live_server):
    """Worker connects → scheduler assigns a queued task → worker receives task_assign."""
    from scrapower.worker.client import WorkerClient

    http_url = live_server.replace("ws://", "http://")
    task_id = uuid.uuid4().hex

    # Connect worker BEFORE creating task to avoid race with other workers
    worker = WorkerClient(f"{live_server}/worker/ws", worker_id="sched-test")
    await worker.connect()

    await _create_task(http_url, task_id)

    msg = await asyncio.wait_for(worker._ws.receive_json(), timeout=10)
    assert msg["type"] == "task_assign"
    assert msg["task"]["id"] == task_id
    assert "assignment_token" in msg["task"]

    await worker.disconnect()
    await _cancel_task(http_url, task_id)


@pytest.mark.asyncio
async def test_scheduler_skips_incompatible_runtime(live_server):
    """Worker with wasm-only runtime shouldn't get a python task."""
    from scrapower.worker.client import WorkerClient

    http_url = live_server.replace("ws://", "http://")
    task_id = uuid.uuid4().hex

    # Connect worker BEFORE creating task to avoid race with other workers
    worker = WorkerClient(f"{live_server}/worker/ws", worker_id="wasm-only", runtimes=["wasm"])
    await worker.connect()

    await _create_task(http_url, task_id, runtime="python")

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(worker._ws.receive_json(), timeout=3)

    await worker.disconnect()
    await _cancel_task(http_url, task_id)


@pytest.mark.asyncio
async def test_concurrent_assignment_one_winner(live_server):
    """Two workers, one task — only one gets it."""
    from scrapower.worker.client import WorkerClient

    http_url = live_server.replace("ws://", "http://")
    task_id = uuid.uuid4().hex

    # Connect workers BEFORE creating task to avoid race with other workers
    w1 = WorkerClient(f"{live_server}/worker/ws", worker_id="w1")
    w2 = WorkerClient(f"{live_server}/worker/ws", worker_id="w2")
    await w1.connect()
    await w2.connect()

    await _create_task(http_url, task_id)

    got_task = 0

    async def wait_for_task(w):
        nonlocal got_task
        try:
            msg = await asyncio.wait_for(w._ws.receive_json(), timeout=10)
            if msg["type"] == "task_assign":
                got_task += 1
        except TimeoutError:
            pass

    await asyncio.gather(wait_for_task(w1), wait_for_task(w2))
    assert got_task == 1

    await w1.disconnect()
    await w2.disconnect()
    await _cancel_task(http_url, task_id)


@pytest.mark.asyncio
async def test_segregation_default_off(live_server):
    """With segregation OFF (default), worker can execute a task from same client_id."""
    from scrapower.worker.client import WorkerClient

    http_url = live_server.replace("ws://", "http://")
    task_id = uuid.uuid4().hex

    # Connect worker BEFORE creating task to avoid race with other workers
    worker = WorkerClient(f"{live_server}/worker/ws", worker_id="alice")
    await worker.connect()

    await _create_task(http_url, task_id, client_id="alice")

    msg = await asyncio.wait_for(worker._ws.receive_json(), timeout=10)
    assert msg["type"] == "task_assign"
    assert msg["task"]["client_id"] == "alice"
    assert msg["task"]["id"] == task_id

    await worker.disconnect()
    await _cancel_task(http_url, task_id)


@pytest.mark.asyncio
async def test_task_not_found(live_server):
    """GET /tasks/unknown returns 404."""
    http_url = live_server.replace("ws://", "http://")
    async with httpx.AsyncClient(headers={"X-API-Key": "test-api-key"}) as client:
        r = await client.get(f"{http_url}/tasks/nonexistent")
        assert r.status_code == 404
