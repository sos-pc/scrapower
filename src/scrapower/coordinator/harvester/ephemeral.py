"""EphemeralHarvester — boucle générique pour tous les WorkerProviders.

Gère le cycle de vie des workers éphémères (Kaggle, Modal, etc.) :
1. Interroge chaque provider pour son quota
2. Trie par capacité restante décroissante
3. Lance un worker sur le provider le moins entamé
4. Nettoie les workers morts
"""

from __future__ import annotations

import asyncio
import logging
import time

from .base import WorkerProvider

log = logging.getLogger(__name__)

TICK_SEC = 15
MIN_QUOTA_PCT = 5.0  # ne pas lancer si < 5% restant


class EphemeralHarvester:
    """Pilote générique pour tous les WorkerProviders."""

    def __init__(self, providers: list[WorkerProvider]):
        self._providers = providers
        self._running = False
        self._last_launch: float = 0

    async def run(self):
        """Boucle principale. Tourne indéfiniment."""
        self._running = True
        names = ", ".join(type(p).__name__ for p in self._providers)
        log.info("harvester: %d provider(s) — %s", len(self._providers), names)
        while self._running:
            try:
                await self._tick()
            except Exception:
                log.exception("harvester tick failed")
            await asyncio.sleep(TICK_SEC)

    def stop(self):
        self._running = False

    async def _tick(self):
        # 1. Récupérer les statuts de tous les providers
        candidates: list[tuple[float, WorkerProvider]] = []
        for p in self._providers:
            try:
                remaining = await p.remaining_pct()
                if remaining >= MIN_QUOTA_PCT:
                    candidates.append((remaining, p))
            except Exception:
                log.exception("harvester: failed to query %s", type(p).__name__)

        if not candidates:
            return

        # 2. Trier par capacité restante décroissante
        candidates.sort(key=lambda x: x[0], reverse=True)

        # 3. Vérifier s'il y a des tâches en attente
        queued = await self._count_queued()
        if queued == 0:
            return

        # 4. Lancer un worker sur le provider le moins entamé
        pct, provider = candidates[0]
        log.info(
            "harvester: %d queued, launching on %s (%.0f%% remaining)",
            queued,
            type(provider).__name__,
            pct,
        )
        try:
            ok = await provider.launch_worker()
            if not ok:
                log.warning("harvester: %s launch failed", type(provider).__name__)
        except Exception:
            log.exception("harvester: %s launch crashed", type(provider).__name__)

        # 5. Nettoyer les workers morts (pour tous les providers)
        for _, p in candidates:
            try:
                await p.cleanup_stale()
            except Exception:
                pass

    async def _count_queued(self) -> int:
        """Compter les tâches en attente dans la DB."""
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
