"""GitHub Actions provider — runs workers via workflow dispatch.

Uses the GitHub REST API to trigger a workflow that runs a Scrapower
native worker. The visitor authorizes via OAuth, and we dispatch
workflows on their behalf.

Requirements:
- GitHub OAuth App with 'workflow' scope
- A repo with scrapower-worker.yml workflow
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import aiohttp

from .base import Provider

log = logging.getLogger(__name__)

# Template repo where the worker workflow lives
TEMPLATE_REPO = os.environ.get("SCRAPOWER_GH_TEMPLATE", "sos-pc/scrapower")
WORKFLOW_ID = "scrapower-worker.yml"

# Workflow dispatch API
API_URL = "https://api.github.com"


class GitHubActionsProvider(Provider):
    """GitHub Actions worker provider — dispatches workflows via REST API."""

    def __init__(
        self,
        coordinator_url: str,
        name: str = "github",
        github_token: str = "",
        repo: str = "",
        cooldown_seconds: int = 300,
        max_workers: int = 3,
    ):
        super().__init__(coordinator_url, name, cooldown_seconds)
        self._token = github_token
        self._repo = repo or TEMPLATE_REPO
        self._max_workers = max_workers
        self._active_runs: set[str] = set()
        self._last_dispatch = 0.0

    @classmethod
    def create(cls, coordinator_url: str, name: str, config: dict) -> GitHubActionsProvider:
        return cls(
            coordinator_url,
            name,
            github_token=config.get("github_token", ""),
            repo=config.get("repo", TEMPLATE_REPO),
            cooldown_seconds=config.get("cooldown_seconds", 300),
            max_workers=config.get("max_workers", 3),
        )

    async def start(self):
        """Periodically dispatch workflows to maintain target worker count."""
        self._running = True
        log.info("GitHub Actions provider started (repo=%s, max=%d)", self._repo, self._max_workers)

        while self._running:
            try:
                await self._tick()
            except Exception:
                log.exception("GitHub Actions provider tick failed")
            await asyncio.sleep(self._cooldown)

    async def _tick(self):
        """One maintenance tick: check active runs, dispatch if needed."""
        now = time.time()

        # Dispatch new workers if below target
        active_count = len(self._active_runs)
        needed = self._max_workers - active_count

        if needed > 0 and (now - self._last_dispatch) > 60:  # rate limit: 1 dispatch/min
            for _ in range(needed):
                run_id = await self._dispatch_workflow()
                if run_id:
                    self._active_runs.add(run_id)
                    log.info("dispatched workflow run: %s", run_id)
            self._last_dispatch = now

        # Clean up completed runs
        await self._prune_completed()

    async def _dispatch_workflow(self) -> str | None:
        """Dispatch a workflow via GitHub API. Returns run_id or None."""
        owner, repo = self._repo.split("/")
        url = f"{API_URL}/repos/{owner}/{repo}/actions/workflows/{WORKFLOW_ID}/dispatches"

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        body = {
            "ref": "master",
            "inputs": {
                "coordinator_url": self._coordinator_url,
                "worker_id": f"gh-{int(time.time())}",
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers) as r:
                if r.status == 204:
                    # GitHub returns 204 on success, but doesn't return the run ID.
                    # We'll find it by listing recent runs.
                    return await self._find_latest_run()
                log.warning("dispatch failed: %d %s", r.status, await r.text())
                return None

    async def _find_latest_run(self) -> str | None:
        """Find the most recent workflow run ID."""
        owner, repo = self._repo.split("/")
        url = f"{API_URL}/repos/{owner}/{repo}/actions/runs?per_page=1"

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as r:
                if r.status == 200:
                    data = await r.json()
                    runs = data.get("workflow_runs", [])
                    if runs:
                        return str(runs[0]["id"])
        return None

    async def _prune_completed(self):
        """Remove completed runs from active set."""
        for run_id in list(self._active_runs):
            owner, repo = self._repo.split("/")
            url = f"{API_URL}/repos/{owner}/{repo}/actions/runs/{run_id}"

            headers = {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("status") == "completed":
                            self._active_runs.discard(run_id)
                            log.info("workflow run %s completed", run_id)

    def stop(self):
        super().stop()
