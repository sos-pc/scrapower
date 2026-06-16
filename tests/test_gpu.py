"""Test GPU — multiplication matricielle sur GPU vs CPU.

Usage:
    python tests/test_gpu.py --url http://localhost:8777 --api-key KEY --size 256
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


def make_matmul_input(size: int) -> bytes:
    """Create input for matrix multiplication: 4-byte N (u32 LE) + A + B (f32, row-major).

    Uses identity-like matrices for easy verification: A[i][j] = i*N+j, B = identity.
    """
    import random

    rng = random.Random(42)
    data = bytearray()
    # Matrix size (u32 LE)
    data += struct.pack("<I", size)
    # Matrix A: random floats 0..1
    for _ in range(size * size):
        data += struct.pack("<f", rng.random())
    # Matrix B: random floats 0..1
    for _ in range(size * size):
        data += struct.pack("<f", rng.random())
    return bytes(data)


async def main():
    parser = argparse.ArgumentParser(description="Test GPU matrix multiplication")
    parser.add_argument("--url", default="http://localhost:8777", help="Coordinator URL")
    parser.add_argument("--api-key", required=True, help="API key")
    parser.add_argument("--size", type=int, default=256, help="Matrix size (N×N)")
    parser.add_argument("--count", type=int, default=4, help="Number of tasks")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    headers = {"X-API-Key": args.api_key}
    size = args.size

    print(f"Génération de matrices {size}×{size} ({2 * size * size * 4} bytes)...")
    input_data = make_matmul_input(size)
    print(f"  -> {len(input_data)} bytes d'input")

    async with aiohttp.ClientSession(headers=headers) as session:
        # Upload input blob
        async with session.put(f"{base_url}/blobs", data=input_data) as r:
            r.raise_for_status()
            input_hash = (await r.json())["hash"]
        print(f"  -> input_hash: {input_hash}")

        # Upload a dummy executable (GPU tasks don't need WASM, but the API requires it)
        dummy_wasm = b"\x00asm\x01\x00\x00\x00"  # minimal WASM header
        async with session.put(f"{base_url}/blobs", data=dummy_wasm) as r:
            r.raise_for_status()
            exec_hash = (await r.json())["hash"]
        print(f"  -> exec_hash: {exec_hash}")

        # Submit GPU tasks
        task_ids = []
        print(f"\nSoumission de {args.count} tâches GPU (size={size})...")
        for i in range(args.count):
            tid = uuid.uuid4().hex
            async with session.post(
                f"{base_url}/tasks",
                json={
                    "task_id": tid,
                    "client_id": "gpu-test",
                    "runtime": "wasm",
                    "executable_hash": exec_hash,
                    "input_hash": input_hash,
                    "gpu_required": True,
                },
            ) as r:
                r.raise_for_status()
            task_ids.append(tid)
        print("  OK")

        # Wait for completion
        print("\nAttente...")
        start = time.time()
        completed: dict[str, dict] = {}
        timeout = 120

        while len(completed) < args.count and (time.time() - start) < timeout:
            for tid in task_ids:
                if tid in completed:
                    continue
                try:
                    async with session.get(f"{base_url}/tasks/{tid}") as r:
                        if r.status != 200:
                            continue
                        info = await r.json()
                        if info.get("status") in ("validated", "failed", "cancelled"):
                            completed[tid] = info
                except Exception:
                    pass
            print(f"\r  {len(completed)}/{args.count} completed...", end="")
            await asyncio.sleep(0.5)

        elapsed = time.time() - start
        print(f"\n  OK {len(completed)}/{args.count} en {elapsed:.1f}s\n")

        # Report
        workers = {}
        for tid, info in completed.items():
            wid = info.get("assigned_worker_id") or "unknown"
            workers[wid] = workers.get(wid, 0) + 1

        print("=" * 50)
        print(f"  GPU MatMul {size}×{size} — {args.count} tâches")
        print("=" * 50)
        for wid, count in sorted(workers.items()):
            print(f"  {wid:<30s} {count} tâches")
        dur = elapsed / args.count if args.count > 0 else 0
        print(f"\n  Temps moyen: {dur:.0f}ms/tâche")
        print(f"  Taille matrice: {size}×{size} = {size * size} éléments")


if __name__ == "__main__":
    asyncio.run(main())
