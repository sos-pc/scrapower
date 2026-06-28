"""Worker entrypoint — detect hardware, build config, start loop.

Runs on every worker type (Modal, Kaggle, HF Spaces). Adapts
capabilities based on available hardware (GPU type/VRAM, RAM, CPU).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid

from .loop import WorkerLoop


def _detect_gpu() -> tuple[str, int]:
    """Detect GPU type and VRAM via nvidia-smi. Falls back to T4/16GB."""
    gpu_type = "T4"
    gpu_vram_mb = 16384
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
                gpu_vram_mb = int(float(parts[1].strip()))
                for name in ("L40S", "A100", "L4", "T4"):
                    if name in gpu_name:
                        gpu_type = name
                        break
    except Exception:
        pass
    return gpu_type, gpu_vram_mb


def _build_capabilities(
    *,
    gpu_type: str,
    gpu_vram_mb: int,
    ram_mb: int,
    cpu_cores: int,
    disk_mb: int,
    task_types: list[str] | None = None,
    lifecycle_mode: str = "ephemeral",
    max_lifetime_sec: int | None = 21600,
    max_concurrent_tasks: int = 1,
) -> dict:
    """Build the capabilities dict sent to the coordinator on every pull."""
    has_gpu = gpu_vram_mb > 0
    caps: dict = {
        "task_types": task_types
        or (["whisper", "python", "wasm"] if has_gpu else ["wasm", "python"]),
        "runtimes": ["wasm", "python"],
        "resources": {
            "cpu_cores": cpu_cores,
            "ram_mb": ram_mb,
            "disk_mb": disk_mb,
            "gpu": (
                {"supported": True, "type": "cuda", "model": gpu_type, "vram_mb": gpu_vram_mb}
                if has_gpu
                else {"supported": False}
            ),
        },
        "lifecycle": {
            "mode": lifecycle_mode,
            "max_lifetime_sec": max_lifetime_sec,
        },
        "network": {"connectivity": "outgoing_only"},
        "limits": {
            "max_task_duration_ms": 900_000,
            "max_concurrent_tasks": max_concurrent_tasks,
        },
    }
    if max_lifetime_sec is not None:
        caps["lifecycle"]["max_lifetime_sec"] = max_lifetime_sec
    return caps


def main() -> None:
    """Detect hardware, configure WorkerLoop, and run until idle timeout.

    Configuration comes from environment variables (set by the harvester
    or provider). No hardcoded provider-specific logic.
    """
    coordinator_url = os.environ.get("COORDINATOR_URL", "")
    if not coordinator_url:
        print("FATAL: COORDINATOR_URL environment variable is required", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("SCRAPOWER_API_KEY", "")
    worker_prefix = os.environ.get("SCRAPOWER_WORKER_PREFIX", "worker")
    worker_id = f"{worker_prefix}-{uuid.uuid4().hex[:8]}"

    # -- CUDA diagnostic (logged before worker starts, for debugging) --
    print("=== CUDA diagnostic ===", file=sys.stderr)
    try:
        import torch

        print(
            f"torch {torch.__version__}, CUDA {torch.version.cuda}, cuDNN {torch.backends.cudnn.version()}",
            file=sys.stderr,
        )
        print(f"torch.cuda.is_available={torch.cuda.is_available()}", file=sys.stderr)
    except ImportError:
        print("torch not installed", file=sys.stderr)
    import os as _os_diag

    print(f"LD_LIBRARY_PATH={_os_diag.environ.get('LD_LIBRARY_PATH', '(empty)')}", file=sys.stderr)
    # Check /usr/local/cuda for cuDNN
    for _cuda_dir in ["/usr/local/cuda", "/usr/local/cuda-12"]:
        _lib64 = f"{_cuda_dir}/lib64"
        if _os_diag.path.isdir(_lib64):
            _cudnn_libs = [f for f in _os_diag.listdir(_lib64) if "cudnn" in f.lower()]
            print(f"{_cuda_dir}/lib64 cuDNN libs: {_cudnn_libs[:5]}", file=sys.stderr)
    try:
        import ctranslate2

        print(
            f"ctranslate2 {ctranslate2.__version__}, CUDA devices={ctranslate2.get_cuda_device_count()}",
            file=sys.stderr,
        )
    except Exception as _e:
        print(f"ctranslate2: {_e}", file=sys.stderr)

    # Hardware detection
    gpu_type, gpu_vram_mb = _detect_gpu()
    has_gpu = gpu_vram_mb > 0

    ram_mb = int(os.environ.get("SCRAPOWER_RAM_MB", "16384"))
    cpu_cores = int(os.environ.get("SCRAPOWER_CPU_CORES", "2"))
    disk_mb = int(os.environ.get("SCRAPOWER_DISK_MB", "51200"))

    idle_timeout_sec = int(os.environ.get("IDLE_TIMEOUT_SEC", "300"))
    poll_interval_sec = int(os.environ.get("POLL_INTERVAL_SEC", "3"))

    # Log startup info
    gpu_info = f"{gpu_type} GPU {gpu_vram_mb}MB" if has_gpu else f"CPU {cpu_cores} cores"
    print(f"Worker: {worker_id} | {gpu_info} | {ram_mb // 1024} GB RAM | Mode B HTTP")

    capabilities = _build_capabilities(
        gpu_type=gpu_type,
        gpu_vram_mb=gpu_vram_mb,
        ram_mb=ram_mb,
        cpu_cores=cpu_cores,
        disk_mb=disk_mb,
        lifecycle_mode="ephemeral" if has_gpu else "persistent",
        max_lifetime_sec=21600 if has_gpu else None,
        max_concurrent_tasks=1 if has_gpu else 2,
    )

    loop = WorkerLoop(
        worker_id=worker_id,
        coordinator_url=coordinator_url,
        api_key=api_key,
        capabilities=capabilities,
        poll_interval_sec=poll_interval_sec,
        idle_timeout_sec=idle_timeout_sec,
    )

    # Ensure runtime dependencies
    for pkg in ["aiohttp", "wasmtime"]:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

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


# Allow running as `python -m scrapower.worker.entry`
if __name__ == "__main__":
    main()
