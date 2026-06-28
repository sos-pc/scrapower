"""WorkerProvider ABC — interface commune pour tous les providers.

Chaque provider (Kaggle, Modal, HF Spaces) implémente cette interface.
Le EphemeralHarvester interroge l'AccountRegistry directement pour
décider quel compte lancer, puis appelle launch_worker(account).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..accounts import Account


@dataclass
class ProviderStatus:
    """Aggregate status for display (dashboard, stats). Not used for decisions."""

    name: str
    provider_type: str
    gpu_type: str
    remaining_pct: float  # best account's quota (indicative)
    workers_active: int
    quota_detail: dict | None = None


class WorkerProvider(ABC):
    """A provider manages one or more accounts on a single platform.

    After v0.7 refactor: the harvester no longer iterates providers
    directly. It queries AccountRegistry for the best account, then
    calls launch_worker(account) on the account's provider.
    """

    provider_name: str = ""  # set by subclass

    @abstractmethod
    async def refresh_quota(self, registry) -> None:
        """Update quota for all accounts of this provider in the registry."""
        ...

    @abstractmethod
    async def launch_worker(self, account: Account) -> bool:
        """Launch a worker on a specific account. Returns True on success."""
        ...

    @abstractmethod
    async def cleanup_stale(self, registry) -> None:
        """Clean up stale workers for all accounts of this provider."""
        ...

    @abstractmethod
    async def status(self, registry) -> ProviderStatus:
        """Aggregate status for display purposes."""
        ...
