"""Test de distribution multi-worker — vérifie que les tâches sont bien réparties.

Usage:
    python tests/test_distribution.py --url http://localhost:8777 --count 20 --api-key KEY

Prérequis : avoir 2+ workers connectés (onglets navigateur ou workers natifs).
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


def compile_wat(wat_path: str) -> bytes:
    """Compile a .wat file to .wasm binary using wasmtime."""
    from wasmtime import wat2wasm

    wat_text = Path(wat_path).read_text()
    return wat2wasm(wat_text)


def make_input(a: int, b: int) -> bytes:
    """Create input bytes: two little-endian i32 values."""
    return struct.pack("<ii", a, b)


async def main():
    parser = argparse.ArgumentParser(description="Test de distribution multi-worker")
    parser.add_argument("--url", default="http://localhost:8777", help="Coordinator URL")
    parser.add_argument("--count", type=int, default=10, help="Number of tasks")
    parser.add_argument("--api-key", required=True, help="API key")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    task_count = args.count
    headers = {"X-API-Key": args.api_key}

    # 1. Compile WASM
    wat_path = Path(__file__).parent / "test_add.wat"
    if not wat_path.exists():
        print(f"ERROR: {wat_path} not found")
        sys.exit(1)

    print(f"Compilation de {wat_path.name}...")
    wasm_bytes = compile_wat(str(wat_path))
    print(f"  -> {len(wasm_bytes)} bytes de WASM")

    async with aiohttp.ClientSession(headers=headers) as session:
        # 2. Upload blobs
        print("Upload de l'exécutable WASM...")
        async with session.put(f"{base_url}/blobs", data=wasm_bytes) as r:
            r.raise_for_status()
            exec_hash = (await r.json())["hash"]
        print(f"  -> executable_hash: {exec_hash}")

        input_data = make_input(1, 2)
        async with session.put(f"{base_url}/blobs", data=input_data) as r:
            r.raise_for_status()
            input_hash = (await r.json())["hash"]
        print(f"  -> input_hash: {input_hash}")

        # 3. Submit tasks
        task_ids = []
        print(f"\nSoumission de {task_count} tâches...")
        for i in range(task_count):
            tid = uuid.uuid4().hex
            async with session.post(
                f"{base_url}/tasks",
                json={
                    "task_id": tid,
                    "client_id": "dist-test",
                    "runtime": "wasm",
                    "executable_hash": exec_hash,
                    "input_hash": input_hash,
                },
            ) as r:
                r.raise_for_status()
            task_ids.append(tid)

        print("  OK toutes soumises (state=queued)")

        # 4. Wait for completion
        print("\nAttente de la complétion...")
        start = time.time()
        completed: dict[str, dict] = {}
        timeout = 60

        while len(completed) < task_count and (time.time() - start) < timeout:
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
            remaining = task_count - len(completed)
            print(f"\r  {len(completed)}/{task_count} completed, {remaining} remaining...", end="")
            await asyncio.sleep(0.5)

        elapsed = time.time() - start
        print(f"\n  OK toutes complétées en {elapsed:.1f}s\n")

        # 5. Report distribution
        worker_tasks: dict[str, int] = {}
        for tid, info in completed.items():
            wid = info.get("assigned_worker_id") or "unknown"
            worker_tasks[wid] = worker_tasks.get(wid, 0) + 1

        print("=" * 55)
        print("  REPARTITION DES TACHES")
        print("=" * 55)
        for wid, count in sorted(worker_tasks.items()):
            pct = 100 * count / task_count
            bar = "#" * count
            print(f"  {wid:<30s} {count:>3d} taches  ({pct:5.1f}%)  {bar}")
        print("=" * 55)

        counts = list(worker_tasks.values())
        if len(counts) >= 2:
            ideal = task_count / len(counts)
            max_dev = max(abs(c - ideal) for c in counts) / ideal * 100
            if max_dev < 20:
                print(f"  [OK] Bonne distribution (ecart max {max_dev:.0f}%)")
            elif max_dev < 50:
                print(f"  [~]  Distribution acceptable (ecart max {max_dev:.0f}%)")
            else:
                print(f"  [!!] Mauvaise distribution (ecart max {max_dev:.0f}%)")
        elif len(counts) == 1:
            print("  [!!] Un seul worker a tout pris — ouvre plus d'onglets !")


if __name__ == "__main__":
    asyncio.run(main())
