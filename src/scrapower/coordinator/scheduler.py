"""Scheduler -- matches queued tasks with compatible workers.

Runs as a background loop. Pushes task_assign directly to worker WebSockets.
"""

from __future__ import annotations

import asyncio
import logging

from .domain import SchedulingPolicy, TaskService
from .protocol import TaskAssign, TaskPayload, to_dict
from .worker_gateway.session import SessionManager

log = logging.getLogger(__name__)


class Scheduler:
    """Matches tasks to workers based on capabilities and lifecycle."""

    def __init__(
        self,
        task_service: TaskService,
        session_manager: SessionManager,
        tick_sec: float = 5.0,
        enforce_segregation: bool = False,
        verification_mode: str = "trust",
        ws_assign_enabled: bool = True,
    ):
        self._tasks = task_service
        self._sm = session_manager
        self._tick = tick_sec
        self._verification = verification_mode
        self._policy = SchedulingPolicy(enforce_segregation=enforce_segregation)
        self._ws_assign = ws_assign_enabled
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

        # Mode B workers pull via HTTP — WS push is secondary.
        if not self._ws_assign:
            return

        workers = self._sm.active_sessions
        if not workers:
            return

        log.info(
            "active workers: %d, queued tasks: %d",
            len(workers),
            len(tasks),
        )

        for task in tasks:
            if task.is_terminal:
                continue

            compatible = self._policy.match(task, workers)
            if not compatible:
                continue

            # Best worker: external first, idle first.
            worker = compatible[0]
            if worker.worker_id == "_embedded":
                pass  # TEMP: allow for debugging

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
            "challenge: task %s â†’ %s + %s",
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
