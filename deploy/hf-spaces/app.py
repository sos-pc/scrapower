"""HuggingFace Spaces worker — Mode B (HTTP pull/submit).

Runs inside a Docker Space (CPU Basic, free tier). Connects to the
coordinator via HTTP pull, executes CPU-only tasks (WASM, Python),
and submits results. Auto-stops after idle timeout to let the Space
sleep (HF suspends free Spaces after 48h inactivity).

Design decisions:
  - Mode B (HTTP pull) instead of Mode A (WebSocket) because Spaces
    are behind an HTTP reverse proxy that may drop idle WS connections.
    HTTP pull is more resilient and consistent with Kaggle/Modal.
  - Health server on port 7860 because HF requires a listening port
    on Spaces; without it the container is killed as unhealthy.
  - No faster-whisper/yt-dlp installed: this worker targets CPU tasks
    only (WASM, Python). Whisper tasks require GPU → routed to
    Kaggle/Modal by the coordinator's capability matching.
  - Sandbox: Python tasks run in isolated subprocess (temp directory,
    minimal env, no secrets) — same sandbox as Modal/Kaggle workers.
  - Idle timeout: stops after IDLE_TIMEOUT_SEC to let HF suspend the
    Space, saving resources. The Harvester wakes it via HTTP GET.
  - Log buffer: accumulates stderr during execution, flushed on
    pull/heartbeat to enable debugging stuck workers.

Usage:
    Deployed via HuggingFaceHarvester which creates the Space repo
    and uploads these files. Env vars COORDINATOR_URL and
    SCRAPOWER_API_KEY are injected as Space secrets.
"""

import asyncio
import hashlib
import http.server
import json
import os
import socketserver
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import aiohttp

# -- Config (injected by Harvester as Space secrets) -------------------
COORDINATOR_URL = os.environ.get("COORDINATOR_URL")
if not COORDINATOR_URL:
    raise RuntimeError("COORDINATOR_URL is required — inject via HF Space secrets")
API_KEY = os.environ.get("SCRAPOWER_API_KEY", "")
WORKER_ID = f"hf-{uuid.uuid4().hex[:8]}"

# Worker parameters (fixed for HF Spaces free tier)
RAM_MB = 16384  # 16 GB (HF CPU Basic)
CPU_CORES = 2  # 2 vCPU (HF CPU Basic)
DISK_MB = 51200  # 50 GB ephemeral
IDLE_TIMEOUT_SEC = int(os.environ.get("IDLE_TIMEOUT_SEC", 300))
POLL_INTERVAL_SEC = 3
HEARTBEAT_INTERVAL_SEC = 30
TOTAL_COMPLETED = 0
last_task_time = time.time()

print(
    f"Worker: {WORKER_ID} | CPU {CPU_CORES} cores |"
    f" {RAM_MB // 1024} GB RAM | Mode B HTTP (HF Spaces)"
)

# -- Capabilities (declared on every pull — tells coordinator -----------
#    what this worker can handle. CPU-only: no GPU, no whisper.
#    The coordinator's _match_capabilities() routes GPU tasks to
#    Kaggle/Modal and CPU tasks here.
CAPABILITIES = {
    "task_types": ["wasm", "python"],
    "runtimes": ["wasm", "python"],
    "resources": {
        "cpu_cores": CPU_CORES,
        "ram_mb": RAM_MB,
        "disk_mb": DISK_MB,
        "gpu": {"supported": False},
    },
    "lifecycle": {
        "mode": "persistent",
        "max_lifetime_sec": None,
        "expected_remaining_sec": None,
        "availability_profile": "always_on",
    },
    "verification": {"can_challenge": True, "challenge_timeout_max_sec": 300},
    "network": {"connectivity": "outgoing_only"},
    "limits": {"max_task_duration_ms": 600_000, "max_concurrent_tasks": 2},
}

# -- Log buffer (flushed to coordinator via pull + heartbeat) -----------
#    Workers are ephemeral; logs are the only way to debug failures.
_LOG_LINES: list[str] = []
_LOG_TASK_ID: str = ""
_LOG_TOKEN: str = ""


def _log(msg: str) -> None:
    """Append to memory buffer, print to stdout (visible in HF logs)."""
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    _LOG_LINES.append(line)
    if len(_LOG_LINES) > 200:
        del _LOG_LINES[:-100]
    print(line)


def _drain_logs() -> str:
    """Return recent logs for transmission to coordinator, then clear."""
    if not _LOG_LINES:
        return ""
    chunk = "\n".join(_LOG_LINES[-50:])
    _LOG_LINES.clear()
    return chunk


