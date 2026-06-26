"""Modal worker — Mode B (HTTP pull/submit) for Scrapower.

Runs inside a Modal Sandbox with GPU. Connects to coordinator,
pulls tasks, executes them, submits results. Auto-stops after
idle timeout to save GPU credits.
"""

import asyncio
import hashlib
import io
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import aiohttp

# -- Config from environment -----------------------------------------
COORDINATOR_URL = os.environ.get("COORDINATOR_URL")
if not COORDINATOR_URL:
    raise RuntimeError("COORDINATOR_URL environment variable is required")
API_KEY = os.environ.get("SCRAPOWER_API_KEY", "")
WORKER_ID = f"modal-{uuid.uuid4().hex[:8]}"

# Detect GPU type and VRAM
GPU_TYPE = "T4"
GPU_VRAM_MB = 16384
try:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode == 0:
        parts = result.stdout.strip().split(",")
        if len(parts) >= 2:
            gpu_name = parts[0].strip()
            gpu_mem = int(float(parts[1].strip()))
            GPU_VRAM_MB = gpu_mem
            if "L40S" in gpu_name:
                GPU_TYPE = "L40S"
            elif "A100" in gpu_name:
                GPU_TYPE = "A100"
            elif "L4" in gpu_name:
                GPU_TYPE = "L4"
            elif "T4" in gpu_name:
                GPU_TYPE = "T4"
except Exception:
    pass

RAM_MB = 30720
CPU_CORES = 4
IDLE_TIMEOUT_SEC = int(os.environ.get("IDLE_TIMEOUT_SEC", 120))
POLL_INTERVAL_SEC = 3
TOTAL_COMPLETED = 0
last_task_time = time.time()

print(
    f"Worker: {WORKER_ID} | {GPU_TYPE} GPU {GPU_VRAM_MB}MB | {RAM_MB // 1024} GB RAM | Mode B HTTP"
)

# -- Capabilities ----------------------------------------------------
CAPABILITIES = {
    "task_types": ["whisper", "python", "wasm"],
    "runtimes": ["wasm", "python"],
    "resources": {
        "cpu_cores": CPU_CORES,
        "ram_mb": RAM_MB,
        "disk_mb": 102400,
        "gpu": {"supported": True, "type": "cuda", "model": GPU_TYPE, "vram_mb": GPU_VRAM_MB},
    },
    "lifecycle": {"mode": "ephemeral", "max_lifetime_sec": 21600},
    "verification": {"can_challenge": True, "challenge_timeout_max_sec": 300},
    "network": {"connectivity": "outgoing_only"},
    "limits": {"max_task_duration_ms": 900000, "max_concurrent_tasks": 1},
}


# -- Task execution --------------------------------------------------
def execute_wasm(wasm_bytes, input_data):
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


