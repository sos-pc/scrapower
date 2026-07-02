"""Modal Harvester — auto-start Sandbox workers on Modal GPU.

Uses modal.Sandbox.create() to provision ephemeral workers.
Supports multiple accounts via MODAL_ACCOUNTS env var.
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from typing import Any

from ..accounts import Account
from .base import ProviderStatus, WorkerProvider

log = logging.getLogger(__name__)

COOLDOWN_SEC = 60
MAX_CONCURRENT = 3
GPU_TYPE = "T4"
SANDBOX_TIMEOUT = 6 * 3600
IDLE_TIMEOUT = 1800
BUDGET_MONTHLY_USD = 30.0


class ModalHarvester(WorkerProvider):
    provider_name = "modal"

    def __init__(
        self,
        account_ids: list[str],
        coordinator_url: str = "",
        api_key: str = "",
        budget_monthly_usd: float = BUDGET_MONTHLY_USD,
        gpu_type: str = GPU_TYPE,
        db_path: str = "",
    ):
        self._account_ids = account_ids
        self._coordinator_url = coordinator_url
        self._api_key = api_key
        self._gpu_type = gpu_type
        self._budget_monthly = budget_monthly_usd
        self._last_start: dict[str, float] = {}
        self._sandbox_ids: list[str] = []
        self._sandbox_tokens: dict[str, tuple[str, str]] = {}
        self._billing_cost_cached: float = 0.0
        self._billing_last_check: float = 0.0
        self._db_path = db_path
        self._clients: dict[str, Any] = {}
        if db_path:
            self._load_state()

    # ── WorkerProvider interface ──────────────────────────────

    async def refresh_quota(self, registry) -> None:
        """Update per-account billing from Modal API."""
        now = time.time()
        if now - self._billing_last_check <= 600:
            return  # cache still fresh
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            start = now_utc - datetime.timedelta(days=30)
            total_cost = 0.0
            per_account_cost: dict[str, float] = {}
            for aid in self._account_ids:
                account = registry.get(aid)
                if not account:
                    continue
                cost = await self._billing_for_account(account, start, now_utc)
                total_cost += cost
                per_account_cost[aid] = cost
                # Per-account remaining: budget - cost
                remaining = max(0.0, self._budget_monthly - cost)
                pct = remaining / self._budget_monthly * 100
                registry.update_quota(
                    aid, pct, {"cost_30d": round(cost, 2), "budget": self._budget_monthly}
                )
            self._billing_cost_cached = total_cost
            self._billing_last_check = now
            total_budget = self._budget_monthly * len(self._account_ids)
            used_pct = total_cost / total_budget * 100 if total_budget > 0 else 0
            log.info("modal billing: $%.2f / $%.0f (%.0f%%)", total_cost, total_budget, used_pct)
        except Exception:
            log.debug("modal billing refresh failed")

    async def launch_worker(self, account: Account) -> bool:
        """Create a Modal Sandbox on a specific account."""
        if account.id not in self._account_ids:
            return False
        last = self._last_start.get(account.id, 0)
        if time.time() - last < COOLDOWN_SEC:
            return False
        if len(self._sandbox_ids) >= MAX_CONCURRENT:
            return False
        try:
            sb = await self._create_sandbox(account)
            self._last_start[account.id] = time.time()
            self._sandbox_ids.append(sb.object_id)
            tid = account.credentials.get("token_id", "")
            tsec = account.credentials.get("token_secret", "")
            self._sandbox_tokens[sb.object_id] = (tid, tsec)
            log.info(
                "modal sandbox created: %s (gpu=%s, account=%s)",
                sb.object_id,
                self._gpu_type,
                account.id,
            )
            return True
        except Exception as e:
            log.error("modal sandbox creation failed: %s", str(e)[:200])
            return False

    async def cleanup_stale(self, registry) -> None:
        """Remove terminated sandboxes."""
        self._save_state()
        if not self._sandbox_ids:
            return
        try:
            import modal

            alive: set[str] = set()
            all_tokens = set(self._sandbox_tokens.values())
            if not all_tokens:
                tid = os.environ.get("MODAL_TOKEN_ID", "")
                tsec = os.environ.get("MODAL_TOKEN_SECRET", "")
                if tid and tsec:
                    all_tokens = {(tid, tsec)}
            for tid, tsec in all_tokens:
                try:
                    client = self._get_client(tid, tsec)
                    app = await modal.App.lookup.aio(
                        "scrapower", create_if_missing=False, client=client
                    )
                    async for sb_info in modal.Sandbox.list.aio(app_id=app.app_id, client=client):
                        alive.add(sb_info.object_id)
                except Exception:
                    continue
            before = len(self._sandbox_ids)
            self._sandbox_ids = [sid for sid in self._sandbox_ids if sid in alive]
            for sid in list(self._sandbox_tokens):
                if sid not in alive:
                    del self._sandbox_tokens[sid]
            removed = before - len(self._sandbox_ids)
            if removed:
                log.info(
                    "modal cleanup: removed %d terminated sandboxes (remaining: %d)",
                    removed,
                    len(self._sandbox_ids),
                )
            self._save_state()
        except Exception:
            log.debug("modal cleanup stale failed")

    async def status(self, registry) -> ProviderStatus:
        """Aggregate Modal status (indicative)."""
        accounts = [registry.get(aid) for aid in self._account_ids if registry.get(aid)]
        best_pct = max((a.remaining_pct for a in accounts if a.enabled), default=0.0)
        return ProviderStatus(
            name="modal",
            provider_type="modal",
            gpu_type=self._gpu_type,
            remaining_pct=best_pct,
            workers_active=len(self._sandbox_ids),
            quota_detail={
                "accounts": len(accounts),
                "budget_monthly_usd": self._budget_monthly,
                "cost_per_hour": {"T4": 0.59, "L4": 0.80, "A10": 1.10, "L40S": 1.95}.get(
                    self._gpu_type, 0.59
                ),
            },
        )

    # ── Internal ──────────────────────────────────────────────

    def _load_state(self) -> None:
        try:
            import sqlite3

            conn = sqlite3.connect(self._db_path)
            for key, attr, cast in [
                ("modal:billing_cost", "_billing_cost_cached", float),
                ("modal:billing_checked", "_billing_last_check", float),
            ]:
                row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
                if row:
                    setattr(self, attr, cast(row[0]))
            conn.close()
        except Exception:
            pass

    def _save_state(self) -> None:
        if not self._db_path:
            return
        try:
            import sqlite3

            conn = sqlite3.connect(self._db_path)
            conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")
            for key, val in [
                ("modal:billing_cost", str(self._billing_cost_cached)),
                ("modal:billing_checked", str(self._billing_last_check)),
            ]:
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)", (key, val)
                )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _get_client(self, token_id: str, token_secret: str):
        if token_id not in self._clients:
            import modal

            self._clients[token_id] = modal.Client.from_credentials(token_id, token_secret)
        return self._clients[token_id]

    async def _billing_for_account(self, account: Account, start, end) -> float:
        try:
            tid = account.credentials.get("token_id", "")
            tsec = account.credentials.get("token_secret", "")
            client = self._get_client(tid, tsec)
            import modal

            ws = modal.Workspace.from_context(client=client)
            report = await ws.billing.report.aio(start=start, end=end, resolution="d")
            return sum(float(item.cost) for item in report)
        except Exception:
            return 0.0

    async def _create_sandbox(self, account: Account):
        import modal

        tid = account.credentials.get("token_id", "")
        tsec = account.credentials.get("token_secret", "")
        client = self._get_client(tid, tsec)
        app = await modal.App.lookup.aio("scrapower", create_if_missing=True, client=client)
        worker_code = open("deploy/modal/worker.py").read()
        image = (
            modal.Image.from_registry("nvidia/cuda:12.4.0-runtime-ubuntu22.04", add_python="3.12")
            .apt_install("ffmpeg")
            .pip_install("aiohttp", "faster-whisper", "yt-dlp")
            .env({"HF_XET_HIGH_PERFORMANCE": "1"})
        )
        sb = await modal.Sandbox.create.aio(
            "python",
            "-c",
            worker_code,
            app=app,
            image=image,
            gpu=self._gpu_type,
            timeout=SANDBOX_TIMEOUT,
            idle_timeout=IDLE_TIMEOUT,
            cpu=4,
            memory=30720,
            client=client,
            secrets=[
                modal.Secret.from_dict(
                    {
                        "COORDINATOR_URL": self._coordinator_url,
                        "SCRAPOWER_API_KEY": self._api_key,
                        "WG_PROXY": "socks5://scrapower:"
                        + os.environ.get("SCRAPOWER_WG_PASS", "")
                        + "@"
                        + os.environ.get("SCRAPOWER_COORDINATOR_URL", "localhost").replace(
                            "https://", ""
                        )
                        + ":1081",
                    }
                )
            ],
        )
        return sb
