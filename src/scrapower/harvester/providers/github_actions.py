"""GitHub Actions provider — runs workers via CI/CD pipeline.

Free for public repos (unlimited minutes), 2000 min/month for private repos.
The workflow in .github/workflows/scrapower-worker.yml triggers on schedule
and runs the native Python worker.
"""

from __future__ import annotations

import asyncio
import logging

from .base import Provider

log = logging.getLogger(__name__)


class GitHubActionsProvider(Provider):
    """GitHub Actions worker provider."""

    def __init__(
        self,
        coordinator_url: str,
        name: str = "github",
        repo: str = "",
        cooldown_seconds: int = 300,
    ):
        super().__init__(coordinator_url, name, cooldown_seconds)
        self._repo = repo

    @classmethod
    def create(cls, coordinator_url: str, name: str, config: dict) -> GitHubActionsProvider:
        return cls(
            coordinator_url,
            name,
            repo=config.get("repo", ""),
            cooldown_seconds=config.get("cooldown_seconds", 300),
        )

    async def start(self):
        """Wait for GitHub Actions workers to connect (triggered by schedule)."""
        self._running = True
        log.info(
            "GitHub Actions provider active for repo: %s. "
            "Ensure .github/workflows/scrapower-worker.yml is configured.",
            self._repo,
        )
        while self._running:
            await asyncio.sleep(60)

    def stop(self):
        super().stop()
