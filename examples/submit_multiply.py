import os
"""Example: submit a WASM multiply task and get the result.

Usage: python examples/submit_multiply.py [--coordinator http://localhost:8777]
"""

import argparse
import asyncio
import struct
import sys, uuid
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from scrapower.worker.runtimes.wasm import compile_wat


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coordinator", default="http://localhost:8777")
    parser.add_argument("--a", type=int, default=6)
    parser.add_argument("--b", type=int, default=7)
    args = parser.parse_args()

    coord = args.coordinator.rstrip("/")

    # 1. Compile WAT → WASM
    wat_path = Path(__file__).parent / "multiply.wat"
    wasm = compile_wat(wat_path.read_text())
    print(f"WASM: {len(wasm)} bytes")

    # 2. Prepare input: two i32 values as bytes
    input_bytes = struct.pack("<ii", args.a, args.b)
    print(f"Input: {args.a} × {args.b}")

    async with aiohttp.ClientSession(headers={"X-API-Key": os.environ.get("SCRAPOWER_API_KEY", "your-api-key")}) as session:
        # 3. Upload WASM
        async with session.put(f"{coord}/blobs", data=wasm) as r:
            wasm_hash = (await r.json())["hash"]

        # 4. Upload input
        async with session.put(f"{coord}/blobs", data=input_bytes) as r:
            input_hash = (await r.json())["hash"]

        # 5. Submit task
        async with session.post(
            f"{coord}/tasks",
            json={
                "task_id": f"multiply-{args.a}x{args.b}-{uuid.uuid4().hex[:6]}",
                "client_id": "demo",
                "runtime": "wasm",
                "executable_hash": wasm_hash,
                "input_hash": input_hash,
            },
        ) as r:
            print(f"Task: {(await r.json())['status']}")

        # 6. Wait for result
        print("Waiting...")
        for _ in range(30):
            await asyncio.sleep(2)
            async with session.get(f"{coord}/tasks/multiply-{args.a}x{args.b}-{uuid.uuid4().hex[:6]}") as r:
                status = (await r.json()).get("status", "unknown")
            if status == "validated":
                async with session.get(f"{coord}/results/multiply-{args.a}x{args.b}-{uuid.uuid4().hex[:6]}") as r:
                    data = await r.read()
                result = struct.unpack("<i", data[:4])[0]
                print(f"\n✅ {args.a} × {args.b} = {result}")
                return
            elif status in ("failed", "cancelled"):
                print(f"❌ {status}")
                return

        print("Timeout")


if __name__ == "__main__":
    asyncio.run(main())
