"""WorkerProvider ABC — interface commune pour tous les providers.

Chaque provider (Kaggle, Modal, HF Spaces) implémente cette interface.
Le EphemeralHarvester interroge l'AccountRegistry directement pour
 décider quel compte lancer, puis appelle launch_worker(account).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..accounts import Account


class WorkerProvider(ABC):
    """A provider manages one or more accounts on a single platform.

    After v0.7 refactor: the harvester no longer iterates providers
    directly. It queries AccountRegistry for the best account, then
    calls launch_worker(account) on the account's provider.
    """

    provider_name: str = ""  # set by subclass

    @abstractmethod
    async def refresh(self, registry) -> None:
        """Update quota and active worker count for all accounts in registry."""
        ...

    @abstractmethod
    async def launch_worker(self, account: Account) -> bool:
        """Launch a worker on a specific account. Returns True on success."""
        ...

    @abstractmethod
    async def cleanup_stale(self, registry) -> None:
        """Clean up stale workers for all accounts of this provider."""
        ...
