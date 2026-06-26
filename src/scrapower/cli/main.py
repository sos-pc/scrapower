"""Scrapower CLI — serve, submit."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import uuid
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser("scrapower", description="Distributed computing aggregator")
    sub = parser.add_subparsers(dest="command")

    # serve
    p = sub.add_parser("serve", help="Start the coordinator")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8777)
    p.add_argument("--data-dir", default="data")

    # submit
    p = sub.add_parser("submit", help="Submit a task")
    p.add_argument("--wasm", required=True, help="Path to .wasm file")
    p.add_argument("--input", required=True, help="Path to input file")
    p.add_argument("--coordinator", default="http://localhost:8777")
    p.add_argument("--runtime", default="wasm")
    p.add_argument("--client-id", default="cli")

    args = parser.parse_args()

    if args.command == "serve":
        asyncio.run(_serve(args))
    elif args.command == "submit":
        asyncio.run(_submit(args))
    else:
        parser.print_help()


async def _serve(args):
    import os

    import uvicorn

    os.environ.setdefault("SCRAPOWER_DATA_DIR", args.data_dir)
    os.environ.setdefault("SCRAPOWER_HOST", args.host)
    os.environ.setdefault("SCRAPOWER_PORT", str(args.port))

    from scrapower.coordinator.main import app

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    print(f"Coordinator running at http://{args.host}:{args.port}")
    await server.serve()


async def _submit(args):
    wasm_path = Path(args.wasm)
    input_path = Path(args.input)
    coord_url = args.coordinator.rstrip("/")

    wasm_data = wasm_path.read_bytes()
    input_data = input_path.read_bytes()
    task_id = uuid.uuid4().hex

    async with httpx.AsyncClient() as client:
        # Upload blobs
        r = await client.put(f"{coord_url}/blobs", content=wasm_data)
        exec_hash = r.json()["hash"]
        r = await client.put(f"{coord_url}/blobs", content=input_data)
        input_hash = r.json()["hash"]

        # Submit task
        r = await client.post(
            f"{coord_url}/tasks",
            json={
                "task_id": task_id,
                "client_id": args.client_id,
                "runtime": args.runtime,
                "executable_hash": exec_hash,
                "input_hash": input_hash,
            },
        )
        print(f"Task {task_id} submitted: {r.json()['status']}")

        # Wait for result
        print("Waiting for result...")
        for _ in range(60):  # ~5 minutes max
            await asyncio.sleep(5)
            r = await client.get(f"{coord_url}/tasks/{task_id}")
            status = r.json().get("status", "unknown")
            print(f"  Status: {status}")
            if status == "completed":
                # Get result
                r = await client.get(f"{coord_url}/results/{task_id}")
                result_data = r.content
                print(f"Result: {len(result_data)} bytes")
                print(f"Result hash: {hashlib.sha256(result_data).hexdigest()}")
                return
            elif status in ("failed", "cancelled"):
                print(f"Task ended with status: {status}")
                return

        print("Timeout waiting for result")


if __name__ == "__main__":
    main()
