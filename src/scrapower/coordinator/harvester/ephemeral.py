"""EphemeralHarvester — boucle générique pour tous les WorkerProviders.

Gère le cycle de vie des workers éphémères (Kaggle, Modal, etc.) :
1. Interroge chaque provider pour son quota
2. Compte les workers actifs vs tâches en attente
3. Ne lance que si nécessaire (évite les "launch failed" fantômes)
4. Trie par capacité restante décroissante
5. Nettoie les workers morts
"""

from __future__ import annotations

import asyncio
import logging

from .base import WorkerProvider

log = logging.getLogger(__name__)

TICK_SEC = 15
MIN_QUOTA_PCT = 5.0  # ne pas lancer si < 5% restant


class EphemeralHarvester:
    """Pilote générique pour tous les WorkerProviders."""

    def __init__(self, providers: list[WorkerProvider], task_service=None):
        self._providers = providers
        self._task_service = task_service
        self._running = False

    async def run(self):
        """Boucle principale. Tourne indéfiniment."""
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
        # 1. Récupérer les statuts de tous les providers
        candidates: list[tuple[float, WorkerProvider]] = []
        total_active = 0
        status_lines: list[str] = []

        # Vérifier si toutes les tâches en attente nécessitent un GPU.
        # Si oui, on ne compte pas les providers CPU-only dans total_active
        # (ils ne peuvent pas traiter ces tâches, donc ils ne sont pas "actifs").
        gpu_only = await self._gpu_only_queued()

        for p in self._providers:
            try:
                remaining = await p.remaining_pct()
                status = await p.status()
                # Ne pas compter les workers CPU-only si toutes les tâches sont GPU
                if not (gpu_only and status.gpu_type == "none"):
                    total_active += status.workers_active
                status_lines.append(
                    f"{status.name}({remaining:.0f}%, {status.workers_active} active)"
                )
                if remaining >= MIN_QUOTA_PCT:
                    candidates.append((remaining, p))
            except Exception:
                log.exception("harvester: failed to query %s", type(p).__name__)

        # Cleanup ALWAYS runs
        for p in self._providers:
            try:
                await p.cleanup_stale()
            except Exception:
                pass

        if not candidates:
            return

        # 2. Trier par capacité restante décroissante
        candidates.sort(key=lambda x: x[0], reverse=True)

        # 3. Vérifier s'il y a des tâches en attente
        queued = await self._count_queued()
        if queued == 0:
            return

        # 4. Smart launch: ne lancer que si on a besoin de plus de workers
        if total_active >= queued:
            log.info(
                "harvester: %d active workers for %d tasks (%s) — skipping launch",
                total_active,
                queued,
                ", ".join(status_lines),
            )
            return

        needed = queued - total_active
        log.info(
            "harvester: %d tasks queued, %d active (%s), need %d more — trying %s",
            queued,
            total_active,
            ", ".join(status_lines),
            needed,
            type(candidates[0][1]).__name__,
        )

        # 5. Lancer sur le meilleur provider (le provider logue son propre résultat)
        launched = False
        skipped = 0
        for pct, provider in candidates:
            try:
                ok = await provider.launch_worker()
                if ok:
                    log.info(
                        "harvester: launched on %s (%.0f%% remaining)",
                        type(provider).__name__,
                        pct,
                    )
                    launched = True
                    break
                skipped += 1
            except Exception:
                log.exception(
                    "harvester: %s launch crashed, trying next...", type(provider).__name__
                )

        if not launched:
            log.info(
                "harvester: all %d provider(s) declined launch (%d tasks waiting, %d workers active)",
                len(candidates),
                queued,
                total_active,
            )

    async def _count_queued(self) -> int:
        """Nombre de tâches en attente, via TaskService (injecté)."""
        if self._task_service is None:
            return 0
        return await self._task_service.count_queued()

    async def _gpu_only_queued(self) -> bool:
        """Vrai si toutes les tâches en attente nécessitent un GPU.

        Si aucune tâche en attente, retourne False (conservateur).
        Utilisé pour ne pas compter les workers CPU-only dans total_active
        quand ils ne peuvent de toute façon pas traiter les tâches."""
        if self._task_service is None:
            return False
        cursor = await self._task_service._tm._db.execute(
            "SELECT COUNT(*) as n FROM tasks WHERE state = 'queued' AND gpu_required = 0"
        )
        row = await cursor.fetchone()
        cpu_count = row["n"] if row else 0
        if cpu_count > 0:
            return False  # Au moins une tâche CPU → ne pas filtrer
        # Vérifier qu'il y a AU MOINS une tâche GPU
        cursor = await self._task_service._tm._db.execute(
            "SELECT COUNT(*) as n FROM tasks WHERE state = 'queued'"
        )
        row = await cursor.fetchone()
        total = row["n"] if row else 0
        return total > 0  # GPU-only si au moins une tâche et zéro CPU
