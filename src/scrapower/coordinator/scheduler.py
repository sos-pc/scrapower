"""Scheduler — matches queued tasks with compatible workers.

Runs as a background loop. Pushes task_assign directly to worker WebSockets.
Supports challenge verification: double-executes random tasks to detect lies.
Challenge rate is now adaptive: new/untrusted workers get higher rates,
trusted workers get minimum sampling.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

from .domain import SchedulingPolicy, TaskService
from .protocol import TaskAssign, TaskPayload, to_dict
from .worker_gateway.session import SessionManager

if TYPE_CHECKING:
    from .reputation import ReputationService

log = logging.getLogger(__name__)

# Default challenge rate when reputation service is unavailable
DEFAULT_CHALLENGE_RATE = 0.10


class Scheduler:
    """Matches tasks to workers based on capabilities and lifecycle."""

    def __init__(
        self,
        task_service: TaskService,
        session_manager: SessionManager,
        tick_sec: float = 5.0,
        enforce_segregation: bool = False,
        verification_mode: str = "trust",
        reputation_service: "ReputationService | None" = None,
    ):
        self._tasks = task_service
        self._sm = session_manager
        self._tick = tick_sec
        self._verification = verification_mode
        self._policy = SchedulingPolicy(enforce_segregation=enforce_segregation)
        self._reputation = reputation_service
        self._running = False

    async def run(self):
        """Main scheduler loop."""
        self._running = True
        while self._running:
            try:
                await self._tick_loop()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(self._tick)

    async def _tick_loop(self):
        """One tick: match queued tasks to available workers."""
        tasks = await self._tasks.get_queued(limit=100)
        await self._tasks.requeue_stale()
        if not tasks:
            return

        workers = self._sm.active_sessions
        if not workers:
            return

        # Pre-load reputation scores for all active workers
        reputations: dict[str, float] = {}
        if self._reputation:
            for w in workers:
                try:
                    rep = await self._reputation.get(w.worker_id)
                    reputations[w.worker_id] = rep.score
                except Exception:
                    reputations[w.worker_id] = 0.5  # neutral fallback

        log.info(
            "active workers: %d, queued tasks: %d",
            len(workers),
            len(tasks),
        )

        for task in tasks:
            if task.is_terminal:
                continue

            compatible = self._policy.match(task, workers, reputations)
            if not compatible:
                continue

            # Best worker: external first, idle first, high reputation first.
            worker = compatible[0]
            if worker.worker_id == "_embedded":
                continue  # Skip embedded � only for trusted/system tasks — only for trusted/system tasks

            # Determine if this task should be challenged (double-executed)
            should_challenge = False
            challenge_probability = DEFAULT_CHALLENGE_RATE

            if self._verification == "challenge" and len(compatible) >= 2:
                if self._reputation:
                    try:
                        challenge_probability = await self._reputation.challenge_rate(
                            worker.worker_id
                        )
                    except Exception:
                        challenge_probability = DEFAULT_CHALLENGE_RATE
                should_challenge = random.random() < challenge_probability

            if should_challenge:
                worker_b = compatible[1]
                await self._assign_challenged(task, worker, worker_b)
            else:
                await self._assign_single(task, worker)

    async def _assign_single(self, task, worker):
        """Assign a task to a single worker (normal flow)."""
        log.info(
            "assigning task %s to %s (in_progress=%d)",
            task.id[:8],
            worker.worker_id,
            worker.tasks_in_progress,
        )
        success, token = await self._tasks.assign(task.id, worker.worker_id)
        if not success:
            return
        worker.tasks_in_progress += 1
        if worker.ws:
            try:
                msg = TaskAssign(
                    task=TaskPayload(
                        id=task.id,
                        runtime=task.runtime,
                        client_id=task.client_id,
                        assignment_token=token,
                        deadline_ms=task.deadline_ms,
                        gpu_required=task.gpu_required,
                        payload={
                            "executable_hash": task.executable_hash,
                            "input_hash": task.input_hash,
                        },
                    )
                )
                await worker.ws.send_json(to_dict(msg))
            except Exception:
                log.warning(
                    "failed to push task to worker",
                    task_id=task.id,
                    worker_id=worker.worker_id,
                )

    async def _assign_challenged(self, task, worker_a, worker_b):
        """Double-assign a task for verification. Both workers must agree."""
        # Assign to worker A
        success_a, token_a = await self._tasks.assign(task.id, worker_a.worker_id)
        if not success_a:
            await self._assign_single(task, worker_b)  # fallback: single assign
            return

        worker_a.tasks_in_progress += 1
        worker_b.tasks_in_progress += 1

        # Generate a second token for worker B
        import uuid

        token_b = uuid.uuid4().hex

        # Store challenge in DB
        await self._tasks.create_challenge(task.id, token_a, token_b)

        log.info(
            "challenge: task %s → %s + %s",
            task.id[:8],
            worker_a.worker_id[:8],
            worker_b.worker_id[:8],
        )

        # Push to worker A (normal)
        payload = TaskPayload(
            id=task.id,
            runtime=task.runtime,
            client_id=task.client_id,
            assignment_token=token_a,
            deadline_ms=task.deadline_ms,
            gpu_required=task.gpu_required,
            payload={
                "executable_hash": task.executable_hash,
                "input_hash": task.input_hash,
            },
        )
        if worker_a.ws:
            try:
                await worker_a.ws.send_json(to_dict(TaskAssign(task=payload)))
            except Exception:
                log.warning("failed to push challenge task A", task_id=task.id)

        # Push to worker B (with challenge token)
        payload_b = TaskPayload(
            id=task.id,
            runtime=task.runtime,
            client_id=task.client_id,
            assignment_token=token_b,
            deadline_ms=task.deadline_ms,
            gpu_required=task.gpu_required,
            payload={
                "executable_hash": task.executable_hash,
                "input_hash": task.input_hash,
            },
        )
        if worker_b.ws:
            try:
                await worker_b.ws.send_json(to_dict(TaskAssign(task=payload_b)))
            except Exception:
                log.warning("failed to push challenge task B", task_id=task.id)

    def stop(self):
        self._running = False
