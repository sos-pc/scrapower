"""Harvester — manages automated workers on free cloud platforms.

Reads harvester.yaml, provisions workers, monitors quotas, handles rotation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .providers.base import Provider
from .providers.colab import ColabProvider
from .providers.github_actions import GitHubActionsProvider
from .providers.local import LocalProvider

log = logging.getLogger(__name__)

PROVIDER_REGISTRY = {
    "local": LocalProvider,
    "colab": ColabProvider,
    "github_actions": GitHubActionsProvider,
}


class QuotaTracker:
    """Tracks usage quotas per provider."""

    def __init__(self):
        self._usage: dict[str, float] = {}  # provider_name → seconds used
        self._limits: dict[str, float] = {}  # provider_name → max seconds

    def set_limit(self, name: str, max_minutes: int):
        self._limits[name] = max_minutes * 60

    def record(self, name: str, seconds: float):
        self._usage[name] = self._usage.get(name, 0) + seconds

    def remaining(self, name: str) -> float | None:
        limit = self._limits.get(name)
        if limit is None:
            return None  # unlimited
        return max(0, limit - self._usage.get(name, 0))

    def is_exhausted(self, name: str) -> bool:
        remaining = self.remaining(name)
        return remaining is not None and remaining <= 0


class Harvester:
    """Manages worker lifecycle across multiple provider backends."""

    def __init__(self, coordinator_url: str, config_path: str | None = None):
        self._url = coordinator_url
        self._config_path = config_path or "harvester.yaml"
        self._providers: list[Provider] = []
        self._running = False
        self._quota = QuotaTracker()

    def load_config(self):
        """Parse harvester.yaml and create providers."""
        config_path = Path(self._config_path)
        if not config_path.exists():
            log.warning("no config found at %s, using local defaults", config_path)
            self._providers.append(
                LocalProvider(self._url, "local-default", count=1, runtimes=["wasm"])
            )
            return

        with open(config_path) as f:
            config = yaml.safe_load(f)

        for entry in config.get("providers", []):
            if not entry.get("enabled", True):
                continue

            provider_type = entry["type"]
            provider_cls = PROVIDER_REGISTRY.get(provider_type)
            if provider_cls is None:
                log.error("unknown provider type: %s", provider_type)
                continue

            name = entry.get("name", provider_type)
            provider = provider_cls.create(self._url, name, entry)
            self._providers.append(provider)

            # Set quota if configured
            max_min = entry.get("max_minutes_per_month") or entry.get("cooldown_minutes")
            if max_min:
                self._quota.set_limit(name, max_min)

            log.info("provider registered: %s (%s)", name, provider_type)

    async def start(self):
        """Main loop: start all providers, monitor, rotate."""
        self.load_config()
        self._running = True

        tasks = []
        for p in self._providers:
            tasks.append(asyncio.create_task(self._run_provider(p)))

        log.info("harvester started with %d providers", len(tasks))

        while self._running:
            await asyncio.sleep(30)
            status = self.status()
            log.info("harvester status: %s", status)

    async def _run_provider(self, provider: Provider):
        """Run a single provider with restart + quota logic."""
        while self._running:
            if self._quota.is_exhausted(provider.name):
                log.info("provider quota exhausted: %s", provider.name)
                await asyncio.sleep(300)  # check every 5 min
                continue

            try:
                await provider.start()
                log.info("provider started: %s", provider.name)

                start = asyncio.get_event_loop().time()
                await provider.wait()
                elapsed = asyncio.get_event_loop().time() - start
                self._quota.record(provider.name, elapsed)

            except Exception:
                log.exception("provider failed, restarting: %s", provider.name)

            await asyncio.sleep(provider.cooldown_seconds)

    def status(self) -> dict[str, Any]:
        return {
            "providers": [
                {
                    "name": p.name,
                    "running": p.is_running,
                    "quota_remaining": self._quota.remaining(p.name),
                }
                for p in self._providers
            ],
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def stop(self):
        self._running = False
        for p in self._providers:
            p.stop()
