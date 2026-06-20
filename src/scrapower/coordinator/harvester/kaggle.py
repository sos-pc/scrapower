"""Kaggle Harvester — auto-start workers when ANY tasks are waiting.

Uses kaggle CLI to create/run kernels on demand.
Supports multiple accounts via KAGGLE_ACCOUNTS env var.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time

log = logging.getLogger(__name__)

TICK_SEC = 30
COOLDOWN_SEC = 120
KAGGLE_BIN = "kaggle"


class KaggleHarvester:
    def __init__(
        self,
        accounts: list[dict],
        coordinator_url: str = "wss://scrapower.talos-int.com/worker/ws",
        api_key: str = "",
        notebook_template: str | None = None,
    ):
        self._accounts = accounts
        self._coordinator_url = coordinator_url
        self._api_key = api_key
        self._notebook_template = notebook_template or self._find_notebook()
        self._running = False
        self._round = 0
        self._last_start: float = 0

    @staticmethod
    def _find_notebook() -> str:
        for c in ["deploy/kaggle/sworker.ipynb", "../deploy/kaggle/sworker.ipynb"]:
            if os.path.exists(c):
                return c
        raise FileNotFoundError("Kaggle notebook template not found")

    async def run(self):
        self._running = True
        log.info("kaggle harvester: %d account(s), tick=%ds", len(self._accounts), TICK_SEC)
        while self._running:
            try:
                await self._tick()
            except Exception:
                log.exception("harvester tick")
            await asyncio.sleep(TICK_SEC)

    def stop(self):
        self._running = False

    async def _tick(self):
        if time.time() - self._last_start < COOLDOWN_SEC:
            return
        queued = await self._count_queued_tasks()
        log.debug("harvester tick: queued=%d", queued)
        if queued == 0:
            return
        log.info("harvester: %d queued tasks, starting kernel", queued)
        await self._start_kernel()

    async def _count_queued_tasks(self) -> int:
        try:
            import scrapower.coordinator.worker_gateway.router as rmod

            tm = getattr(rmod, "task_manager", None)
            if tm is None:
                return 0
            cursor = await tm._db.execute("SELECT COUNT(*) as n FROM tasks WHERE state = 'queued'")
            row = await cursor.fetchone()
            return row["n"] if row else 0
        except Exception:
            return 0

    async def _start_kernel(self):
        account = self._next_account()
        if not account:
            return

        username = account["username"]
        token = account["token"]
        ts = int(time.time())
        kernel_id = f"{username}/scrapower-auto-{ts}"
        kernel_title = f"Scrapower Auto {ts}"

        with open(self._notebook_template) as f:
            nb = json.load(f)

        for cell in nb.get("cells", []):
            if cell.get("cell_type") != "code":
                continue
            src = cell.get("source", "")
            if isinstance(src, list):
                src = "".join(src)
            # Replace coordinator URL in the notebook template.
            # Mode B uses HTTP (not WebSocket), e.g. https://scrapower.talos-int.com
            for pat in (
                "https://scrapower.talos-int.com",
                "wss://scrapower.talos-int.com/worker/ws",
            ):
                if pat in src:
                    src = src.replace(
                        f'COORDINATOR_URL = "{pat}"',
                        f'COORDINATOR_URL = "{self._coordinator_url}"',
                    )
                    break
            src = src.replace('API_KEY = ""', f'API_KEY = "{self._api_key}"')
            cell["source"] = src

        with tempfile.TemporaryDirectory() as tmp:
            nb_path = os.path.join(tmp, "notebook.ipynb")
            meta_path = os.path.join(tmp, "kernel-metadata.json")

            with open(nb_path, "w") as f:
                json.dump(nb, f)
            with open(meta_path, "w") as f:
                json.dump(
                    {
                        "id": kernel_id,
                        "title": kernel_title,
                        "code_file": "notebook.ipynb",
                        "language": "python",
                        "kernel_type": "notebook",
                        "is_private": False,
                        "enable_gpu": True,
                        "enable_internet": True,
                        "machine_shape": "NvidiaTeslaT4",
                    },
                    f,
                )

            env = os.environ.copy()
            env["KAGGLE_API_TOKEN"] = token

            proc = await asyncio.create_subprocess_exec(
                KAGGLE_BIN,
                "kernels",
                "push",
                "-p",
                tmp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            _, stderr = await proc.communicate()

            if proc.returncode == 0:
                self._last_start = time.time()
                log.info("kaggle kernel started: %s (account=%s)", kernel_id, username)
            else:
                log.error("kaggle push failed (account=%s): %s", username, stderr.decode()[:200])

    def _next_account(self) -> dict | None:
        if not self._accounts:
            return None
        a = self._accounts[self._round % len(self._accounts)]
        self._round += 1
        return a
