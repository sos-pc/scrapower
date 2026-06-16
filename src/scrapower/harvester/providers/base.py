"""Provider interface for harvester backends."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class Provider(ABC):
    """Base class for cloud worker providers."""

    def __init__(self, coordinator_url: str, name: str, cooldown_seconds: int = 10):
        self._coordinator_url = coordinator_url
        self._name = name
        self._running = False
        self._cooldown = cooldown_seconds

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def cooldown_seconds(self) -> int:
        return self._cooldown

    @classmethod
    def create(cls, coordinator_url: str, name: str, config: dict) -> Provider:
        """Factory method. Override in subclasses for custom config parsing."""
        return cls(coordinator_url, name)

    @abstractmethod
    async def start(self):
        """Provision and start the worker."""
        ...

    async def wait(self):
        """Wait until the worker stops (blocking)."""
        while self._running:
            await asyncio.sleep(5)

    def stop(self):
        """Stop the worker."""
        self._running = False
