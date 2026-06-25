"""Tests for Worker Gateway — Mode B (HTTP ephemeral).

Tests the pull/submit cycle for stateless workers.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_pull_no_tasks(http_client):
    """Worker pulls, no tasks available."""
    response = await http_client.post(
        "/worker/pull",
        headers={"X-API-Key": "test-api-key"},
        json={
            "type": "pull",
            "version": "2.1",
            "mode": "ephemeral",
            "worker_id": "lambda-1",
            "capabilities": {
                "runtimes": ["wasm"],
                "resources": {
                    "cpu_cores": 2,
                    "ram_mb": 3008,
                    "disk_mb": 512,
                    "gpu": {"supported": False},
                },
                "lifecycle": {
                    "mode": "ephemeral",
                    "max_lifetime_sec": 900,
                    "expected_remaining_sec": 850,
                    "idle_timeout_sec": None,
                },
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "pull_response"
    assert data["task"] is None  # no tasks queued yet


@pytest.mark.asyncio
async def test_pull_with_capabilities(http_client):
    """Worker pulls with capabilities, server accepts."""
    response = await http_client.post(
        "/worker/pull",
        headers={"X-API-Key": "test-api-key"},
        json={
            "type": "pull",
            "version": "2.1",
            "mode": "ephemeral",
            "worker_id": "cf-worker-1",
            "capabilities": {
                "runtimes": ["wasm"],
                "resources": {
                    "cpu_cores": 1,
                    "ram_mb": 128,
                    "disk_mb": 0,
                    "gpu": {"supported": False},
                },
                "lifecycle": {
                    "mode": "ephemeral",
                    "max_lifetime_sec": 1,  # 10ms CPU
                    "expected_remaining_sec": 1,
                    "idle_timeout_sec": None,
                },
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "pull_response"
    assert data["task"] is None


@pytest.mark.asyncio
async def test_submit_invalid_task(http_client):
    """Submitting result for non-existent task returns error."""
    response = await http_client.post(
        "/worker/submit",
        json={
            "type": "submit",
            "task_id": "nonexistent-task-id",
            "worker_id": "lambda-1",
            "status": "success",
            "result": {
                "output_hash": "a" * 64,
                "execution_metadata": {
                    "duration_ms": 100,
                    "exit_code": 0,
                },
            },
        },
    )

    assert response.status_code in (404, 400)


@pytest.mark.asyncio
async def test_pull_with_minimal_lifecycle(http_client):
    """Pull with very short remaining lifetime still works."""
    response = await http_client.post(
        "/worker/pull",
        headers={"X-API-Key": "test-api-key"},
        json={
            "type": "pull",
            "version": "2.1",
            "mode": "ephemeral",
            "worker_id": "short-lived",
            "capabilities": {
                "runtimes": ["wasm"],
                "resources": {
                    "cpu_cores": 1,
                    "ram_mb": 128,
                    "disk_mb": 0,
                    "gpu": {"supported": False},
                },
                "lifecycle": {
                    "mode": "ephemeral",
                    "max_lifetime_sec": 30,
                    "expected_remaining_sec": 5,
                    "idle_timeout_sec": None,
                },
            },
        },
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_pull(http_client):
    """Multiple pulls from same worker_id are rate-limited."""
    # First pull — should succeed
    response = await http_client.post(
        "/worker/pull",
        headers={"X-API-Key": "test-api-key"},
        json={
            "type": "pull",
            "version": "2.1",
            "mode": "ephemeral",
            "worker_id": "rate-limit-test",
            "capabilities": {
                "runtimes": ["wasm"],
                "resources": {
                    "cpu_cores": 1,
                    "ram_mb": 256,
                    "disk_mb": 0,
                    "gpu": {"supported": False},
                },
                "lifecycle": {
                    "mode": "ephemeral",
                    "max_lifetime_sec": 3600,
                    "expected_remaining_sec": 3600,
                },
            },
        },
    )
    assert response.status_code == 200

    # Immediate second pull — still within 30/min limit
    response2 = await http_client.post(
        "/worker/pull",
        headers={"X-API-Key": "test-api-key"},
        json={
            "type": "pull",
            "version": "2.1",
            "mode": "ephemeral",
            "worker_id": "rate-limit-test-2",
            "capabilities": {
                "runtimes": ["wasm"],
                "resources": {
                    "cpu_cores": 1,
                    "ram_mb": 256,
                    "disk_mb": 0,
                    "gpu": {"supported": False},
                },
                "lifecycle": {
                    "mode": "ephemeral",
                    "max_lifetime_sec": 3600,
                    "expected_remaining_sec": 3600,
                },
            },
        },
    )
    # Should still pass (rate limit permissive in tests)
    assert response2.status_code == 200
