"""HuggingFace Harvester — manages a persistent Docker Space worker.

Unlike Kaggle/Modal (ephemeral workers), HF Spaces are persistent:
deploy once, run forever. The Harvester's job is simpler:
  1. On first run: create the Space repo, upload code, set secrets.
  2. On subsequent ticks: check if Space is RUNNING. If SLEEPING
     (free Spaces sleep after 48h inactivity), wake it via HTTP GET.
     No cooldown — CPU is free and unlimited.
  3. No cleanup — the worker handles its own lifecycle (idle timeout).
  4. No quota tracking — free CPU tier has no usage limits.

Design decisions:
  - HTTP GET wake instead of restart_space(): restart rebuilds the
    Docker image (slow), GET just wakes the sleeping container (fast).
  - Space is public: required for free tier, and the health endpoint
    reveals no secrets (just worker ID + coordinator URL).
  - upload_folder() instead of git push: simpler, no git configuration
    needed on the coordinator. Uses huggingface_hub's API.
  - Worker file bundling: copies src/scrapower/worker/ into the
    deploy folder before upload, because the Dockerfile references
    ./worker/ relative to the Space repo root.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

import aiohttp
from huggingface_hub import HfApi

from .base import ProviderStatus, WorkerProvider

log = logging.getLogger(__name__)

# How often to check if Space needs waking (seconds).
# Free Spaces sleep after 48h, checking every ~5min is plenty.
HEALTH_CHECK_INTERVAL_SEC = 300


class HuggingFaceHarvester(WorkerProvider):
    """Manages a single persistent HF Space running our Mode B worker.

    Must be configured with:
      - hf_token: HuggingFace API token (write access)
      - space_id: "username/repo-name" for the Space
      - coordinator_url: where the worker should connect
      - api_key: for worker authentication
    """

    def __init__(
        self,
        hf_token: str,
        space_id: str,
        coordinator_url: str,
        api_key: str = "",
        deploy_dir: str | None = None,
    ):
        self._token = hf_token
        self._space_id = space_id
        self._coordinator_url = coordinator_url
        self._api_key = api_key
        # Directory containing Dockerfile, app.py, README.md
        # Default: deploy/hf-spaces/ relative to project root
        self._deploy_dir = deploy_dir or self._find_deploy_dir()
        self._api = HfApi(token=hf_token)
        self._deployed = False  # True after first successful deploy
        self._last_health_check: float = 0

    @staticmethod
    def _find_deploy_dir() -> str:
        """Locate deploy/hf-spaces/ from the coordinator's working dir."""
        for candidate in [
            "deploy/hf-spaces",
            "../deploy/hf-spaces",
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "deploy", "hf-spaces"),
        ]:
            if os.path.isdir(candidate):
                return candidate
        raise FileNotFoundError(
            "HF Spaces deploy directory not found. Expected deploy/hf-spaces/"
        )

    # -- WorkerProvider interface ------------------------------------

    async def remaining_pct(self) -> float:
        """CPU free tier has unlimited usage. Always 100% if Space exists."""
        if not self._deployed:
            return 100.0  # We can always deploy
        try:
            runtime = self._api.get_space_runtime(self._space_id)
            # SLEEPING is fine — we'll wake it on next launch_worker()
            if runtime.stage in ("RUNNING", "SLEEPING", "APP_STARTING"):
                return 100.0
            if runtime.stage in ("BUILDING", "RUNNING_BUILDING"):
                return 50.0  # Building — not ready yet but progressing
            return 0.0  # ERROR, STOPPED, etc.
        except Exception:
            return 100.0  # Optimistic: assume we can create it

    async def has_quota(self) -> bool:
        """CPU is always free. Always True."""
        return True

    async def launch_worker(self) -> bool:
        """Ensure a worker is running. Creates the Space on first call,
        wakes it on subsequent calls if sleeping.

        Returns True if a worker is (or will be) running.
        """
        if not self._deployed:
            return await self._first_deploy()

        # Space exists — check if it needs waking
        now = time.time()
        if now - self._last_health_check < HEALTH_CHECK_INTERVAL_SEC:
            return True  # Checked recently, assume alive

        self._last_health_check = now
        try:
            runtime = self._api.get_space_runtime(self._space_id)
            if runtime.stage == "RUNNING":
                return True
            if runtime.stage in ("BUILDING", "RUNNING_BUILDING", "APP_STARTING"):
                log.info("hf: Space %s is %s — waiting", self._space_id, runtime.stage)
                return True  # It's starting, will be ready soon
            if runtime.stage in ("SLEEPING", "PAUSED", "STOPPED"):
                # Wake via HTTP GET (avoids Docker rebuild)
                return await self._wake_space()
            log.warning("hf: Space %s in unexpected stage: %s", self._space_id, runtime.stage)
            # Try restart as fallback
            self._api.restart_space(self._space_id)
            return True
        except Exception as e:
            log.warning("hf: Failed to check/restart Space: %s", e)
            return False

    async def cleanup_stale(self):
        """Nothing to clean — the worker manages its own lifecycle.
        The Space auto-sleeps after 48h inactivity (free tier)."""
        pass

    async def status(self) -> ProviderStatus:
        """Human-readable status for the EphemeralHarvester log line."""
        try:
            rt = self._api.get_space_runtime(self._space_id)
            stage = rt.stage
        except Exception:
            stage = "unknown"
        return ProviderStatus(
            name="hf",
            provider_type="hf-spaces",
            gpu_type="none",
            remaining_pct=await self.remaining_pct(),
            workers_active=1 if stage == "RUNNING" else 0,
            quota_detail={"stage": stage},
        )

    # -- Internal: first deployment ---------------------------------

    async def _first_deploy(self) -> bool:
        """Create Space repo, set secrets, upload code, wait for build.
        Called exactly once per coordinator lifetime."""
        log.info("hf: first deploy for %s", self._space_id)

        # 1. Create the Space repo (idempotent if exists)
        try:
            self._api.create_repo(
                self._space_id,
                repo_type="space",
                space_sdk="docker",
                exist_ok=True,
            )
        except Exception as e:
            log.error("hf: failed to create Space repo: %s", e)
            return False

        # 2. Set secrets (env vars the worker reads at runtime)
        try:
            self._api.add_space_secret(
                self._space_id, "COORDINATOR_URL", self._coordinator_url
            )
            self._api.add_space_secret(
                self._space_id, "SCRAPOWER_API_KEY", self._api_key
            )
        except Exception as e:
            log.warning("hf: failed to set some secrets: %s", e)
            # Non-fatal — secrets may already exist

        # 3. Prepare upload folder with worker runtime files
        with tempfile.TemporaryDirectory() as tmp:
            upload_dir = Path(tmp)
            # Copy deploy files (Dockerfile, app.py, README.md)
            for fname in ["Dockerfile", "app.py", "README.md"]:
                src = Path(self._deploy_dir) / fname
                if src.exists():
                    shutil.copy2(src, upload_dir / fname)

            # Copy worker runtime (needed by Dockerfile's COPY worker/)
            worker_src = Path(self._deploy_dir).parent.parent / "src" / "scrapower" / "worker"
            if worker_src.exists():
                shutil.copytree(worker_src, upload_dir / "worker")
            else:
                log.error("hf: worker runtime not found at %s", worker_src)
                return False

            # 4. Upload everything to the Space repo
            try:
                self._api.upload_folder(
                    repo_id=self._space_id,
                    folder_path=str(upload_dir),
                    repo_type="space",
                    commit_message="Deploy Scrapower worker v0.1",
                )
            except Exception as e:
                log.error("hf: failed to upload code: %s", e)
                return False

        # 5. Wait for Space to build and start
        try:
            runtime = self._api.restart_space(self._space_id)
            log.info("hf: Space restart triggered, stage=%s", runtime.stage)
        except Exception as e:
            log.warning("hf: restart failed (may already be building): %s", e)

        self._deployed = True
        self._last_health_check = time.time()
        log.info("hf: deploy complete for %s", self._space_id)
        return True

    async def _wake_space(self) -> bool:
        """Wake a sleeping Space by sending an HTTP GET to its health endpoint.
        This is faster than restart_space() which triggers a Docker rebuild.
        HF doc: 'Anyone visiting your Space will restart it automatically.'"""
        # Derive Space URL from space_id: username/repo-name → username-repo-name.hf.space
        space_host = self._space_id.replace("/", "-") + ".hf.space"
        space_url = f"https://{space_host}"

        log.info("hf: waking Space %s via %s", self._space_id, space_url)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(space_url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status in (200, 503):
                        # 200 = already awake, 503 = waking up (HF returns this during cold start)
                        log.info("hf: Space wake OK (HTTP %s)", r.status)
                        return True
                    log.warning("hf: unexpected HTTP %s waking Space", r.status)
        except Exception as e:
            log.warning("hf: HTTP wake failed (%s), trying restart_space", e)
            try:
                self._api.restart_space(self._space_id)
                return True
            except Exception:
                return False
        return True
