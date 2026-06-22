"""WorkerProvider ABC — interface commune pour tous les providers éphémères.

Tous les providers (Kaggle, Modal, etc.) implémentent cette interface.
Le EphemeralHarvester les interroge pour décider lequel lance un worker.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProviderStatus:
    name: str
    provider_type: str  # "kaggle", "modal", ...
    gpu_type: str
    remaining_pct: float  # 0.0 = épuisé, 100.0 = plein
    workers_active: int
    quota_detail: dict | None = None  # données brutes (heures restantes, budget, etc.)


class WorkerProvider(ABC):
    """Une source de workers GPU éphémères.

    Chaque provider gère un ou plusieurs comptes sur une plateforme
    (Kaggle, Modal, etc.). Le harvester l'interroge périodiquement
    pour décider s'il doit lancer un worker.
    """

    @abstractmethod
    async def remaining_pct(self) -> float:
        """Pourcentage de quota restant. 0.0 = épuisé, 100.0 = plein.

        Permet de comparer des sources hétérogènes (heures GPU Kaggle
        vs crédits $ Modal) sur une échelle unique.
        """
        ...

    @abstractmethod
    async def has_quota(self) -> bool:
        """True si au moins un worker peut être lancé (quota > seuil minimum)."""
        ...

    @abstractmethod
    async def launch_worker(self) -> bool:
        """Lance un worker. Retourne True si succès.

        Le worker doit s'autogérer : se connecter au coordinateur,
        pull des tâches, et s'arrêter après idle_timeout.
        """
        ...

    @abstractmethod
    async def cleanup_stale(self) -> None:
        """Nettoie les workers morts/orphelins (kernels stuck, sandbox zombies)."""
        ...

    @abstractmethod
    async def status(self) -> ProviderStatus:
        """Statut actuel du provider (quota, workers actifs, etc.)."""
        ...
