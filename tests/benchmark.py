"""Benchmark suite — mesure les performances de Scrapower.

Usage:
    python tests/benchmark.py --url http://localhost:8777 --api-key KEY
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import sys
import time
import uuid
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def make_monte_carlo_input(seed: int, points: int) -> bytes:
    """Input for monte_carlo_pi.wat: 8-byte seed (i64 LE)."""
    return struct.pack("<q", seed)


def make_matmul_input(size: int) -> bytes:
    """Two random matrices of given size, f32 row-major."""
    import random

    rng = random.Random(42)
    data = bytearray()
    data += struct.pack("<I", size)
    for _ in range(size * size):
        data += struct.pack("<f", rng.random())
    for _ in range(size * size):
        data += struct.pack("<f", rng.random())
    return bytes(data)


def make_python_prime_sieve(limit: int) -> bytes:
    """Python code that computes primes up to limit."""
    code = f"""
result = []
sieve = [True] * {limit}
for i in range(2, int({limit}**0.5) + 1):
    if sieve[i]:
        for j in range(i*i, {limit}, i):
            sieve[j] = False
primes = [i for i in range(2, {limit}) if sieve[i]]
print(f"Found {{len(primes)}} primes up to {limit}")
print(f"Largest: {{primes[-1]}}")
"""
    return code.encode()


async def upload_blob(session: aiohttp.ClientSession, url: str, data: bytes) -> str:
    async with session.put(f"{url}/blobs", data=data) as r:
        r.raise_for_status()
        return (await r.json())["hash"]


async def submit_and_wait(
    session: aiohttp.ClientSession,
    url: str,
    task_type: str,
    exec_hash: str,
    input_hash: str,
    count: int,
    timeout: int = 120,
):
    """Submit N tasks and wait for completion. Returns (elapsed_sec, worker_distribution)."""
    task_ids = [uuid.uuid4().hex for _ in range(count)]
    for tid in task_ids:
        body = {
            "task_id": tid,
            "client_id": "bench",
            "runtime": "wasm",
            "executable_hash": exec_hash,
            "input_hash": input_hash,
        }
        if task_type == "gpu":
            body["gpu_required"] = True
        elif task_type == "python":
            body["runtime"] = "python"
        async with session.post(f"{url}/tasks", json=body) as r:
            r.raise_for_status()

    start = time.time()
    completed = {}
    workers = {}
    while len(completed) < count and (time.time() - start) < timeout:
        for tid in task_ids:
            if tid in completed:
                continue
            try:
                async with session.get(f"{url}/tasks/{tid}") as r:
                    if r.status != 200:
                        continue
                    info = await r.json()
                    if info.get("status") in ("completed", "failed", "cancelled"):
                        completed[tid] = info
                        wid = info.get("assigned_worker_id", "?")
                        workers[wid] = workers.get(wid, 0) + 1
            except Exception:
                pass
        await asyncio.sleep(0.1)

    elapsed = time.time() - start
    success = sum(1 for t in completed.values() if t["status"] == "completed")
    return elapsed, workers, success


async def main():
    parser = argparse.ArgumentParser(description="Scrapower benchmark")
    parser.add_argument("--url", default="http://localhost:8777")
    parser.add_argument("--api-key", required=True)
    args = parser.parse_args()

    url = args.url.rstrip("/")
    headers = {"X-API-Key": args.api_key}

    print("=" * 55)
    print("  SCRAPOWER BENCHMARK")
    print("=" * 55)

    async with aiohttp.ClientSession(headers=headers) as s:
        # ── Setup: compile WASM modules ──
        from wasmtime import wat2wasm

        monte_carlo_wasm = wat2wasm(Path("tests/../examples/monte_carlo_pi.wat").read_text())
        mc_hash = await upload_blob(s, url, monte_carlo_wasm)

        multiply_wasm = wat2wasm(Path("tests/../examples/multiply.wat").read_text())
        mult_hash = await upload_blob(s, url, multiply_wasm)

        dummy_wasm = b"\x00asm\x01\x00\x00\x00"
        dummy_hash = await upload_blob(s, url, dummy_wasm)

        # ── Bench 1: Monte Carlo π (100K points × 10 tasks) ──
        print("\n📐 Monte Carlo π — 100K pts × 10 tâches")
        mc_input = make_monte_carlo_input(42, 100000)
        mc_input_hash = await upload_blob(s, url, mc_input)
        elapsed, workers, ok = await submit_and_wait(s, url, "wasm", mc_hash, mc_input_hash, 10)
        tasks_per_sec = 10 / elapsed if elapsed > 0 else 0
        print(
            f"   {elapsed:.1f}s | {tasks_per_sec:.1f} tâches/s | {ok}/10 réussies | workers: {dict(workers)}"
        )

        # ── Bench 2: GPU MatMul 256×256 × 6 tâches ──
        print("\n⚡ GPU MatMul 256×256 — 6 tâches")
        mm256 = make_matmul_input(256)
        mm256_hash = await upload_blob(s, url, mm256)
        elapsed, workers, ok = await submit_and_wait(s, url, "gpu", dummy_hash, mm256_hash, 6)
        print(f"   {elapsed:.1f}s | {ok}/6 réussies | workers: {dict(workers)}")

        # ── Bench 3: GPU MatMul 512×512 × 2 tâches (gros blobs → P2P) ──
        print("\n⚡ GPU MatMul 512×512 — 2 tâches (test P2P)")
        mm512 = make_matmul_input(512)
        mm512_hash = await upload_blob(s, url, mm512)
        elapsed, workers, ok = await submit_and_wait(s, url, "gpu", dummy_hash, mm512_hash, 2)
        blob_size_mb = len(mm512) / (1024 * 1024)
        print(f"   {elapsed:.1f}s | blob: {blob_size_mb:.1f}MB | workers: {dict(workers)}")

        # ── Bench 4: Python prime sieve × 4 tâches ──
        print("\n🐍 Python prime sieve (100K) — 4 tâches")
        py_code = make_python_prime_sieve(100000)
        py_hash = await upload_blob(s, url, py_code)
        elapsed, workers, ok = await submit_and_wait(s, url, "python", dummy_hash, py_hash, 4)
        print(f"   {elapsed:.1f}s | {ok}/4 réussies | workers: {dict(workers)}")

        # ── Bench 5: Stress — 50 tâches rapides ──
        print("\n🔥 Stress test — 50 tâches WASM rapides (1+2)")
        add_input = struct.pack("<ii", 1, 2)
        add_hash = await upload_blob(s, url, add_input)
        elapsed, workers, ok = await submit_and_wait(s, url, "wasm", mult_hash, add_hash, 50)
        tasks_per_sec = 50 / elapsed if elapsed > 0 else 0
        print(
            f"   {elapsed:.1f}s | {tasks_per_sec:.1f} tâches/s | {ok}/50 réussies | workers: {dict(workers)}"
        )

    print("\n" + "=" * 55)
    print("  BENCH COMPLETE")
    print("=" * 55)


if __name__ == "__main__":
    asyncio.run(main())
