"""Modal Harvester — auto-start Sandbox workers on Modal GPU.

Uses modal.Sandbox.create() to provision ephemeral workers.
Supports multiple accounts via MODAL_ACCOUNTS env var.
Authentication via MODAL_TOKEN_ID/MODAL_TOKEN_SECRET (single account)
or MODAL_ACCOUNTS JSON array for multi-account.
"""

from __future__ import annotations

import logging
import os
import time

from .base import ProviderStatus, WorkerProvider

log = logging.getLogger(__name__)

COOLDOWN_SEC = 60  # minimum seconds between sandbox creations
MAX_CONCURRENT = 3  # max simultaneous sandboxes per provider
GPU_TYPE = "T4"  # default GPU — $0.59/h on Modal Starter
GPU_VRAM_MB = 16384
SANDBOX_TIMEOUT = 6 * 3600  # 6h max per sandbox
IDLE_TIMEOUT = 1800  # 30 min idle → terminate (covers long whisper + upload retries)
BUDGET_MONTHLY_USD = 30.0  # Modal Starter free credits per account


class ModalHarvester(WorkerProvider):
    """Provisionne des Sandboxes Modal avec GPU. Supporte le multi-comptes."""

    def __init__(
        self,
        accounts: list[dict],
        coordinator_url: str = "",
        api_key: str = "",
        budget_monthly_usd: float = BUDGET_MONTHLY_USD,
        gpu_type: str = GPU_TYPE,
        db_path: str = "",
        provider_enabled: bool = True,
    ):
        from ..accounts import AccountFilter

        self._accounts = AccountFilter(accounts, provider_enabled=provider_enabled)
        self._coordinator_url = coordinator_url
        self._api_key = api_key
        self._gpu_type = gpu_type
        self._budget_monthly = budget_monthly_usd
        self._last_start: dict[str, float] = {}  # token_id -> timestamp
        self._round = 0
        self._sandbox_ids: list[str] = []
        self._sandbox_tokens: dict[str, tuple[str, str]] = {}  # sb_id -> (token_id, token_secret)
        self._running = False
        # Billing cache (refreshed from modal.billing API every 10 min)
        self._billing_cost_cached: float = 0.0
        self._billing_last_check: float = 0.0
        # Persist tracking across coordinator restarts
        self._db_path = db_path
        if db_path:
            self._load_state()
        # Modal Clients per account (avoids os.environ mutation for thread safety)
        self._clients: dict[str, Any] = {}  # token_id → modal.Client (lazy)

    # ── WorkerProvider interface ──────────────────────────────

    # State persistence (survives coordinator restart)

    def _load_state(self) -> None:
        """Restore tracking from DB (survives coordinator restart)."""
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
            pass  # DB might not exist yet - will save on next cleanup

    def _save_state(self) -> None:
        """Persist tracking to DB (called after each cleanup cycle)."""
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
                    "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                    (key, val),
                )
            conn.commit()
            conn.close()
        except Exception:
            pass  # best-effort - DB might be locked

    async def remaining_pct(self) -> float:
        """Budget restant (0-100). Single source of truth: modal.billing API.

        Calls billing for ALL accounts every 10 min. Adds a small margin
        per active sandbox (API has collection delay) -- one sandbox hour
        at worst = $0.59, or ~2% of the monthly budget. Simple and reliable.
        """
        if not self._accounts:
            return 0.0

        now = time.time()
        if now - self._billing_last_check > 600:
            try:
                import datetime

                now_utc = datetime.datetime.now(datetime.timezone.utc)
                start = now_utc - datetime.timedelta(days=30)
                total_cost = 0.0
                for account in self._accounts.all:
                    cost = await self._billing_for_account(account, start, now_utc)
                    total_cost += cost
                self._billing_cost_cached = total_cost
                self._billing_last_check = now
                log.debug("modal billing: $%.4f total (all accounts)", total_cost)
            except Exception:
                pass  # keep cached value

        # Per-account: best remaining budget, with sandbox margin
        best_pct = 0.0
        for account in self._accounts.all:
            tid = account.get("token_id", "")
            # Count active sandboxes for this account
            active = sum(
                1
                for sid, (tok_id, _) in self._sandbox_tokens.items()
                if tok_id == tid and sid in self._sandbox_ids
            )
            # Margin: 1 sandbox-hour = $0.59 = ~2% of $30
            margin = active * 2.0
            account_pct = max(0.0, 100.0 - margin)
            best_pct = max(best_pct, account_pct)

        # Floor by billing API (actual cost, never lower)
        if self._billing_cost_cached > 0:
            total_budget = self._budget_monthly * len(self._accounts.all)
            billed_pct = max(0, total_budget - self._billing_cost_cached) / total_budget * 100
            best_pct = min(best_pct, billed_pct)
        return best_pct

    async def _billing_for_account(self, account: dict, start, end) -> float:
        """Call modal billing API for a single account. Returns total cost."""
        import modal

        tid = account.get("token_id", "")
        tsec = account.get("token_secret", "")
        client = self._get_client(tid, tsec)
        try:
            ws = modal.Workspace.from_context(client=client)
            report = await ws.billing.report.aio(start=start, end=end, resolution="d")
            return sum(float(item.get("cost", 0)) for item in report)
        except Exception:
            return 0.0

    async def has_quota(self) -> bool:
        """Budget restant > 1%."""
        return await self.remaining_pct() > 1.0

    async def launch_worker(self) -> bool:
        """Create a Modal Sandbox with GPU T4. Per-account cooldown."""
        # Peek at next account to check per-account cooldown
        next_account = self._accounts.get(self._round)
        if next_account:
            next_tid = next_account.get("token_id", "default")
        else:
            log.info("modal no enabled accounts")
            return False
        last = self._last_start.get(next_tid, 0)
        if time.time() - last < COOLDOWN_SEC:
            log.info(
                "modal cooldown for %s (%.0fs remaining)",
                next_tid[:12],
                COOLDOWN_SEC - (time.time() - last),
            )
            return False
        if len(self._sandbox_ids) >= MAX_CONCURRENT:
            log.info("modal max concurrent reached (%d/%d)", len(self._sandbox_ids), MAX_CONCURRENT)
            return False

        try:
            sb = await self._create_sandbox()
            self._last_start[next_tid] = time.time()
            self._sandbox_ids.append(sb.object_id)
            # Track which account's token created this sandbox
            account = self._accounts.enabled[(self._round - 1) % len(self._accounts.enabled)]
            self._sandbox_tokens[sb.object_id] = (
                account["token_id"],
                account["token_secret"],
            )
            log.info("modal sandbox created: %s (gpu=%s)", sb.object_id, self._gpu_type)
            return True
        except Exception as e:
            log.error("modal sandbox creation failed: %s", str(e)[:200])
            return False

    async def cleanup_stale(self) -> None:
        """Remove terminated sandboxes from local tracking list.

        Iterates ALL account tokens to find sandboxes — Modal's API is
        scoped per account, so a sandbox created with account A is
        invisible to account B. We check every token via its own Client."""
        self._save_state()
        if not self._sandbox_ids:
            log.debug("modal cleanup: 0 sandboxes tracked")
            return
        try:
            import modal

            alive: set[str] = set()
            all_tokens = set(self._sandbox_tokens.values())
            # Fallback: if no tokens tracked yet (pre-fix sandboxes), use current env
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
                    continue  # token might be invalid or account deleted

            before = len(self._sandbox_ids)

            self._sandbox_ids = [sid for sid in self._sandbox_ids if sid in alive]
            # Also clean up orphaned token entries
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
            else:
                log.debug("modal cleanup: 0 terminated")
            self._save_state()
        except Exception:
            pass  # Modal API might not be available; will retry next tick

    async def status(self) -> ProviderStatus:
        """Statut du provider Modal."""
        pct = await self.remaining_pct()
        return ProviderStatus(
            name="modal",
            provider_type="modal",
            gpu_type=self._gpu_type,
            remaining_pct=pct,
            workers_active=len(self._sandbox_ids),
            quota_detail={
                "accounts": len(self._accounts.all),
                "budget_monthly_usd": self._budget_monthly,
                "cost_per_hour": {"T4": 0.59, "L4": 0.80, "A10": 1.10, "L40S": 1.95}.get(
                    self._gpu_type, 0.59
                ),
            },
        )

    # ── Internal ──────────────────────────────────────────────

    def _get_client(self, token_id: str, token_secret: str):
        """Return or create a Modal Client for this account.

        Avoids mutating os.environ — each account gets its own
        Client, making Modal operations thread-safe."""
        if token_id not in self._clients:
            import modal

            self._clients[token_id] = modal.Client.from_credentials(token_id, token_secret)
        return self._clients[token_id]

    def _next_account(self) -> dict:
        account = self._accounts.get(self._round)
        if account is None:
            raise RuntimeError("No enabled Modal accounts")
        self._round += 1
        return account

    @staticmethod
    def _find_worker_script() -> str:
        """Return path to worker entrypoint (no longer used — kept for reference)."""
        return "deploy/modal/worker.py"

    async def _create_sandbox(self):
        """Create a Modal Sandbox running the worker package."""
        import modal

        account = self._next_account()
        tid = account["token_id"]
        tsec = account["token_secret"]
        client = self._get_client(tid, tsec)

        app = await modal.App.lookup.aio("scrapower", create_if_missing=True, client=client)

        # Read the self-contained worker script (bundled from src/scrapower/worker/)
        worker_code = open("deploy/modal/worker.py").read()

        # Build image with CUDA + dependencies (no local files needed)
        image = (
            modal.Image.from_registry("nvidia/cuda:12.4.0-runtime-ubuntu22.04", add_python="3.12")
            .apt_install("ffmpeg")
            .pip_install("aiohttp", "faster-whisper", "yt-dlp", "wasmtime")
            .env({"HF_XET_HIGH_PERFORMANCE": "1"})
        )

        # Create sandbox — entrypoint runs the bundled worker script via -c
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
