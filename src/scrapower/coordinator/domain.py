"""Domain services — pure business logic, no I/O, no framework.

These are the "what" of Scrapower. The coordinator modules (scheduler,
ws_handler, client_api) are the "how" — they adapt HTTP/WebSocket to
these services.
"""

from __future__ import annotations

from .config import Config
from .task_manager import Task, TaskManager, TaskState


class TaskService:
    """Business rules for task lifecycle.

    Wraps TaskManager with validation, retry logic, and assignment
    token verification. The scheduler and API handlers call this
    instead of touching TaskManager directly.
    """

    def __init__(self, task_manager: TaskManager, db, config: Config):
        self._tm = task_manager
        self._db = db
        self._config = config

    async def submit(
        self,
        task_id: str,
        client_id: str,
        runtime: str,
        executable_hash: str,
        input_hash: str,
        task_type: str = "whisper",
        requirements_json: str = "{}",
        gpu_required: bool = False,
        deadline_ms: int = 60000,
        initial_state: TaskState = TaskState.QUEUED,
    ) -> Task:
        """Submit a new task. Returns the created Task."""
        return await self._tm.create(
            task_id=task_id,
            client_id=client_id,
            runtime=runtime,
            executable_hash=executable_hash,
            input_hash=input_hash,
            task_type=task_type,
            requirements_json=requirements_json,
            gpu_required=gpu_required,
            deadline_ms=deadline_ms,
            initial_state=initial_state,
        )

    async def set_state(self, task_id: str, new_state: TaskState) -> bool:
        """Transition a task to a new state (e.g. PENDING → DOWNLOADING)."""
        return await self._tm.transition(task_id, new_state)

    async def run_prepare(
        self,
        task_id: str,
        prepare_fn,
        log=None,
        max_retries: int = 2,
    ) -> bool:
        """Run a prepare function in background, managing PENDING→QUEUED lifecycle.

        Calls prepare_fn() which must return an input_hash (str).
        Retries up to max_retries times on failure (e.g. transient yt-dlp errors).
        On success: PENDING → DOWNLOADING → QUEUED (with input_hash).
        On final failure: marks task FAILED with the exception message."""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0 and log:
                    log.info("prepare: retry %d/%d for %s", attempt, max_retries, task_id[:12])
                await self.set_state(task_id, TaskState.DOWNLOADING)
                input_hash = await prepare_fn()
                ok = await self.set_queued(task_id, input_hash)
                if ok and log:
                    log.info("prepare: task %s queued", task_id[:12])
                return ok
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await self._tm.transition(task_id, TaskState.PENDING)  # reset for retry
                    import asyncio

                    await asyncio.sleep(2**attempt)  # 0s, 2s, 4s...
        # All retries exhausted
        if log:
            log.error(
                "prepare: failed for %s after %d retries: %s",
                task_id[:12],
                max_retries,
                str(last_error)[:200],
            )
        await self.mark_failed(task_id, str(last_error)[:4096])
        return False

    async def set_queued(self, task_id: str, input_hash: str) -> bool:
        """Set a PENDING/DOWNLOADING task to QUEUED with its audio input hash."""
        task = await self.get(task_id)
        if not task or task.state not in (TaskState.PENDING, TaskState.DOWNLOADING):
            return False
        # Update input_hash and transition to QUEUED
        import time

        await self._tm._db.execute(
            "UPDATE tasks SET input_hash = ?, updated_at = ? WHERE id = ?",
            (input_hash, str(time.time()), task_id),
        )
        await self._tm._db.execute(
            "UPDATE blobs SET ref_count = ref_count + 1 WHERE hash = ?", (input_hash,)
        )
        await self._tm._db.commit()
        return await self._tm.transition(task_id, TaskState.QUEUED)

    async def mark_failed(self, task_id: str, reason: str = "") -> bool:
        """Mark a task as FAILED with an error message."""
        import time as _time

        now = _time.time()
        await self._tm._db.execute(
            "UPDATE tasks SET error = ?, updated_at = ? WHERE id = ?",
            (reason, str(now), task_id),
        )
        await self._tm._db.commit()
        return await self._tm.transition(task_id, TaskState.FAILED)

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

    async def count_queued(self) -> int:
        """Nombre de tâches en attente. COUNT(*), zéro objet chargé."""
        cursor = await self._tm._db.execute(
            "SELECT COUNT(*) as n FROM tasks WHERE state = 'queued'"
        )
        row = await cursor.fetchone()
        return row["n"] if row else 0

    async def requeue_stale(self, silence_timeout_sec: float = 90) -> int:
        """Re-queue ASSIGNED tasks whose worker hasn't signalled recently.

        Uses transition(TIMEOUT) which handles can_retry, retries++,
        token cleanup, and routes to QUEUED or FAILED. The TIMEOUT
        state is never actually persisted — transition() redirects
        to QUEUED (retry) or FAILED (max retries exhausted).

        Args:
            silence_timeout_sec: seconds without any signal before timeout.
                Default 90s (3 heartbeat intervals at 30s).

        Returns number of tasks requeued."""
        import time as _time

        now = _time.time()
        cursor = await self._tm._db.execute(
            "SELECT id FROM tasks WHERE state = ? AND assigned_at < ?",
            (TaskState.ASSIGNED, str(now - silence_timeout_sec)),
        )
        rows = await cursor.fetchall()
        count = 0
        for row in rows:
            if await self._tm.transition(row["id"], TaskState.TIMEOUT):
                count += 1
        if count:
            import logging

            logging.getLogger(__name__).info(
                "requeued %d stale tasks (silence > %ds)",
                count,
                silence_timeout_sec,
            )
        return count

    async def requeue_for_worker(self, worker_id: str) -> int:
        """Requeue all tasks assigned to a dead worker.

        Bridge between zombie_watchdog (session lifecycle) and
        requeue_stale (task lifecycle). Called immediately when
        a WS session is detected as dead - no need to wait for
        the periodic requeue_stale scan (up to 90s).
        """
        import time as _time

        now = _time.time()
        cursor = await self._tm._db.execute(
            "UPDATE tasks SET state = ?, updated_at = ? WHERE state = ? AND assigned_worker_id = ?",
            (TaskState.TIMEOUT, str(now), TaskState.ASSIGNED, worker_id),
        )
        await self._tm._db.commit()
        return cursor.rowcount

    async def cleanup_expired(
        self, completed_ttl_sec: float = 2592000, pending_ttl_sec: float = 3600
    ) -> int:
        """Delete expired tasks, their log files, and release blob refs.

        - COMPLETED/FAILED/CANCELLED > completed_ttl_sec → deleted + log removed
        - PENDING > pending_ttl_sec → marked FAILED

        Default completed_ttl_sec: 2592000 (30 days).
        Returns number of tasks cleaned up."""
        import time as _time
        from pathlib import Path as _Path

        now = _time.time()
        cleaned = 0

        # Terminal tasks older than TTL → delete, release blob refs, remove logs
        cursor = await self._tm._db.execute(
            """SELECT id, executable_hash, input_hash, output_hash FROM tasks
               WHERE state IN ('completed', 'failed', 'cancelled')
                 AND updated_at < ?""",
            (str(now - completed_ttl_sec),),
        )
        async for row in cursor:
            for h in (row["executable_hash"], row["input_hash"], row["output_hash"]):
                if h:
                    await self._tm._db.execute(
                        "UPDATE blobs SET ref_count = MAX(0, ref_count - 1) WHERE hash = ?", (h,)
                    )
            # Remove associated log file
            try:
                log_path = _Path("data/logs") / f"{row['id']}.log"
                if log_path.exists():
                    log_path.unlink()
            except OSError:
                pass
            await self._tm._db.execute("DELETE FROM tasks WHERE id = ?", (row["id"],))
            cleaned += 1

        # PENDING tasks stuck > pending_ttl_sec → FAILED
        cursor = await self._tm._db.execute(
            """UPDATE tasks SET state = 'failed',
                   output_hash = 'download lost after coordinator restart',
                   updated_at = ?
               WHERE state = 'pending' AND created_at < ?""",
            (str(now), str(now - pending_ttl_sec)),
        )
        cleaned += cursor.rowcount

        await self._tm._db.commit()
        return cleaned


def _match_capabilities(task, capabilities: dict) -> bool:
    """Check if a worker's capabilities can execute this task."""
    import json as _json

    worker_types = capabilities.get("task_types") or capabilities.get("runtimes", [])
    worker_runtimes = capabilities.get("runtimes", ["python"])
    if task.task_type not in worker_types:
        return False
    if task.runtime not in worker_runtimes:
        return False

    if task.gpu_required and not capabilities.get("resources", {}).get("gpu", {}).get(
        "supported", False
    ):
        return False

    try:
        reqs = _json.loads(task.requirements_json) if task.requirements_json else {}
    except (_json.JSONDecodeError, TypeError):
        reqs = {}
    ram_req = max(reqs.get("ram_mb", 0), 128)  # floor: 128 MB minimum
    if capabilities.get("resources", {}).get("ram_mb", 0) < ram_req:
        return False

    net_req = reqs.get("network")
    if (
        net_req == "outbound"
        and capabilities.get("network", {}).get("connectivity") != "outgoing_only"
    ):
        return False

    lifecycle = capabilities.get("lifecycle", {})
    remaining = lifecycle.get("expected_remaining_sec")
    if remaining and remaining < task.deadline_ms / 1000:
        return False

    return True
