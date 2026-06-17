import os
"""Estimate π via Monte Carlo on Scrapower.

Usage: python examples/estimate_pi.py [--coordinator http://localhost:8777] [--tasks 10]
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from scrapower.worker.runtimes.wasm import compile_wat


async def main():
    parser = argparse.ArgumentParser(description="Estimate π via Monte Carlo")
    parser.add_argument("--coordinator", default="http://localhost:8777")
    parser.add_argument("--tasks", type=int, default=10)
    args = parser.parse_args()

    coord = args.coordinator.rstrip("/")

    wat_path = Path(__file__).parent / "monte_carlo_pi.wat"
    wasm_bytes = compile_wat(wat_path.read_text())
    print(f"WASM compiled: {len(wasm_bytes)} bytes")

    async with aiohttp.ClientSession(headers={"X-API-Key": os.environ.get("SCRAPOWER_API_KEY", "your-api-key")}) as session:
        # Upload WASM once
        async with session.put(f"{coord}/blobs", data=wasm_bytes) as r:
            exec_hash = (await r.json())["hash"]

        # Submit tasks with different seeds
        task_ids = []
        total_inside = 0
        total_points = 0

        for i in range(args.tasks):
            seed = 42 + i * 1000
            input_bytes = struct.pack("<Q", seed)
            async with session.put(f"{coord}/blobs", data=input_bytes) as r:
                input_hash = (await r.json())["hash"]

            task_id = f"pi-{i:04d}"
            async with session.post(
                f"{coord}/tasks",
                json={
                    "task_id": task_id,
                    "client_id": "pi-estimator",
                    "runtime": "wasm",
                    "executable_hash": exec_hash,
                    "input_hash": input_hash,
                },
            ) as r:
                await r.json()
            task_ids.append(task_id)

        print(f"Submitted {args.tasks} tasks. Waiting for results...")

        for task_id in task_ids:
            for _ in range(30):
                await asyncio.sleep(5)
                async with session.get(f"{coord}/tasks/{task_id}") as r:
                    task_json = await r.json()
                    status = task_json.get("status", "unknown")
                if status == "completed":
                    async with session.get(f"{coord}/results/{task_id}") as r:
                        data = await r.read()
                    inside = struct.unpack("<Q", data[:8])[0]
                    total = struct.unpack("<Q", data[8:16])[0]
                    total_inside += inside
                    total_points += total
                    pi_est = 4 * inside / total if total else 0
                    print(f"  {task_id}: {inside}/{total} -> {pi_est:.6f}")
                    break
                elif status in ("failed", "cancelled"):
                    print(f"  {task_id}: {status}")
                    break

        if total_points > 0:
            pi_estimate = 4 * total_inside / total_points
            print(f"\nπ ≈ {pi_estimate:.10f}  ({total_points:,} points, {len(task_ids)} tasks)")
            error = abs(pi_estimate - 3.141592653589793)
            print(f"Error: {error:.10f}")
        else:
            print("No results collected.")


if __name__ == "__main__":
    asyncio.run(main())
