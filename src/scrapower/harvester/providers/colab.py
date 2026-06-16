"""Colab provider — runs a worker in Google Colab (free GPU T4).

To use:
1. Open the notebook at providers/colab_worker.ipynb in Colab
2. Run it — it will install the worker and connect to the coordinator
3. The notebook runs for ~12h, then needs restart (handled by harvester rotation)

For automated provisioning (future): use Google Drive API or Selenium,
but this violates ToS for automated/non-interactive usage.
"""

from __future__ import annotations

import asyncio
import logging

from .base import Provider

log = logging.getLogger(__name__)


class ColabProvider(Provider):
    """Colab worker — user opens notebook manually. Harvester monitors and rotates."""

    def __init__(
        self,
        coordinator_url: str,
        name: str = "colab",
        cooldown_seconds: int = 1800,  # 30 min cooldown between sessions
    ):
        super().__init__(coordinator_url, name, cooldown_seconds)
        self._session_lifetime = 12 * 3600  # 12 hours max
        self._started_at: float | None = None

    @classmethod
    def create(cls, coordinator_url: str, name: str, config: dict) -> ColabProvider:
        return cls(
            coordinator_url,
            name,
            cooldown_seconds=config.get("cooldown_minutes", 30) * 60,
        )

    async def start(self):
        """Wait for a Colab worker to connect."""
        self._running = True
        self._started_at = asyncio.get_event_loop().time()
        log.info(
            "Colab provider waiting for worker. Open the notebook at "
            "providers/colab_worker.ipynb and run all cells."
        )
        # Just wait until stop() is called or session expires
        while self._running:
            elapsed = asyncio.get_event_loop().time() - (self._started_at or 0)
            if elapsed > self._session_lifetime:
                log.info("Colab session lifetime reached (12h), restarting")
                break
            await asyncio.sleep(60)

    def stop(self):
        super().stop()
