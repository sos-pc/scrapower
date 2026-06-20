"""Embedded worker — runs inside the coordinator process.

Ensures the system is always functional even without external workers.
Priority is low: only takes tasks when no external workers are available,
or when Oracle needs keepalive CPU usage.
"""

from __future__ import annotations

import asyncio
import logging

from ..worker.client import WorkerClient
from ..worker.sandbox import Sandbox

log = logging.getLogger(__name__)


class EmbeddedWorker:
    """Worker that runs in the coordinator process."""

    def __init__(self, coordinator_url: str, sandbox: Sandbox):
        self._url = coordinator_url
        self._sandbox = sandbox
        self._client: WorkerClient | None = None
        self._running = False

    async def start(self):
        self._running = True
        while self._running:
            try:
                if self._client:
                    await self._client.disconnect()

                # Wait for server to be ready before connecting
                await self._wait_for_server()

                self._client = WorkerClient(
                    self._url,
                    worker_id="_embedded",
                    runtimes=["wasm", "python"],
                    sandbox=self._sandbox,
                )
                await self._client.connect()
                log.info("embedded worker connected")
                await self._client.run()
            except Exception:
                log.exception("embedded worker disconnected, reconnecting in 5s")
            await asyncio.sleep(5)

    async def _wait_for_server(self, timeout: int = 30):
        """Wait until the coordinator health endpoint responds."""
        import aiohttp

        for i in range(timeout):
            try:
                async with aiohttp.ClientSession() as s:
                    health_url = self._url.replace("/worker/ws", "/health").replace(
                        "ws://", "http://"
                    )
                    async with s.get(health_url) as r:
                        if r.status == 200:
                            return
            except Exception:
                pass
            await asyncio.sleep(1)
        log.warning("embedded worker: server not ready after %ds, connecting anyway", timeout)

    def stop(self):
        self._running = False
