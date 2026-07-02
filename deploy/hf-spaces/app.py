"""HuggingFace Spaces worker — Mode B (HTTP pull/submit).

Thin wrapper around scrapower.worker. The Dockerfile copies src/
so the worker package is importable directly.

Runs a health server on :7860 (HF requirement — without it the
container is killed as unhealthy). CPU-only: no GPU, no whisper.
"""

import asyncio
import http.server
import os
import socketserver
import threading
import time
import uuid

# -- Config (injected by Harvester as Space secrets) -------------------
COORDINATOR_URL = os.environ.get("COORDINATOR_URL")
if not COORDINATOR_URL:
    raise RuntimeError("COORDINATOR_URL is required — inject via HF Space secrets")
API_KEY = os.environ.get("SCRAPOWER_API_KEY", "")
WORKER_ID = f"hf-{uuid.uuid4().hex[:8]}"
RAM_MB = 16384
CPU_CORES = 2
DISK_MB = 51200
IDLE_TIMEOUT_SEC = int(os.environ.get("IDLE_TIMEOUT_SEC", 300))

print(
    f"Worker: {WORKER_ID} | CPU {CPU_CORES} cores |"
    f" {RAM_MB // 1024} GB RAM | Mode B HTTP (HF Spaces)"
)


# -- Health server (HF requirement) ------------------------------------
def _run_health_server():
    """Minimal HTTP server on :7860 — HF pings this to check liveness."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            pass  # silent

    with socketserver.TCPServer(("0.0.0.0", 7860), Handler) as httpd:
        httpd.serve_forever()


# -- Main --------------------------------------------------------------
if __name__ == "__main__":
    # Start health server in background thread
    threading.Thread(target=_run_health_server, daemon=True).start()
    print("Health server on :7860")

    from scrapower.worker.loop import WorkerLoop

    capabilities = {
        "task_types": ["python"],
        "runtimes": ["python"],
        "resources": {
            "cpu_cores": CPU_CORES,
            "ram_mb": RAM_MB,
            "disk_mb": DISK_MB,
            "gpu": {"supported": False},
        },
        "lifecycle": {
            "mode": "persistent",
            "max_lifetime_sec": None,
        },
        "network": {"connectivity": "outgoing_only"},
        "limits": {"max_task_duration_ms": 600_000, "max_concurrent_tasks": 2},
    }

    loop = WorkerLoop(
        worker_id=WORKER_ID,
        coordinator_url=COORDINATOR_URL,
        api_key=API_KEY,
        capabilities=capabilities,
        idle_timeout_sec=IDLE_TIMEOUT_SEC,
    )

    # Run with reconnect on crash
    backoff = 5
    while True:
        try:
            asyncio.run(loop.run())
            break
        except Exception as e:
            print(f"Worker crashed: {e}")
            print(f"Restarting in {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 300)
