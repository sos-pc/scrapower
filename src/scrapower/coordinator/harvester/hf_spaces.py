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
        session_manager=None,
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
        self._space_url: str = ""  # Cached Space URL (populated on first use)
        self._session_manager = session_manager  # For counting active HF workers

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
        raise FileNotFoundError("HF Spaces deploy directory not found. Expected deploy/hf-spaces/")

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

        Returns True only when this call actually started/restarted
        a worker (first deploy or wake). Returns False when the worker
        is already running — the EphemeralHarvester should try another
        provider instead of counting this as a successful launch.
        """
        if not self._deployed:
            return await self._first_deploy()

        # Space exists — check if it needs waking.
        # Only return True when we actually DO something.
        now = time.time()
        if now - self._last_health_check < HEALTH_CHECK_INTERVAL_SEC:
            return False  # Nothing to do, worker is already running

        self._last_health_check = now
        try:
            runtime = self._api.get_space_runtime(self._space_id)
            if runtime.stage == "RUNNING":
                return False  # Already running, nothing to launch
            if runtime.stage in ("BUILDING", "RUNNING_BUILDING", "APP_STARTING"):
                log.info("hf: Space %s is %s — waiting", self._space_id, runtime.stage)
                return False  # Still building, will be ready eventually
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
            workers_active=(
                self._session_manager.mode_b_active_count("hf-") if self._session_manager else 0
            ),
            quota_detail={"stage": stage},
        )

    # -- Internal: first deployment ---------------------------------

    async def _first_deploy(self) -> bool:
        """Create Space repo, set secrets, upload code, wait for build.
        If the Space already exists and is running, skips the upload
        (coordinator restart — _deployed flag was lost)."""

        # Check if Space already exists and is running (coordinator restart)
        try:
            info = self._api.space_info(self._space_id)
            self._space_url = info.host  # Cache for _wake_space()
            rt = self._api.get_space_runtime(self._space_id)
            if rt.stage in ("RUNNING", "BUILDING", "RUNNING_BUILDING", "APP_STARTING"):
                log.info(
                    "hf: Space %s already exists (stage=%s) — skipping deploy",
                    self._space_id,
                    rt.stage,
                )
                self._deployed = True
                self._last_health_check = time.time()
                # Secrets were set during initial deploy and don't change
                # on restart. To update them after changing .env, delete the
                # HF Space and restart the coordinator (triggers Path B).
                return True
            # Stage not in the expected list (e.g. ERROR, STOPPED) —
            # fall through to full deploy to try to recover.
            log.warning(
                "hf: Space %s in stage=%s — attempting full deploy",
                self._space_id,
                rt.stage,
            )
        except Exception:
            # Space doesn't exist yet (expected on first deploy) or API
            # unreachable. Fall through to Path B: create_repo(exist_ok=True)
            # is idempotent — creates the Space on first deploy, fails fast
            # if the API is truly down.
            log.warning(
                "hf: cannot check Space %s, will attempt full deploy",
                self._space_id,
                exc_info=True,
            )
            # Falls through to Path B

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

        # Cache the Space URL now that the repo exists
        try:
            info = self._api.space_info(self._space_id)
            self._space_url = info.host
        except Exception:
            pass  # Will be lazy-populated in _wake_space if needed

        # 2. Set secrets
        await self._ensure_secrets()

        # 3. Prepare upload folder with worker runtime files
        with tempfile.TemporaryDirectory() as tmp:
            upload_dir = Path(tmp)
            # Copy deploy files (Dockerfile, app.py, README.md).
            # The HF worker (app.py) is standalone — it doesn't import
            # anything from worker/ so we don't bundle that directory.
            for fname in ["Dockerfile", "app.py", "README.md"]:
                src = Path(self._deploy_dir) / fname
                if src.exists():
                    shutil.copy2(src, upload_dir / fname)

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

    async def _ensure_secrets(self):
        """Set secrets on the Space, cleaning up any colliding variables first.
        HF Spaces forbids having both a variable and a secret with the same key."""
        for key in ["COORDINATOR_URL", "SCRAPOWER_API_KEY"]:
            try:
                self._api.delete_space_variable(self._space_id, key)
            except Exception:
                pass  # Variable may not exist, that's fine
            try:
                self._api.add_space_secret(
                    self._space_id,
                    key,
                    self._coordinator_url if key == "COORDINATOR_URL" else self._api_key,
                )
            except Exception as e:
                log.warning("hf: failed to set secret %s: %s", key, e)

    async def _ping_worker(self) -> bool:
        """Check whether the worker process is alive inside the Space.

        GETs the health endpoint (port 7860). Returns True if the
        instance responds 200 — meaning the Python worker process
        is running, even if it hasn't pulled yet."""
        space_url = self._space_url
        if not space_url:
            try:
                info = self._api.space_info(self._space_id)
                space_url = info.host
                self._space_url = space_url
            except Exception:
                space_host = self._space_id.replace("/", "-") + ".hf.space"
                space_url = f"https://{space_host}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(space_url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    return r.status == 200
        except Exception:
            return False

    async def _wake_space(self) -> bool:
        """Wake a sleeping Space by sending an HTTP GET to its health endpoint.
        This is faster than restart_space() which triggers a Docker rebuild.
        HF doc: 'Anyone visiting your Space will restart it automatically.'

        Uses the cached Space URL (from space_info().host) when available.
        Falls back to manual derivation from space_id if the cache is empty
        (e.g., first deploy before space_info() was called)."""
        space_url = self._space_url
        if not space_url:
            # Cache miss — fetch from API or derive manually
            try:
                info = self._api.space_info(self._space_id)
                space_url = info.host
                self._space_url = space_url
            except Exception:
                # Fallback: derive from space_id (old behavior)
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
