"""Kaggle Harvester — auto-start workers when tasks are waiting.

Uses kaggle CLI to create/run kernels on demand.
Supports multiple accounts via KAGGLE_ACCOUNTS env var.
Implements WorkerProvider for EphemeralHarvester.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time

from ..accounts import Account
from .base import ProviderStatus, WorkerProvider

log = logging.getLogger(__name__)

COOLDOWN_SEC = 60
KAGGLE_BIN = "kaggle"


class KaggleHarvester(WorkerProvider):
    provider_name = "kaggle"

    def __init__(
        self,
        account_ids: list[str],
        coordinator_url: str = "",
        api_key: str = "",
        notebook_template: str | None = None,
    ):
        self._account_ids = account_ids
        self._coordinator_url = coordinator_url
        self._api_key = api_key
        self._notebook_template = notebook_template or self._find_notebook()
        self._last_start: dict[str, float] = {}
        self._last_cleanup: float = 0
        self._kernel_refs: list[str] = []

    @staticmethod
    def _find_notebook() -> str:
        for c in ["deploy/kaggle/sworker.ipynb", "../deploy/kaggle/sworker.ipynb"]:
            if os.path.exists(c):
                return c
        raise FileNotFoundError("Kaggle notebook template not found")

    # ── WorkerProvider interface ──────────────────────────────

    async def refresh_quota(self, registry) -> None:
        """Update per-account quota in registry from Kaggle API."""
        for aid in self._account_ids:
            account = registry.get(aid)
            if not account or not account.enabled:
                continue
            q = await self._get_quota_for(account)
            if q:
                pct = min(100.0, q["remaining_h"] / q["total_h"] * 100)
                registry.update_quota(aid, pct, q)

    async def launch_worker(self, account: Account) -> bool:
        """Launch a Kaggle kernel on a specific account."""
        if account.id not in self._account_ids:
            return False
        last = self._last_start.get(account.id, 0)
        if time.time() - last < COOLDOWN_SEC:
            log.info(
                "kaggle cooldown for %s (%.0fs remaining)",
                account.id,
                COOLDOWN_SEC - (time.time() - last),
            )
            return False
        ok = await self._start_kernel(account)
        if ok:
            self._last_start[account.id] = time.time()
        return ok

    async def cleanup_stale(self, registry) -> None:
        """Delete dead kernels and sync local tracking."""
        await self._cleanup_old_kernels(registry)
        actual = await self._count_active_kernels(registry)
        if len(self._kernel_refs) > actual:
            self._kernel_refs = self._kernel_refs[-actual:] if actual > 0 else []

    async def status(self, registry) -> ProviderStatus:
        """Aggregate Kaggle status (indicative)."""
        accounts = [registry.get(aid) for aid in self._account_ids if registry.get(aid)]
        best_pct = max((a.remaining_pct for a in accounts if a.enabled), default=0.0)
        return ProviderStatus(
            name="kaggle",
            provider_type="kaggle",
            gpu_type="T4",
            remaining_pct=best_pct,
            workers_active=len(self._kernel_refs),
            quota_detail={"accounts": len(accounts)},
        )

    # ── Internal ──────────────────────────────────────────────

    async def _get_quota_for(self, account: Account) -> dict | None:
        try:
            env = os.environ.copy()
            env["KAGGLE_API_TOKEN"] = account.credentials["token"]
            proc = await asyncio.create_subprocess_exec(
                KAGGLE_BIN,
                "quota",
                "--csv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode != 0:
                return None
            for line in stdout.decode().strip().split("\n")[1:]:
                parts = line.split(",")
                if len(parts) >= 4 and parts[0] == "GPU":
                    return {
                        "used_h": float(parts[1].rstrip("h")),
                        "remaining_h": float(parts[2].rstrip("h")),
                        "total_h": float(parts[3].rstrip("h")),
                    }
        except Exception:
            log.debug("kaggle quota check failed for %s", account.id)
            return None

    async def _start_kernel(self, account: Account) -> bool:
        username = account.credentials.get("username", "")
        token = account.credentials.get("token", "")
        ts = int(time.time())
        kernel_id = f"{username}/scrapower-auto-{ts}"

        with open(self._notebook_template) as f:
            nb = json.load(f)

        for cell in nb.get("cells", []):
            if cell.get("cell_type") != "code":
                continue
            src = cell.get("source", "")
            if isinstance(src, list):
                src = "".join(src)
            src = src.replace("{{COORDINATOR_URL}}", self._coordinator_url)
            src = src.replace("{{API_KEY}}", self._api_key)
            wg_proxy = os.environ.get("SCRAPOWER_WG_PROXY_PUBLIC", "") or os.environ.get(
                "SCRAPOWER_WG_PROXY", ""
            )
            if wg_proxy:
                try:
                    rest = wg_proxy.split("://", 1)[1]
                    auth, host_port = rest.split("@", 1)
                    user, passwd = auth.split(":", 1)
                    host = host_port.rsplit(":", 1)[0]
                except (ValueError, IndexError):
                    user, passwd, host = "", "", ""
                src = src.replace("{{WG_USER}}", user)
                src = src.replace("{{WG_PASS}}", passwd)
                src = src.replace("{{WG_HOST}}", host)
            else:
                src = (
                    src.replace("{{WG_USER}}", "")
                    .replace("{{WG_PASS}}", "")
                    .replace("{{WG_HOST}}", "")
                )
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
                        "title": f"Scrapower Auto {ts}",
                        "code_file": "notebook.ipynb",
                        "language": "python",
                        "kernel_type": "notebook",
                        "is_private": True,
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
                log.info("kaggle kernel started: %s (account=%s)", kernel_id, username)
                self._kernel_refs.append(kernel_id)
                return True
            else:
                log.error("kaggle push failed (account=%s): %s", username, stderr.decode()[:200])
                return False

    async def _cleanup_old_kernels(self, registry) -> None:
        now = time.time()
        if now - self._last_cleanup < 300:
            return
        self._last_cleanup = now
        cleaned = 0
        for account in registry.by_provider("kaggle"):
            try:
                env = os.environ.copy()
                env["KAGGLE_API_TOKEN"] = account.credentials["token"]
                proc = await asyncio.create_subprocess_exec(
                    KAGGLE_BIN,
                    "kernels",
                    "list",
                    "-m",
                    "--csv",
                    "--page-size",
                    "30",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode != 0:
                    continue
                for line in stdout.decode().strip().split("\n")[1:]:
                    parts = line.split(",")
                    if len(parts) < 2 or "scrapower-auto" not in parts[0]:
                        continue
                    ref = parts[0]
                    should_delete = False
                    sproc = await asyncio.create_subprocess_exec(
                        KAGGLE_BIN,
                        "kernels",
                        "status",
                        ref,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env,
                    )
                    sout, _ = await sproc.communicate()
                    status_str = sout.decode()
                    if "COMPLETE" in status_str or "ERROR" in status_str:
                        should_delete = True
                    elif "RUNNING" in status_str:
                        try:
                            last_run = parts[3] if len(parts) > 3 else ""
                            if last_run:
                                from datetime import UTC, datetime

                                run_dt = datetime.strptime(
                                    last_run.strip(), "%Y-%m-%d %H:%M:%S.%f"
                                ).replace(tzinfo=UTC)
                                if (now - run_dt.timestamp()) > 3600:
                                    should_delete = True
                        except (ValueError, IndexError):
                            pass
                    if should_delete:
                        dproc = await asyncio.create_subprocess_exec(
                            KAGGLE_BIN,
                            "kernels",
                            "delete",
                            ref,
                            "--yes",
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=env,
                        )
                        await dproc.communicate()
                        if dproc.returncode == 0:
                            log.info("harvester cleanup: deleted %s", ref)
                            cleaned += 1
                            if ref in self._kernel_refs:
                                self._kernel_refs.remove(ref)
            except Exception:
                log.debug("kaggle cleanup failed for account %s", account.id)
        if cleaned:
            log.info("harvester cleanup: deleted %d kernels", cleaned)

    async def _count_active_kernels(self, registry) -> int:
        active = 0
        for account in registry.by_provider("kaggle"):
            try:
                env = os.environ.copy()
                env["KAGGLE_API_TOKEN"] = account.credentials["token"]
                proc = await asyncio.create_subprocess_exec(
                    KAGGLE_BIN,
                    "kernels",
                    "list",
                    "-m",
                    "--csv",
                    "--page-size",
                    "5",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    for line in stdout.decode().strip().split("\n")[1:]:
                        if "scrapower-auto" in line and "RUNNING" in line:
                            active += 1
            except Exception:
                log.debug("kaggle count active failed for %s", account.id)
            return active
