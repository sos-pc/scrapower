"""Local provider — launches native workers as subprocesses."""

from __future__ import annotations

import asyncio
import logging

from .base import Provider

log = logging.getLogger(__name__)


class LocalProvider(Provider):
    """Launches native Python workers as subprocesses."""

    def __init__(
        self,
        coordinator_url: str,
        name: str = "local",
        count: int = 1,
        runtimes: list[str] | None = None,
        cooldown_seconds: int = 10,
    ):
        super().__init__(coordinator_url, name, cooldown_seconds)
        self._count = count
        self._runtimes = runtimes or ["wasm"]
        self._processes: list[asyncio.subprocess.Process] = []

    @classmethod
    def create(cls, coordinator_url: str, name: str, config: dict) -> LocalProvider:
        return cls(
            coordinator_url,
            name,
            count=config.get("count", 1),
            runtimes=config.get("runtimes", ["wasm"]),
            cooldown_seconds=config.get("cooldown_seconds", 10),
        )

    async def start(self):
        self._running = True

        for i in range(self._count):
            worker_id = f"{self._name}-{i}"
            cmd = [
                "python",
                "-m",
                "scrapower.cli.worker_standalone",
                "--coordinator",
                self._coordinator_url,
                "--worker-id",
                worker_id,
                "--runtimes",
                ",".join(self._runtimes),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._processes.append(proc)
            log.info("local worker started: %s", worker_id)

        while self._running:
            for proc in self._processes:
                if proc.returncode is not None:
                    log.warning("local worker exited, returncode=%s", proc.returncode)
            await asyncio.sleep(10)

    def stop(self):
        super().stop()
        for proc in self._processes:
            proc.terminate()
