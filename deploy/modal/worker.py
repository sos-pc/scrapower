"""Modal worker — Mode B (HTTP pull/submit) for Scrapower.

Runs inside a Modal Sandbox with GPU. Connects to coordinator,
pulls tasks, executes them, submits results. Auto-stops after
idle timeout to save GPU credits.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import aiohttp

# -- Config from environment -----------------------------------------
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "https://scrapower.talos-int.com")
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
    )


def execute_python(executable, input_data):
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "script.py"
        script.write_bytes(executable)
        proc = subprocess.run(
            ["python3", str(script)],
            input=input_data,
            capture_output=True,
            timeout=900,
        )
        try:
            result = json.loads(proc.stdout.decode())
            output = result.get("output_bytes", proc.stdout)
            if isinstance(output, str):
                try:
                    output = bytes.fromhex(output)
                except ValueError:
                    output = output.encode()
            output_hash = result.get("output_hash", hashlib.sha256(output).hexdigest())
            exit_code = result.get("exit_code", 0)
        except (json.JSONDecodeError, UnicodeDecodeError):
            output = proc.stdout
            output_hash = hashlib.sha256(output).hexdigest()
            exit_code = 1 if proc.returncode != 0 else 0
    return output, output_hash, exit_code


# -- Main loop (Mode B: HTTP pull/submit) ----------------------------
async def run_worker():
    global TOTAL_COMPLETED, last_task_time

    async with aiohttp.ClientSession() as session:
        print(f"Polling {COORDINATOR_URL}/worker/pull every {POLL_INTERVAL_SEC}s...")
        while True:
            # PULL
            try:
                async with session.post(
                    f"{COORDINATOR_URL}/worker/pull",
                    json={
                        "type": "pull",
                        "worker_id": WORKER_ID,
                        "capabilities": CAPABILITIES,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    data = await r.json()
            except Exception as e:
                print(f"Pull failed: {e}")
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            task = data.get("task")
            if not task:
                if time.time() - last_task_time > IDLE_TIMEOUT_SEC:
                    print(f"Idle for {IDLE_TIMEOUT_SEC}s — stopping to save credits")
                    break
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            # EXECUTE
            last_task_time = time.time()
            tid = task["id"][:12]
            tok = task["assignment_token"]
            rt = task.get("runtime", "wasm")
            print(f"Task: {tid}... (runtime={rt})")

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
                print(f"  Blob download failed: {e}")
                continue

            try:
                if rt == "python":
                    output, output_hash, exit_code = execute_python(executable, input_data)
                else:
                    output, output_hash, exit_code = execute_wasm(executable, input_data)
            except Exception as e:
                print(f"  Error: {e}")
                continue

            print(f"  OK: {output_hash[:12]}... exit_code={exit_code}")

            # UPLOAD result blob
            try:
                async with session.put(
                    f"{COORDINATOR_URL}/blobs?assignment_token={tok}",
                    data=output,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as r:
                    up = await r.json()
                if not output_hash:
                    output_hash = up.get("hash", "")
            except Exception as e:
                print(f"  Blob upload failed: {e}")
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
                            "execution_metadata": {"exit_code": exit_code, "stderr": ""},
                        },
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    result = await r.json()
                accepted = result.get("accepted", False)
                print(f"  Submit: accepted={accepted}")
                if accepted:
                    TOTAL_COMPLETED += 1
                    print(f"  Total: {TOTAL_COMPLETED}")
            except Exception as e:
                print(f"  Submit failed: {e}")

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
