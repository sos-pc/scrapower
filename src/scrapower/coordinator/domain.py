"""Domain services — pure business logic, no I/O, no framework.

These are the "what" of Scrapower. The coordinator modules (scheduler,
ws_handler, client_api) are the "how" — they adapt HTTP/WebSocket to
these services.
"""

from __future__ import annotations

from .task_manager import Task, TaskManager, TaskState
from .worker_gateway.session import WorkerSession


class TaskService:
    """Business rules for task lifecycle.

    Wraps TaskManager with validation, retry logic, and assignment
    token verification. The scheduler and API handlers call this
    instead of touching TaskManager directly.
    """

    def __init__(self, task_manager: TaskManager):
        self._tm = task_manager

    async def submit(
        self,
        task_id: str,
        client_id: str,
        runtime: str,
        executable_hash: str,
        input_hash: str,
        gpu_required: bool = False,
    ) -> Task:
        """Submit a new task. Returns the created Task."""
        return await self._tm.create(
            task_id=task_id,
            client_id=client_id,
            runtime=runtime,
            executable_hash=executable_hash,
            input_hash=input_hash,
            gpu_required=gpu_required,
        )

    async def assign(self, task_id: str, worker_id: str) -> tuple[bool, str]:
        """Try to assign a task to a worker. Returns (success, token)."""
        return await self._tm.assign(task_id, worker_id)

    async def complete(self, task_id: str, output_hash: str, assignment_token: str = "") -> bool:
        """Mark a task as validated."""
        return await self._tm.complete(task_id, output_hash, assignment_token)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a queued or assigned task."""
        return await self._tm.transition(task_id, TaskState.CANCELLED)

    async def get(self, task_id: str) -> Task | None:
        return await self._tm.get(task_id)

    async def get_queued(self, limit: int = 100) -> list[Task]:
        return await self._tm.get_queued(limit)

    async def create_challenge(self, task_id: str, token_a: str, token_b: str) -> None:
        """Create a challenge record for double-execution verification."""
        return await self._tm.create_challenge(task_id, token_a, token_b)

    async def requeue_stale(self, timeout_sec: float = 120) -> int:
        """Re-queue ASSIGNED tasks that haven't completed in time.
        Returns number of tasks requeued."""
        import time

        now = time.time()
        cursor = await self._tm._db.execute(
            "SELECT id FROM tasks WHERE state = ? AND assigned_at < ?",
            (TaskState.ASSIGNED, str(now - timeout_sec)),
        )
        count = 0
        async for row in cursor:
            await self._tm.transition(row["id"], TaskState.TIMEOUT)
            count += 1
        return count


class SchedulingPolicy:
    """Pure function: given a task and available workers, return
    the best candidates in preference order.

    This is extracted from Scheduler._match so it can be tested
    without a running server.
    """

    def __init__(self, enforce_segregation: bool = False):
        self._enforce_segregation = enforce_segregation

    def match(
        self,
        task: Task,
        workers: list[WorkerSession],
        reputations: dict[str, float] | None = None,
    ) -> list[WorkerSession]:
        """Return compatible workers sorted by preference (best first).

        Args:
            task: The task to match.
            workers: Available workers.
            reputations: Optional {worker_id: score} dict (0.0=blacklisted, 1.0=trusted).
                         Workers with score <= 0.0 are excluded.
        """
        if reputations is None:
            reputations = {}

        compatible = []
        for w in workers:
            if not w.capabilities:
                continue

            # Blacklist check (reputation score <= 0 means blacklisted)
            if reputations.get(w.worker_id, 0.5) <= 0.0:
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

            # GPU requirement
            if task.gpu_required and not resources.get("gpu", {}).get("supported", False):
                continue

            # Lifecycle: don't assign long tasks to short-lived workers
            lifecycle = w.capabilities.get("lifecycle", {})
            remaining = lifecycle.get("expected_remaining_sec")
            if remaining and remaining < task.deadline_ms / 1000:
                continue

            compatible.append(w)

        # Shuffle for fairness, then sort by load & reputation (idle + trusted first)
        import random

        random.shuffle(compatible)
        compatible.sort(
            key=lambda w: (
                1 if w.worker_id == "_embedded" else 0,
                w.tasks_in_progress,
                -reputations.get(w.worker_id, 0.5),  # higher reputation = lower sort key
            )
        )
        return compatible
