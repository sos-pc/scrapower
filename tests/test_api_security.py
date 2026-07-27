"""Auth and input-validation on the publicly reachable endpoints.

Regression cover for two real holes:
  - /worker/submit and /worker/heartbeat accepted unauthenticated calls,
  - PUT /blobs buffered the whole body before checking auth or size.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scrapower.coordinator.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A booted app on a throwaway data dir (lifespan runs: db, seed, reconcile)."""
    monkeypatch.setenv("SCRAPOWER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCRAPOWER_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("SCRAPOWER_MAX_BLOB_SIZE_MB", "1")
    monkeypatch.chdir(tmp_path)  # data/logs and data/transcripts land here
    with TestClient(app) as c:
        yield c


# ── Worker endpoints require the API key ───────────────────────────────────


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/worker/pull", {"type": "pull", "worker_id": "w", "capabilities": {}}),
        ("/worker/submit", {"type": "submit", "task_id": "x", "result": {"output_hash": "y"}}),
        ("/worker/heartbeat", {"type": "heartbeat", "worker_id": "w"}),
    ],
)
def test_worker_endpoints_reject_anonymous(client, path, payload):
    assert client.post(path, json=payload).status_code == 401


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/worker/pull", {"type": "pull", "worker_id": "w", "capabilities": {}}),
        ("/worker/submit", {"type": "submit", "task_id": "x", "result": {"output_hash": "y"}}),
        ("/worker/heartbeat", {"type": "heartbeat", "worker_id": "w"}),
    ],
)
def test_worker_endpoints_reject_wrong_key(client, path, payload):
    assert client.post(path, json=payload, headers={"X-API-Key": "nope"}).status_code == 401


def test_authenticated_worker_endpoints_pass_auth(client, auth_headers):
    """With a valid key the request reaches the handler (no 401)."""
    assert client.post(
        "/worker/heartbeat", json={"type": "heartbeat", "worker_id": "w"}, headers=auth_headers
    ).status_code == 200
    assert client.post(
        "/worker/pull",
        json={"type": "pull", "worker_id": "w", "capabilities": {}},
        headers=auth_headers,
    ).status_code == 200


# ── PUT /blobs: auth and size are checked before the body is buffered ──────


def test_blob_upload_rejects_anonymous(client):
    assert client.put("/blobs", content=b"x" * 1000).status_code == 401


def test_blob_upload_rejects_oversized_anonymous_without_buffering(client):
    """2MB over a 1MB cap, unauthenticated: refused on auth, never buffered."""
    assert client.put("/blobs", content=b"x" * (2 * 1024 * 1024)).status_code == 401


def test_blob_upload_rejects_invalid_assignment_token(client):
    assert client.put("/blobs?assignment_token=deadbeef", content=b"x").status_code == 401


def test_blob_upload_enforces_size_cap(client, auth_headers):
    assert client.put(
        "/blobs", content=b"x" * (2 * 1024 * 1024), headers=auth_headers
    ).status_code == 413


def test_blob_upload_enforces_cap_without_content_length(client, auth_headers):
    """Chunked upload (no Content-Length) must still be capped mid-stream."""

    def chunks():
        for _ in range(40):
            yield b"y" * (64 * 1024)  # ~2.5MB total

    assert client.put("/blobs", content=chunks(), headers=auth_headers).status_code == 413


def test_blob_round_trip_for_authenticated_client(client, auth_headers):
    payload = b"legitimate worker output"
    r = client.put("/blobs", content=payload, headers=auth_headers)
    assert r.status_code == 200
    digest = r.json()["hash"]
    assert len(digest) == 64
    assert client.get(f"/blobs/{digest}").content == payload


def test_blob_download_rejects_malformed_hash(client):
    assert client.get("/blobs/not-a-hash").status_code == 400


# ── Worker log files: the id becomes a filename ────────────────────────────


async def test_worker_logs_refuse_path_traversal(tmp_path, monkeypatch):
    """task_id/worker_id come from the request body and end up in a log path."""
    from scrapower.coordinator.worker_gateway import http_handler

    monkeypatch.chdir(tmp_path)
    escaped = tmp_path.parent / "pwned.log"

    await http_handler._save_worker_logs("../pwned", "malicious", prefix="x")
    assert not escaped.exists(), "traversal must not write outside data/logs"

    await http_handler._save_worker_logs("a1b2c3d4", "ok", prefix="x")
    assert (tmp_path / "data" / "logs" / "a1b2c3d4.log").exists(), "valid ids still log"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "x" * 200, "", "with space"])
async def test_worker_logs_reject_unsafe_ids(tmp_path, monkeypatch, bad):
    from scrapower.coordinator.worker_gateway import http_handler

    monkeypatch.chdir(tmp_path)
    await http_handler._save_worker_logs(bad, "content", prefix="x")
    logs = tmp_path / "data" / "logs"
    written = list(logs.iterdir()) if logs.exists() else []
    assert written == [], f"{bad!r} should not produce a log file"
