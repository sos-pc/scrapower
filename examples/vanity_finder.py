#!/usr/bin/env python3
"""
Distributed Vanity Hash Finder — démonstrateur de puissance Scrapower.

Cherche un nombre N dont le hash SHA-256 commence par un préfixe donné.
Divise l'espace de recherche en tranches, distribue aux workers,
agrège les résultats.

Usage:
    python examples/vanity_finder.py --prefix 0000 --workers 4
"""

import argparse
import asyncio
import hashlib
import struct
import time
import uuid

import httpx


async def submit_search(client, coord_url, api_key, seed, prefix, range_size):
    """Submit one search task to the coordinator."""
    task_id = f"vanity-{uuid.uuid4().hex[:12]}"
    input_data = struct.pack("<Q", seed) + prefix.encode()
    exec_data = f"# vanity search\nprefix='{prefix}'\nseed={seed}\nrange={range_size}".encode()

    h = {"X-API-Key": api_key}
    r = await client.put(f"{coord_url}/blobs", content=exec_data, headers=h)
    exec_hash = r.json()["hash"]
    r = await client.put(f"{coord_url}/blobs", content=input_data, headers=h)
    input_hash = r.json()["hash"]

    r = await client.post(
        f"{coord_url}/tasks",
        json={
            "task_id": task_id,
            "client_id": "vanity-finder",
            "runtime": "wasm",
            "executable_hash": exec_hash,
            "input_hash": input_hash,
        },
        headers=h,
    )
    return task_id


async def main():
    parser = argparse.ArgumentParser(description="Vanity Hash Finder")
    parser.add_argument("--coordinator", default="http://localhost:8777")
    parser.add_argument("--api-key", default="sp-secure-key-2026")
    parser.add_argument("--prefix", default="0000", help="Target hash prefix (more zeros = harder)")
    parser.add_argument("--range", type=int, default=2_000_000, help="Numbers to check per worker")
    parser.add_argument("--workers", type=int, default=4, help="Number of workers to use")
    args = parser.parse_args()

    coord_url = args.coordinator.rstrip("/")
    print(f"🔍 Searching for seed where SHA-256 starts with '{args.prefix}'")
    print(f"   Range: {args.range:,} numbers/worker × {args.workers} workers")
    print(f"   Total: {args.range * args.workers:,} hashes to check\n")

    start = time.time()

    async with httpx.AsyncClient() as client:
        # Submit tasks in parallel
        tasks = []
        for w in range(args.workers):
            seed = w * args.range
            tid = await submit_search(
                client, coord_url, args.api_key, seed, args.prefix, args.range
            )
            tasks.append((tid, seed))
            print(f"  Worker {w}: seed={seed:,}..{seed + args.range - 1:,} [{tid[:12]}]")

        print(f"\n  ⏳ Waiting for results...")

        # Wait for results
        found = None
        total_checked = 0
        h = {"X-API-Key": args.api_key, "X-Client-ID": "vanity-finder"}

        for attempt in range(120):  # 10 minutes max
            await asyncio.sleep(2)
            all_done = True
            for tid, seed in tasks:
                r = await client.get(f"{coord_url}/tasks/{tid}", headers=h)
                if r.status_code != 200:
                    continue
                data = r.json()
                status = data.get("status")
                if status == "completed":
                    r = await client.get(f"{coord_url}/results/{tid}", headers=h)
                    result = r.content
                    if result and len(result) >= 8:
                        found_seed = struct.unpack("<Q", result[:8])[0]
                        if found_seed != 0:
                            hh = hashlib.sha256(str(found_seed).encode()).hexdigest()
                            found = (found_seed, hh)
                            break
                    total_checked += args.range
                elif status not in ("completed", "failed", "cancelled"):
                    all_done = False

            if found or all_done:
                break

    elapsed = time.time() - start
    total = args.range * args.workers
    rate = total / elapsed if elapsed > 0 else 0

    print(f"\n{'=' * 50}")
    if found:
        seed, hh = found
        print(f"✅ FOUND!")
        print(f"   Seed: {seed}")
        print(f"   Hash: {hh}")
    else:
        print(f"❌ Not found in {total:,} attempts")

    print(f"\n📊 Stats:")
    print(f"   Time: {elapsed:.1f}s")
    print(f"   Rate: {rate:,.0f} hashes/s")
    print(f"   Workers used: {args.workers}")
    print(f"   Hashes checked: {total:,}")


if __name__ == "__main__":
    asyncio.run(main())
