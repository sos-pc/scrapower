"""Standalone worker entry point — called by the harvester local provider."""

from __future__ import annotations

import argparse
import asyncio
import sys

sys.path.insert(0, ".")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coordinator", default="ws://localhost:8777/worker/ws")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--runtimes", default="wasm")
    args = parser.parse_args()

    from scrapower.worker.client import WorkerClient

    runtimes = args.runtimes.split(",")
    worker = WorkerClient(args.coordinator, worker_id=args.worker_id, runtimes=runtimes)
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
