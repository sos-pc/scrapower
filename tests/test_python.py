"""Test Python runtime — exécute du Python dans les navigateurs via Pyodide.

Usage:
    python tests/test_python.py --url http://localhost:8777 --api-key KEY
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

PYTHON_CODE = """
import sys
import math
print(f"Python {sys.version}")
print(f"pi approximate = {math.pi:.10f}")
# Calcul démo
result = sum(i*i for i in range(1000))
print(f"sum of squares 0..999 = {result}")
print("DONE")
"""


async def main():
    parser = argparse.ArgumentParser(description="Test Python runtime via Pyodide")
    parser.add_argument("--url", default="http://localhost:8777", help="Coordinator URL")
    parser.add_argument("--api-key", required=True, help="API key")
    parser.add_argument("--count", type=int, default=2, help="Number of tasks")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    headers = {"X-API-Key": args.api_key}

    async with aiohttp.ClientSession(headers=headers) as session:
        # Upload Python source as blob
        source = PYTHON_CODE.encode()
        async with session.put(f"{base_url}/blobs", data=source) as r:
            r.raise_for_status()
            input_hash = (await r.json())["hash"]
        print(f"Python source uploaded: {input_hash[:16]}...")

        # Dummy executable (not used by Python runtime)
        dummy = b"\x00asm\x01\x00\x00\x00"
        async with session.put(f"{base_url}/blobs", data=dummy) as r:
            exec_hash = (await r.json())["hash"]

        # Submit Python tasks
        task_ids = []
        print(f"\nSoumission de {args.count} tâches Python...")
        for i in range(args.count):
            tid = uuid.uuid4().hex
            async with session.post(
                f"{base_url}/tasks",
                json={
                    "task_id": tid,
                    "client_id": "python-test",
                    "runtime": "python",
                    "executable_hash": exec_hash,
                    "input_hash": input_hash,
                },
            ) as r:
                r.raise_for_status()
            task_ids.append(tid)
        print("  OK")

        # Wait for completion
        print("\nAttente...")
        start = time.time()
        completed = {}
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
            await asyncio.sleep(0.5)

        elapsed = time.time() - start
        print(f"\n  {len(completed)}/{args.count} completed en {elapsed:.1f}s\n")

        # Retrieve and display results
        for tid, info in completed.items():
            wid = info.get("assigned_worker_id", "?")
            print(f"  [{wid}] {tid[:12]}... ", end="")
            async with session.get(f"{base_url}/results/{tid}") as r:
                if r.status == 200:
                    result = (await r.read()).decode()
                    first_line = result.strip().split("\n")[0] if result.strip() else "(empty)"
                    print(first_line)
                else:
                    print(f"status={info['status']}")


if __name__ == "__main__":
    asyncio.run(main())