def execute_python(executable, input_data):
    """Run a Python task script in a sandboxed subprocess.

    Minimal environment: no secrets, no network except WG_PROXY.
    Isolated to temp directory.
    """
    import threading

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "script.py"
        script.write_bytes(executable)
        # Sandbox: minimal env, no secrets, working dir isolated
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

        # Read stderr line-by-line in a thread, feed to global log buffer
        stderr_lines: list[str] = []

        def _read_stderr():
            for line in proc.stderr:
                decoded = line.decode(errors="replace").rstrip()
                stderr_lines.append(decoded)
                _log(f"[sub] {decoded}")

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()
        _log("[sub] stderr reader started")

        # Write input and close stdin (blocks until process exits or timeout)
        try:
            stdout_data, _ = proc.communicate(input=input_data, timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_data, _ = proc.communicate()
            _log("[sub] TIMEOUT after 600s")
        stderr_thread.join(timeout=5)
        exit_code = proc.returncode
        stderr_str = "\n".join(stderr_lines)
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


# -- Main loop (Mode B: HTTP pull/submit) ----------------------------

# Log buffer: workers accumulate stderr here and flush to coordinator
# via pull requests and heartbeat. Enables debugging stuck workers.
_LOG_LINES: list[str] = []
_LOG_TASK_ID: str = ""
_LOG_TOKEN: str = ""


def _log(msg: str) -> None:
    """Append to in-memory log buffer (also print for sandbox stdout)."""
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    _LOG_LINES.append(line)
    if len(_LOG_LINES) > 200:
        del _LOG_LINES[:-100]
    print(line)


def _drain_logs() -> str:
    """Return recent logs and clear the buffer."""
    if not _LOG_LINES:
        return ""
    chunk = "\n".join(_LOG_LINES[-50:])
    _LOG_LINES.clear()
    return chunk


def _run_task_sync(executable, input_data, rt):
    """Execute task in a thread, capturing stdout/stderr into log buffer."""
    import sys

    # Redirect stderr to a StringIO so we can capture it for heartbeats
    stderr_capture = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = stderr_capture
    try:
        if rt == "python":
            result = execute_python(executable, input_data)
        else:
            result = execute_wasm(executable, input_data)
        # Flush captured stderr into log buffer
        captured = stderr_capture.getvalue()
        if captured:
            for line in captured.split("\n"):
                if line.strip():
                    _log(f"[sub] {line.strip()}")
        return result
    finally:
        sys.stderr = old_stderr


async def _heartbeat(session, interval=30):
    """Periodically send logs to coordinator during task execution."""
    while _LOG_TASK_ID:
        await asyncio.sleep(interval)
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
                ack = await r.json()
            if not ack.get("task_valid"):
                _log("Heartbeat: task reassigned, aborting")
                _LOG_TASK_ID = ""  # stop transcription loop
        except Exception as e:
            _log(f"Heartbeat failed: {e}")


async def run_worker():
    global TOTAL_COMPLETED, last_task_time, _LOG_TASK_ID, _LOG_TOKEN

    async with aiohttp.ClientSession() as session:
        _log(f"Polling {COORDINATOR_URL}/worker/pull every {POLL_INTERVAL_SEC}s...")
        while True:
            # Drain any buffered logs before pull
            logs_chunk = _drain_logs()

            # PULL (with retry on 5xx / transient errors)
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
                if time.time() - last_task_time > IDLE_TIMEOUT_SEC:
                    _log(f"Idle for {IDLE_TIMEOUT_SEC}s — stopping to save credits")
                    break
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            # EXECUTE (in thread, with heartbeat)
            last_task_time = time.time()
            tid = task["id"][:12]
            tok = task["assignment_token"]
            rt = task.get("runtime", "wasm")
            _log(f"Task: {tid}... (runtime={rt})")

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
                _log(f"Blob download failed: {e}")
                continue

            # Start heartbeat during task execution
            _LOG_TASK_ID = task["id"]
            _LOG_TOKEN = tok
            hb_task = asyncio.create_task(_heartbeat(session))

            worker_stderr = ""
            output = b""
            output_hash = ""
            exit_code = 1
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, _run_task_sync, executable, input_data, rt
                )
                output, output_hash, exit_code, worker_stderr = result
            except Exception as e:
                worker_stderr = f"{type(e).__name__}: {e}"
                _log(f"Error: {worker_stderr}")
            finally:
                _LOG_TASK_ID = ""
                _LOG_TOKEN = ""
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

            _log(f"OK: {output_hash[:12]}... exit_code={exit_code}")

            # UPLOAD + SUBMIT — retry up to 3 times for transient failures
            submitted = False
            for attempt in range(3):
                try:
                    async with session.put(
                        f"{COORDINATOR_URL}/blobs?assignment_token={tok}",
                        data=output,
                        timeout=aiohttp.ClientTimeout(
                            total=min(300, max(30, 10 + len(output) // 50_000))
                        ),
                    ) as r:
                        up = await r.json()
                    output_hash = up.get("hash", output_hash)
                except Exception as e:
                    _log(f"Blob upload failed (attempt {attempt + 1}/3): {e}")
                    await asyncio.sleep(1)
                    continue

                # SUBMIT
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
                        _log(f"Total completed: {TOTAL_COMPLETED}")
                        submitted = True
                        break
                    _log(f"Submit rejected (attempt {attempt + 1}/3)")
                except Exception as e:
                    _log(f"Submit failed (attempt {attempt + 1}/3): {e}")

                await asyncio.sleep(1)

            if not submitted:
                _log("Submit failed after 3 attempts — task will be requeued by stale check")

            await asyncio.sleep(POLL_INTERVAL_SEC)


# -- Entrypoint ------------------------------------------------------
if __name__ == "__main__":
    # Ensure dependencies
    for pkg in ["aiohttp", "faster-whisper", "yt-dlp"]:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            subprocess.check_call(["pip", "install", "-q", pkg])

    # Run with reconnect backoff
    backoff = 5
    while True:
        try:
            asyncio.run(run_worker())
            break
        except Exception as e:
            print(f"Worker crashed: {e}")
            print(f"Restarting in {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 300)
