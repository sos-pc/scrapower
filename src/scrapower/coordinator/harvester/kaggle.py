"""Kaggle Harvester — auto-start workers when ANY tasks are waiting.

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

from .base import ProviderStatus, WorkerProvider

log = logging.getLogger(__name__)

COOLDOWN_SEC = 60  # minimum seconds between kernel pushes per account
KAGGLE_BIN = "kaggle"


class KaggleHarvester(WorkerProvider):
    def __init__(
        self,
        accounts: list[dict],
        coordinator_url: str = "wss://your-coordinator.example.com/worker/ws",
        api_key: str = "",
        notebook_template: str | None = None,
    ):
        self._accounts = accounts
        self._coordinator_url = coordinator_url
        self._api_key = api_key
        self._notebook_template = notebook_template or self._find_notebook()
        self._running = False
        self._round = 0
        self._last_start: dict[str, float] = {}  # token_id -> timestamp
        self._current_cooldown: float = COOLDOWN_SEC
        self._last_cleanup: float = 0
        self._kernel_refs: list[str] = []  # kernel IDs we've launched

    @staticmethod
    def _find_notebook() -> str:
        for c in ["deploy/kaggle/sworker.ipynb", "../deploy/kaggle/sworker.ipynb"]:
            if os.path.exists(c):
                return c
        raise FileNotFoundError("Kaggle notebook template not found")

    async def _get_quota(self) -> dict | None:
        """Get GPU quota for the next account (peek, don't consume)."""
        if not self._accounts:
            return None
        account = self._accounts[self._round % len(self._accounts)]
        try:
            env = os.environ.copy()
            env["KAGGLE_API_TOKEN"] = account["token"]
            proc = await asyncio.create_subprocess_exec(
                "kaggle",
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
                        "username": account["username"],
                        "used_h": float(parts[1].rstrip("h")),
                        "remaining_h": float(parts[2].rstrip("h")),
                        "total_h": float(parts[3].rstrip("h")),
                    }
        except Exception:
            pass
        return None

    async def _cleanup_old_kernels(self):
        """Delete dead/orphaned kernels.

        Handles:
        - COMPLETE/ERROR: normal completion
        - RUNNING > 1h: stuck (notebook has 5min idle timeout)
        """
        now = time.time()
        if now - self._last_cleanup < 300:
            return
        self._last_cleanup = now

        cleaned = 0
        for account in self._accounts:
            try:
                env = os.environ.copy()
                env["KAGGLE_API_TOKEN"] = account["token"]
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
                    if len(parts) < 2:
                        continue
                    ref = parts[0]
                    if "scrapower-auto" not in ref:
                        continue
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
                        # RUNNING > 1h = stuck (notebook idle timeout is 5min)
                        try:
                            last_run = parts[3] if len(parts) > 3 else ""
                            if last_run:
                                from datetime import UTC, datetime

                                run_dt = datetime.strptime(
                                    last_run.strip(), "%Y-%m-%d %H:%M:%S.%f"
                                ).replace(tzinfo=UTC)
                                if (now - run_dt.timestamp()) > 3600:
                                    should_delete = True
                                    log.info(
                                        "harvester: killing stuck kernel %s (running >1h)", ref
                                    )
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
            except Exception:
                pass
        if cleaned:
            log.info("harvester cleanup: deleted %d kernels", cleaned)
        else:
            log.debug("harvester cleanup: nothing to clean")

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
            # Mode B uses HTTP (not WebSocket), e.g. https://your-coordinator.example.com
            for pat in (
                "https://your-coordinator.example.com",
                "wss://your-coordinator.example.com/worker/ws",
            ):
                if pat in src:
                    src = src.replace(
                        f'COORDINATOR_URL = "{pat}"',
                        f'COORDINATOR_URL = "{self._coordinator_url}"',
                    )
                    break
            src = src.replace('API_KEY = ""', f'API_KEY = "{self._api_key}"')
            # Workers need the PUBLIC proxy URL (your-coordinator.example.com:1081),
            # not the coordinator's localhost alias. The public URL is reachable
            # from Kaggle/Modal servers; localhost is only for coordinator fallback.
            wg_proxy = os.environ.get("SCRAPOWER_WG_PROXY_PUBLIC", "") or os.environ.get(
                "SCRAPOWER_WG_PROXY", ""
            )
            if wg_proxy:
                # Never put the full proxy URL (with password) in the notebook source.
                # Inject components separately, assembled at runtime by the worker.
                try:
                    rest = wg_proxy.split("://", 1)[1]
                    auth, host_port = rest.split("@", 1)
                    user, passwd = auth.split(":", 1)
                    host = host_port.rsplit(":", 1)[0]
                except (ValueError, IndexError):
                    user, passwd, host = "scrapower", "", "your-coordinator.example.com"
                src = src.replace('WG_USER = ""', f'WG_USER = "{user}"')
                src = src.replace('WG_PASS = ""', f'WG_PASS = "{passwd}"')
                src = src.replace('WG_HOST = ""', f'WG_HOST = "{host}"')

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

    def _next_account(self) -> dict | None:
        if not self._accounts:
            return None
        a = self._accounts[self._round % len(self._accounts)]
        self._round += 1
        return a

    # ── WorkerProvider interface ──────────────────────────────

    async def remaining_pct(self) -> float:
        """Quota du MEILLEUR compte (pas la moyenne).

        Le harvester doit savoir si au moins un compte peut lancer un worker.
        Retourner la moyenne masquerait un compte épuisé derrière un compte plein.
        """
        if not self._accounts:
            return 0.0
        best = 0.0
        for account in self._accounts:
            q = await self._get_quota_for(account)
            if q:
                best = max(best, min(100.0, q["remaining_h"] / q["total_h"] * 100))
        return best

    async def has_quota(self) -> bool:
        """Au moins un compte a > 0.1h restantes."""
        return await self.remaining_pct() > 0.3  # 0.1h / 30h ≈ 0.3%

    async def launch_worker(self) -> bool:
        """Lance un kernel Kaggle. Avec rate-limit per-compte et max concurrent."""
        # Peek at next account to check per-account cooldown
        if self._accounts:
            next_idx = self._round % len(self._accounts)
            next_tid = self._accounts[next_idx].get("token_id", "default")
            next_name = self._accounts[next_idx].get("username", "?")
        else:
            return False
        last = self._last_start.get(next_tid, 0)
        if time.time() - last < COOLDOWN_SEC:
            log.info(
                "kaggle cooldown for %s (%.0fs remaining)",
                next_name,
                COOLDOWN_SEC - (time.time() - last),
            )
            return False
        if len(self._kernel_refs) >= 3:
            log.info("kaggle max concurrent reached (%d/3)", len(self._kernel_refs))
            return False
        ok = await self._start_kernel()
        if ok:
            self._last_start[next_tid] = time.time()
        return ok

    async def cleanup_stale(self) -> None:
        """Nettoie les kernels morts + synchro du tracking local."""
        await self._cleanup_old_kernels()
        # Sync _kernel_refs with reality: remove entries for deleted kernels
        # _cleanup_old_kernels handles the actual deletion; we just need to
        # know how many are still alive. Use _count_active_kernels as ground truth.
        actual = await self._count_active_kernels()
        if len(self._kernel_refs) > actual:
            # Some kernels died without us tracking the deletion;
            # trim the list to match reality (oldest first)
            self._kernel_refs = self._kernel_refs[-actual:] if actual > 0 else []

    async def _count_active_kernels(self) -> int:
        """Count RUNNING scrapower-auto kernels across all accounts."""
        active = 0
        for account in self._accounts:
            try:
                env = os.environ.copy()
                env["KAGGLE_API_TOKEN"] = account["token"]
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
                pass
        return active

    async def status(self) -> ProviderStatus:
        """Statut agregé de tous les comptes Kaggle."""
        pct = await self.remaining_pct()
        return ProviderStatus(
            name="kaggle",
            provider_type="kaggle",
            gpu_type="T4",
            remaining_pct=pct,
            workers_active=len(self._kernel_refs),
            quota_detail={"accounts": len(self._accounts)},
        )

    async def _get_quota_for(self, account: dict) -> dict | None:
        """Get GPU quota for a specific account."""
        try:
            env = os.environ.copy()
            env["KAGGLE_API_TOKEN"] = account["token"]
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
            pass
        return None
