"""HuggingFace Spaces worker for Scrapower.

Connects to the coordinator via WebSocket (Worker Protocol v2.1 Mode A),
declares 16 GB RAM + CPU capabilities, and executes WASM tasks via wasmtime.

Usage:
    Set env vars:
        COORDINATOR_URL  — wss://scrapower.talos-int.com/worker/ws
        SCRAPOWER_API_KEY — optional, for auth_level >= 1
        WORKER_ID         — optional, defaults to "hf-{random}"
"""

from __future__ import annotations

import asyncio
import http.server
import os
import socketserver
import sys
import threading

sys.path.insert(0, "/app")

from worker.client import WorkerClient
from worker.runtimes.wasm import WasmRuntime

COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "wss://scrapower.talos-int.com/worker/ws")
API_KEY = os.environ.get("SCRAPOWER_API_KEY", "")
WORKER_ID = os.environ.get("WORKER_ID", f"hf-{os.urandom(4).hex()}")

# ── HF Spaces capabilities (overrides the hardcoded defaults) ──
HF_CAPABILITIES = {
    "runtimes": ["wasm", "python"],
    "resources": {
        "cpu_cores": 2,
        "ram_mb": 16384,
        "disk_mb": 51200,
        "gpu": {"supported": False},
    },
    "lifecycle": {
        "mode": "persistent",
        "max_lifetime_sec": None,
        "expected_remaining_sec": None,
        "availability_profile": "always_on",
    },
    "verification": {
        "can_challenge": True,
        "challenge_timeout_max_sec": 300,
    },
    "network": {"connectivity": "outgoing_only"},
    "limits": {
        "max_task_duration_ms": 600_000,
        "max_concurrent_tasks": 2,
        "max_input_size_bytes": 100 * 1024 * 1024,
        "max_output_size_bytes": 100 * 1024 * 1024,
    },
}


# ── Health-check HTTP server (HF Spaces requires port 7860) ────
def _run_health_server(port: int = 7860):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Scrapower worker: OK\n")
            self.wfile.write(f"worker_id: {WORKER_ID}\n".encode())
            self.wfile.write(f"coordinator: {COORDINATOR_URL}\n".encode())

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


# ── Main ───────────────────────────────────────────────────────
async def main():
    print(f"[hf-worker] starting: {WORKER_ID}")
    print(f"[hf-worker] coordinator: {COORDINATOR_URL}")

    threading.Thread(target=_run_health_server, daemon=True).start()

    worker = WorkerClient(
        coordinator_url=COORDINATOR_URL,
        worker_id=WORKER_ID,
        auth_token=API_KEY or None,
        runtimes=["wasm", "python"],
        sandbox=WasmRuntime(),
    )

    # Override capabilities BEFORE connect() sends them
    worker._send_capabilities = _make_send_capabilities(worker)

    backoff = 5
    while True:
        try:
            print(f"[hf-worker] connecting ...")
            await worker.run()
        except Exception as exc:
            print(f"[hf-worker] disconnected: {exc}")
        print(f"[hf-worker] reconnecting in {backoff}s ...")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, 300)


def _make_send_capabilities(worker: WorkerClient):
    """Return an async function that sends HF-specific capabilities."""

    async def patched():
        ws = worker._ws
        if ws is None:
            return
        await ws.send_json(
            {
                "type": "capabilities",
                "session_id": worker._session_id,
                "payload": HF_CAPABILITIES,
            }
        )

    return patched


if __name__ == "__main__":
    asyncio.run(main())
