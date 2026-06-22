"""Modal Harvester — auto-start Sandbox workers on Modal GPU.

Uses modal.Sandbox.create() to provision ephemeral workers.
Authentication via MODAL_TOKEN_ID + MODAL_TOKEN_SECRET env vars.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from .base import ProviderStatus, WorkerProvider

log = logging.getLogger(__name__)

COOLDOWN_SEC = 120  # minimum seconds between sandbox creations
GPU_TYPE = "T4"  # default GPU — $0.59/h on Modal Starter
GPU_VRAM_MB = 16384
SANDBOX_TIMEOUT = 6 * 3600  # 6h max per sandbox
IDLE_TIMEOUT = 300  # 5 min idle → auto-terminate
WORKER_SCRIPT = "deploy/modal/worker.py"


class ModalHarvester(WorkerProvider):
    """Provisionne des Sandboxes Modal avec GPU."""

    def __init__(
        self,
        token_id: str,
        token_secret: str,
        coordinator_url: str = "https://scrapower.talos-int.com",
        api_key: str = "",
        budget_monthly_usd: float = 30.0,
        gpu_type: str = GPU_TYPE,
    ):
        self._token_id = token_id
        self._token_secret = token_secret
        self._coordinator_url = coordinator_url
        self._api_key = api_key
        self._gpu_type = gpu_type
        self._budget_monthly = budget_monthly_usd
        self._last_start: float = 0
        self._total_seconds_used: float = 0
        self._sandbox_ids: list[str] = []
        self._running = False
        # GPU cost per second (from Modal pricing)
        self._cost_per_sec = {
            "T4": 0.000164,
            "L4": 0.000222,
            "A10": 0.000306,
            "L40S": 0.000542,
        }.get(gpu_type, 0.000164)

    # ── WorkerProvider interface ──────────────────────────────

    async def remaining_pct(self) -> float:
        """Budget restant en pourcentage du budget mensuel."""
        used_usd = self._total_seconds_used * self._cost_per_sec
        remaining = max(0, self._budget_monthly - used_usd)
        return remaining / self._budget_monthly * 100

    async def has_quota(self) -> bool:
        """Budget restant > 1%."""
        return await self.remaining_pct() > 1.0

    async def launch_worker(self) -> bool:
        """Crée un Sandbox Modal avec GPU T4.

        Le Sandbox exécute deploy/modal/worker.py qui se connecte
        au coordinateur en Mode B (HTTP pull), traite des tâches,
        et s'arrête après idle_timeout.
        """
        # Rate-limit sandbox creation
        if time.time() - self._last_start < COOLDOWN_SEC:
            return False

        try:
            worker_path = self._find_worker_script()
            sb = await self._create_sandbox(worker_path)
            self._last_start = time.time()
            self._sandbox_ids.append(sb.object_id)
            log.info("modal sandbox created: %s (gpu=%s)", sb.object_id, self._gpu_type)
            return True
        except Exception:
            log.exception("modal sandbox creation failed")
            return False

    async def cleanup_stale(self) -> None:
        """Termine les Sandboxes qui ne sont plus en cours d'exécution.

        Modal facture à la seconde, donc on ne paie que le temps
        d'exécution réel. Les Sandboxes s'auto-terminent après
        idle_timeout — ici on nettoie juste la liste locale.
        """
        # Modal Sandboxes auto-terminate after idle_timeout.
        # We just track local state — no explicit cleanup needed.
        # In future: call modal.Sandbox.list() to check status.
        pass

    async def status(self) -> ProviderStatus:
        """Statut du provider Modal."""
        pct = await self.remaining_pct()
        return ProviderStatus(
            name="modal",
            provider_type="modal",
            gpu_type=self._gpu_type,
            remaining_pct=pct,
            workers_active=len(self._sandbox_ids),
            quota_detail={
                "budget_monthly_usd": self._budget_monthly,
                "cost_per_hour": self._cost_per_sec * 3600,
                "seconds_used": self._total_seconds_used,
            },
        )

    # ── Internal ──────────────────────────────────────────────

    @staticmethod
    def _find_worker_script() -> str:
        for path in [WORKER_SCRIPT, f"../{WORKER_SCRIPT}", f"/app/{WORKER_SCRIPT}"]:
            if os.path.exists(path):
                return path
        raise FileNotFoundError(f"Modal worker script not found: {WORKER_SCRIPT}")

    async def _create_sandbox(self, worker_path: str):
        """Create a Modal Sandbox running the worker script."""
        import modal

        # Set auth from env vars (already configured via modal setup or env)
        os.environ.setdefault("MODAL_TOKEN_ID", self._token_id)
        os.environ.setdefault("MODAL_TOKEN_SECRET", self._token_secret)

        app = await modal.App.lookup.aio("scrapower", create_if_missing=True)

        # Read worker script content
        worker_code = open(worker_path).read()

        # Build image with dependencies + CUDA for GPU
        # Use CUDA base image so faster-whisper can use the GPU
        image = (
            modal.Image.from_registry("nvidia/cuda:12.4.0-runtime-ubuntu22.04", add_python="3.12")
            .apt_install("ffmpeg")
            .pip_install("aiohttp", "faster-whisper", "yt-dlp", "yt-dlp-ejs")
            .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
        )

        # Create sandbox with worker script as entrypoint
        sb = await modal.Sandbox.create.aio(
            "python",
            "-c",
            worker_code,
            app=app,
            image=image,
            gpu=self._gpu_type,
            timeout=SANDBOX_TIMEOUT,
            cpu=4,
            memory=30720,  # 30 GB RAM
            secrets=[
                modal.Secret.from_dict(
                    {
                        "COORDINATOR_URL": self._coordinator_url,
                        "SCRAPOWER_API_KEY": self._api_key,
                    }
                )
            ],
        )
        return sb
