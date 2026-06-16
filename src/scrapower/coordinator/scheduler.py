"""Scheduler — matches queued tasks with compatible workers.

Runs as a background loop. Pushes task_assign directly to worker WebSockets.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .task_manager import TaskManager, TaskState
from .worker_gateway.session import SessionManager, WorkerSession

log = logging.getLogger(__name__)


class Scheduler:
    """Matches tasks to workers based on capabilities and lifecycle."""

    def __init__(
        self,
        task_manager: TaskManager,
        session_manager: SessionManager,
        tick_sec: float = 5.0,
        enforce_segregation: bool = False,
    ):
        self._tm = task_manager
        self._sm = session_manager
        self._tick = tick_sec
        self._enforce_segregation = enforce_segregation
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
        """One tick of the scheduler."""
        tasks = await self._tm.get_queued(limit=100)
        await self._check_stale_assignments()
        if not tasks:
            return

        workers = self._sm.active_sessions
        log.info("active workers: %d", len(workers))
        if not workers:
            return

        log.info("active workers: %d", len(workers))
        log.info("queued tasks: %d", len(tasks))

        for task in tasks:
            if task.is_terminal:
                continue

            # Match workers to task by capabilities
            compatible = self._match(task, workers)
            if not compatible:
                log.info("no compatible worker for task %s (runtime=%s)", task.id[:8], task.runtime)
                continue

            # Pick best worker: external workers first, idle workers first
            worker = compatible[0]
            log.info(
                "assigning task %s to %s (in_progress=%d)",
                task.id[:8],
                worker.worker_id,
                worker.tasks_in_progress,
            )

            # Assign
            success, token = await self._tm.assign(task.id, worker.worker_id)
            if not success:
                continue  # task was taken by another scheduler tick

            # Update in-tick counter so next task goes to a different worker
            worker.tasks_in_progress += 1

            # Push task_assign to worker
            if worker.ws:
                log.info(
                    "assigning task %s to worker %s (exec=%s)",
                    task.id,
                    worker.worker_id,
                    task.executable_hash[:16],
                )
                try:
                    await worker.ws.send_json(
                        {
                            "type": "task_assign",
                            "task": {
                                "id": task.id,
                                "definition_hash": "",
                                "runtime": task.runtime,
                                "client_id": task.client_id,
                                "assignment_token": token,
                                "resources_required": {
                                    "cpu_cores_min": 1,
                                    "ram_mb_min": 128,
                                    "gpu_required": task.gpu_required,
                                },
                                "deadline_ms": task.deadline_ms,
                                "payload": {
                                    "executable_hash": task.executable_hash,
                                    "input_hash": task.input_hash,
                                },
                                "verification": {"mode": "trust"},
                                "reward": {"base_credit": 100},
                            },
                        }
                    )
                except Exception:
                    log.warning(
                        "failed to push task to worker", task_id=task.id, worker_id=worker.worker_id
                    )
                    # Task stays ASSIGNED, will timeout and be requeued
                    continue

    def _match(self, task, workers: list[WorkerSession]) -> list[WorkerSession]:
        """Find workers compatible with a task."""
        compatible = []
        for w in workers:
            if not w.capabilities:
                continue

            # Segregation rule
            if self._enforce_segregation and w.worker_id == task.client_id:
                continue

            # Runtime compatibility
            if task.runtime not in w.capabilities.get("runtimes", []):
                continue

            # Resource check
            resources = w.capabilities.get("resources", {})
            if resources.get("ram_mb", 0) < 128:
                continue

            # GPU check: if task requires GPU, worker must support it
            if task.gpu_required and not resources.get("gpu", {}).get("supported", False):
                continue

            # Lifecycle check: don't assign long tasks to short-lived workers
            lifecycle = w.capabilities.get("lifecycle", {})
            remaining = lifecycle.get("expected_remaining_sec")
            if remaining and remaining < task.deadline_ms / 1000:
                continue

            compatible.append(w)

        # Sort by reputation/fitness (simple: more idle = preferred)
        import random

        random.shuffle(compatible)
        compatible.sort(key=lambda w: (1 if w.worker_id == "_embedded" else 0, w.tasks_in_progress))
        return compatible

    async def _check_stale_assignments(self):
        """Re-queue tasks that have been ASSIGNED too long."""

        now = time.time()
        cursor = await self._tm._db.execute(
            "SELECT id FROM tasks WHERE state = ? AND assigned_at < ?",
            (TaskState.ASSIGNED, str(now - 120)),  # 2 min timeout
        )
        async for row in cursor:
            log.warning("task %s timed out, requeueing", row["id"])
            await self._tm.transition(row["id"], TaskState.TIMEOUT)

    def stop(self):
        self._running = False
