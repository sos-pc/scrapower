"""EphemeralHarvester — boucle de distribution par compte.

Gère le cycle de vie des workers éphémères (Kaggle, Modal) et
persistants (HF Spaces) :
1. Rafraîchit les quotas de tous les comptes via leurs providers
2. Compte les workers actifs par compte
3. Sélectionne le meilleur compte (quota + matching GPU)
4. Lance un worker sur ce compte
5. Nettoie les workers morts
"""

from __future__ import annotations

import asyncio
import logging

from ..accounts import AccountRegistry
from .base import WorkerProvider

log = logging.getLogger(__name__)

TICK_SEC = 15
MIN_QUOTA_PCT = 5.0


class EphemeralHarvester:
    """Pilote générique pour tous les comptes worker.

    Après v0.7 : itère les comptes directement via AccountRegistry,
    pas les providers. Les providers sont juste la couche API.
    """

    def __init__(
        self,
        registry: AccountRegistry,
        providers: list[WorkerProvider],
        task_service=None,
    ):
        self._registry = registry
        self._providers = providers
        self._providers_by_name = {p.provider_name: p for p in providers}
        self._task_service = task_service
        self._running = False

    async def run(self):
        self._running = True
        names = ", ".join(type(p).__name__ for p in self._providers)
        log.info("harvester: %d provider(s) - %s", len(self._providers), names)
        while self._running:
            try:
                await self._tick()
            except Exception:
                log.exception("harvester tick failed")
            await asyncio.sleep(TICK_SEC)

    def stop(self):
        self._running = False

    async def _tick(self):
        # 1. Refresh quotas for all accounts via their providers
        for p in self._providers:
            try:
                await p.refresh_quota(self._registry)
            except Exception:
                log.exception("harvester: refresh_quota failed for %s", type(p).__name__)

        # 2. Cleanup stale workers (always runs, provider-wide)
        for p in self._providers:
            try:
                await p.cleanup_stale(self._registry)
            except Exception:
                pass  # best-effort cleanup, errors logged at provider level

        # 3. Count queued tasks and decide if we need workers
        queued = await self._count_queued()
        if queued == 0:
            return

        gpu_only = await self._gpu_only_queued()
        total_active = sum(
            a.workers_active for a in self._registry.enabled if not (gpu_only and not a.has_gpu)
        )

        if total_active >= queued:
            return  # enough workers already

        # 4. Launch workers on multiple accounts in parallel
        candidates = self._registry.candidates_for_task(
            gpu_required=gpu_only, min_quota_pct=MIN_QUOTA_PCT
        )
        needed = queued - total_active

        # Build launch coroutines for candidate accounts (up to needed)
        to_launch: list[tuple[str, asyncio.Task]] = []
        for account in candidates:
            if len(to_launch) >= needed:
                break
            provider = self._providers_by_name.get(account.provider)
            if not provider:
                continue
            if account.lifecycle == "persistent":
                to_launch.append((account.id, provider.ensure_running(account)))
            else:
                to_launch.append((account.id, provider.launch_worker(account)))

        if not to_launch:
            return

        # Launch all in parallel
        results = await asyncio.gather(*(coro for _, coro in to_launch), return_exceptions=True)
        for (account_id, _), ok in zip(to_launch, results):
            if isinstance(ok, Exception):
                log.warning("harvester: launch failed for %s: %s", account_id, ok)
            elif ok:
                account = self._registry.get(account_id)
                log.info(
                    "harvester: launched on %s (%.0f%%)",
                    account_id,
                    account.remaining_pct if account else 0,
                )

    async def _count_queued(self) -> int:
        if self._task_service:
            return await self._task_service.count_queued()
        return 0

    async def _gpu_only_queued(self) -> bool:
        """True if ALL queued tasks require GPU."""
        if not self._task_service:
            return False
        tasks = await self._task_service.get_queued(limit=10)
        if not tasks:
            return False
        return all(t.gpu_required for t in tasks)