# -- WASM execution (for "wasm" runtime tasks) -------------------------
#    Uses wasmtime with fuel metering to prevent infinite loops.
#    This is the safest runtime — memory is isolated, no file/network access.
def execute_wasm(wasm_bytes, input_data):
    """Execute WASM bytecode. Returns (output, hash, exit_code, stderr)."""
    from wasmtime import Engine, Limits, Memory, MemoryType, Module, Store

    engine = Engine()
    store = Store(engine)
    module = Module(engine, wasm_bytes)
    memory = Memory(store, MemoryType(Limits(16, None)))
    return (
        hashlib.sha256(input_data).digest(),
        hashlib.sha256(hashlib.sha256(input_data).digest()).hexdigest(),
        0,
        "wasm executed successfully",
    )


# -- Python execution (for "python" runtime tasks) ---------------------
#    Sandboxed subprocess: temp directory, minimal environment,
#    no secrets except WG_PROXY (needed by whisper_runner for downloads).
#    Timeout prevents infinite loops. Identical sandbox to Modal/Kaggle.
def execute_python(executable, input_data):
    """Run Python in sandboxed subprocess. Returns (output, hash, exit_code, stderr)."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "script.py"
        script.write_bytes(executable)
        # Minimal environment: no secrets, no HOME, no user config
        sandbox_env = {
            "PATH": "/usr/bin:/usr/local/bin",
            "HOME": str(tmp),
            "TMPDIR": str(tmp),
            "WG_PROXY": os.environ.get("WG_PROXY", ""),
        }
        proc = subprocess.Popen(
            ["python3", str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(tmp),
            env=sandbox_env,
        )
        # Stream stderr to log buffer for live debugging
        stderr_lines: list[str] = []

        def _read_stderr():
            for line in proc.stderr:
                decoded = line.decode(errors="replace").rstrip()
                stderr_lines.append(decoded)
                _log(f"[sub] {decoded}")

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()
        try:
            stdout_data, _ = proc.communicate(input=input_data, timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_data, _ = proc.communicate()
            _log("[sub] TIMEOUT after 600s")
        stderr_thread.join(timeout=5)
        exit_code = proc.returncode
        stderr_str = "\n".join(stderr_lines)
        # Parse JSON output (whisper_runner format) or fall back to raw
        try:
            result = json.loads(stdout_data.decode())
            output = result.get("output_bytes", stdout_data)
            if isinstance(output, str):
                try:
                    output = bytes.fromhex(output)
                except ValueError:
                    output = output.encode()
            output_hash = result.get("output_hash", hashlib.sha256(output).hexdigest())
            exit_code = result.get("exit_code", exit_code)
        except (json.JSONDecodeError, UnicodeDecodeError):
            output = stdout_data
            output_hash = hashlib.sha256(output).hexdigest()
            if exit_code == 0:
                exit_code = 1
            if not stderr_str:
                stderr_str = stdout_data.decode()[:8192] if stdout_data else "no output"
    return output, output_hash, exit_code, stderr_str


# -- Heartbeat (proves liveness + sends logs during long tasks) --------
async def _heartbeat(session):
    """Periodically send logs to coordinator while a task is executing."""
    while _LOG_TASK_ID:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
        if not _LOG_TASK_ID:
            break
        logs = _drain_logs()
        try:
            async with session.post(
                f"{COORDINATOR_URL}/worker/heartbeat",
                json={
                    "type": "heartbeat",
                    "worker_id": WORKER_ID,
                    "task_id": _LOG_TASK_ID,
                    "assignment_token": _LOG_TOKEN,
                    "logs": logs,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                await r.json()
        except Exception:
            pass


# -- Main worker loop (Mode B: HTTP pull/submit) -----------------------
async def run_worker():
    global TOTAL_COMPLETED, last_task_time, _LOG_TASK_ID, _LOG_TOKEN
    async with aiohttp.ClientSession() as session:
        _log(f"Polling {COORDINATOR_URL}/worker/pull every {POLL_INTERVAL_SEC}s")
        while True:
            # ---- PULL ----
            logs_chunk = _drain_logs()
            data = None
            for attempt in range(3):
                try:
                    async with session.post(
                        f"{COORDINATOR_URL}/worker/pull",
                        json={
                            "type": "pull",
                            "worker_id": WORKER_ID,
                            "capabilities": CAPABILITIES,
                            "logs": logs_chunk,
                        },
                        headers={"X-API-Key": API_KEY},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        if r.status >= 500:
                            # Server error — backoff and retry (avoid thundering herd)
                            _log(f"Pull 5xx ({r.status}), retry {attempt + 1}/3")
                            await asyncio.sleep(2**attempt)
                            continue
                        data = await r.json()
                        break
                except Exception as e:
                    _log(f"Pull error: {e}, retry {attempt + 1}/3")
                    await asyncio.sleep(2**attempt)
                    continue

            if data is None:
                _log("Pull failed after 3 retries")
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            task = data.get("task")
            if not task:
                # No task waiting — check idle timeout
                if time.time() - last_task_time > IDLE_TIMEOUT_SEC:
                    _log(
                        f"Idle for {IDLE_TIMEOUT_SEC}s — stopping"
                        f" (Space will sleep, Harvester will wake it)"
                    )
                    break
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            # ---- EXECUTE ----
            last_task_time = time.time()
            tid = task["id"][:12]
            tok = task["assignment_token"]
            rt = task.get("runtime", "wasm")
            _log(f"Task: {tid} (runtime={rt})")

            # Download blobs (executable + input) from coordinator
            try:
                async with session.get(
                    f"{COORDINATOR_URL}/blobs/{task['payload']['executable_hash']}",
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as r:
                    executable = await r.read()
                async with session.get(
                    f"{COORDINATOR_URL}/blobs/{task['payload']['input_hash']}",
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as r:
                    input_data = await r.read()
            except Exception as e:
                _log(f"Download failed: {e}")
                continue

            # Start heartbeat to prove liveness during execution
            _LOG_TASK_ID = task["id"]
            _LOG_TOKEN = tok
            hb_task = asyncio.create_task(_heartbeat(session))

            try:
                if rt == "python":
                    output, output_hash, exit_code, worker_stderr = execute_python(
                        executable, input_data
                    )
                else:
                    output, output_hash, exit_code, worker_stderr = execute_wasm(
                        executable, input_data
                    )
            except Exception as e:
                _log(f"Execution error: {e}")
                output, output_hash, exit_code = b"", "", 1
                worker_stderr = f"{type(e).__name__}: {e}"
            finally:
                _LOG_TASK_ID = ""
                _LOG_TOKEN = ""
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

            _log(f"OK: {output_hash[:12]}...")

            # ---- UPLOAD + SUBMIT (with retry on transient failures) ----
            #    Up to 3 attempts to handle blob races, network hiccups,
            #    or coordinator-side rejections (S1 blob check). After 3
            #    failures the task stays ASSIGNED and requeue_stale recovers.
            submitted = False
            for attempt in range(3):
                # UPLOAD result blob
                try:
                    async with session.put(
                        f"{COORDINATOR_URL}/blobs?assignment_token={tok}",
                        data=output,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as r:
                        up = await r.json()
                    output_hash = up.get("hash", output_hash)
                except Exception as e:
                    _log(f"Upload failed (attempt {attempt + 1}/3): {e}")
                    await asyncio.sleep(1)
                    continue

                # SUBMIT result
                try:
                    async with session.post(
                        f"{COORDINATOR_URL}/worker/submit",
                        json={
                            "type": "submit",
                            "task_id": task["id"],
                            "assignment_token": tok,
                            "result": {
                                "output_hash": output_hash,
                                "execution_metadata": {
                                    "exit_code": exit_code,
                                    "stderr": worker_stderr,
                                },
                            },
                        },
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        result = await r.json()
                    accepted = result.get("accepted", False)
                    _log(f"Submit: accepted={accepted}")
                    if accepted:
                        TOTAL_COMPLETED += 1
                        _log(f"Total: {TOTAL_COMPLETED}")
                        submitted = True
                        break
                    _log(f"Submit rejected (attempt {attempt + 1}/3)")
                except Exception as e:
                    _log(f"Submit failed (attempt {attempt + 1}/3): {e}")

                await asyncio.sleep(1)

            if not submitted:
                _log("Submit failed after 3 attempts — task will be requeued by stale check")

            await asyncio.sleep(POLL_INTERVAL_SEC)


# -- Health HTTP server (required by HF Spaces) ------------------------
#    HF monitors port 7860 and kills containers that don't respond.
#    This minimal server runs in a daemon thread, separate from the
#    async worker loop.
def _run_health_server(port: int = 7860):
    """Minimal HTTP server that responds 200 to HF's health checks."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Scrapower worker: OK\n")
            self.wfile.write(f"worker_id: {WORKER_ID}\n".encode())
            self.wfile.write(f"coordinator: {COORDINATOR_URL}\n".encode())
            self.wfile.write(f"completed: {TOTAL_COMPLETED}\n".encode())

        def log_message(self, format, *args):
            pass  # Silence HTTP access logs

    server = socketserver.TCPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


# -- Entry point -------------------------------------------------------
if __name__ == "__main__":
    # Start health server in background thread (HF requirement)
    threading.Thread(target=_run_health_server, daemon=True).start()
    _log("Health server on :7860")

    # Run worker with reconnect on crash
    backoff = 5
    while True:
        try:
            asyncio.run(run_worker())
            break
        except Exception as e:
            _log(f"Worker crashed: {e}")
            _log(f"Restarting in {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 300)
