"""Scheduler — matches queued tasks with compatible workers.

Runs as a background loop. Pushes task_assign directly to worker WebSockets.
Uses domain services for business logic.
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
    ):
        self._tasks = task_service
        self._sm = session_manager
        self._tick = tick_sec
        self._policy = SchedulingPolicy(enforce_segregation=enforce_segregation)
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

        log.info("active workers: %d, queued tasks: %d", len(workers), len(tasks))

        for task in tasks:
            if task.is_terminal:
                continue

            compatible = self._policy.match(task, workers)
            if not compatible:
                continue

            # Best worker: external first, idle first. Skip embedded for untrusted tasks.
            worker = compatible[0]
            if worker.worker_id == "_embedded":
                continue  # Skip embedded - only for trusted/system tasks
            log.info(
                "assigning task %s to %s (in_progress=%d)",
                task.id[:8],
                worker.worker_id,
                worker.tasks_in_progress,
            )

            success, token = await self._tasks.assign(task.id, worker.worker_id)
            if not success:
                continue  # taken by another tick

            worker.tasks_in_progress += 1

            # Push to worker
            if worker.ws:
                log.info(
                    "pushing task %s to worker %s (exec=%s)",
                    task.id,
                    worker.worker_id,
                    task.executable_hash[:16],
                )
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

    def stop(self):
        self._running = False
