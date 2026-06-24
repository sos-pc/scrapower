"""Task lifecycle management.

States: PENDING → QUEUED → ASSIGNED → COMPLETED | FAILED | TIMEOUT

Each task has a unique assignment_token per assignment attempt to prevent races.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import Enum

import aiosqlite


class TaskState(str, Enum):
    PENDING = "pending"  # created, waiting for audio download
    DOWNLOADING = "downloading"  # yt-dlp in progress
    QUEUED = "queued"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    @classmethod
    def _missing_(cls, value):
        """Backward compat: map old DB value to COMPLETED."""
        if value == "validated":
            return cls.COMPLETED
        return None


VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.PENDING: {
        TaskState.DOWNLOADING,
        TaskState.QUEUED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.DOWNLOADING: {TaskState.QUEUED, TaskState.FAILED, TaskState.PENDING},
    TaskState.QUEUED: {TaskState.ASSIGNED, TaskState.CANCELLED},
    TaskState.ASSIGNED: {
        TaskState.COMPLETED,
        TaskState.TIMEOUT,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.TIMEOUT: {TaskState.QUEUED, TaskState.FAILED},
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}


@dataclass
class Task:
    id: str
    client_id: str
    state: TaskState = TaskState.PENDING
    definition_json: str = "{}"
    retries: int = 0
    max_retries: int = 3
    current_assignment_token: str | None = None
    assigned_worker_id: str | None = None
    assigned_at: float | None = None
    deadline_ms: int = 60000
    executable_hash: str = ""
    input_hash: str = ""
    runtime: str = "wasm"
    gpu_required: bool = False
    output_hash: str = ""
    error: str = ""
    task_type: str = "wasm"
    requirements_json: str = "{}"
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED)

    @property
    def can_retry(self) -> bool:
        return self.retries < self.max_retries


# --- Task lifecycle ---
# States: PENDING → QUEUED → ASSIGNED → VALIDATED/FAILED/TIMEOUT
# TIMEOUT can loop back to QUEUED if retries remain (max 3).
# Each assignment has a unique token to prevent double-execution.
class TaskManager:
    """Manages task lifecycle with atomic state transitions."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def create(
        self,
        task_id: str,
        client_id: str,
        runtime: str,
        executable_hash: str,
        input_hash: str,
        task_type: str = "wasm",
        requirements_json: str = "{}",
        max_retries: int = 3,
        deadline_ms: int = 60000,
        gpu_required: bool = False,
        initial_state: TaskState = TaskState.QUEUED,
    ) -> Task:
        now = time.time()
        task = Task(
            id=task_id,
            client_id=client_id,
            state=initial_state,
            runtime=runtime,
            executable_hash=executable_hash,
            input_hash=input_hash,
            task_type=task_type,
            requirements_json=requirements_json,
            max_retries=max_retries,
            deadline_ms=deadline_ms,
            gpu_required=gpu_required,
            created_at=str(now),
            updated_at=str(now),
        )
        cursor = await self._db.execute(
            """INSERT INTO tasks (id, client_id, state, definition_json, retries,
               executable_hash, input_hash, runtime, gpu_required, deadline_ms,
               max_retries, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.id,
                task.client_id,
                task.state,
                task.definition_json,
                task.retries,
                task.executable_hash,
                task.input_hash,
                task.runtime,
                int(task.gpu_required),
                task.deadline_ms,
                task.max_retries,
                task.created_at,
                task.updated_at,
            ),
        )
        # Increment blob ref_counts so GC doesn't delete them
        for h in (executable_hash, input_hash):
            if h:
                await self._db.execute(
                    "UPDATE blobs SET ref_count = ref_count + 1 WHERE hash = ?", (h,)
                )
        await self._db.commit()
        return task

    async def get(self, task_id: str) -> Task | None:
        cursor = cursor = await self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return Task(
            id=row["id"],
            client_id=row["client_id"],
            state=TaskState(row["state"]),
            retries=row["retries"],
            assigned_worker_id=row["assigned_worker_id"]
            if "assigned_worker_id" in row.keys()
            else None,
            gpu_required=bool(row["gpu_required"]) if "gpu_required" in row.keys() else False,
            executable_hash=row["executable_hash"] if "executable_hash" in row.keys() else "",
            input_hash=row["input_hash"] if "input_hash" in row.keys() else "",
            runtime=row["runtime"] if "runtime" in row.keys() else "wasm",
            output_hash=row["output_hash"] if "output_hash" in row.keys() else "",
            error=row["error"] if "error" in row.keys() else "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_queued(self, limit: int = 100) -> list[Task]:
        cursor = cursor = await self._db.execute(
            "SELECT * FROM tasks WHERE state = ? ORDER BY created_at ASC LIMIT ?",
            (TaskState.QUEUED, limit),
        )
        tasks = []
        async for row in cursor:
            tasks.append(
                Task(
                    id=row["id"],
                    client_id=row["client_id"],
                    state=TaskState(row["state"]),
                    retries=row["retries"],
                    executable_hash=row["executable_hash"]
                    if "executable_hash" in row.keys()
                    else "",
                    input_hash=row["input_hash"] if "input_hash" in row.keys() else "",
                    runtime=row["runtime"] if "runtime" in row.keys() else "wasm",
                    gpu_required=bool(row["gpu_required"])
                    if "gpu_required" in row.keys()
                    else False,
                    error=row["error"] if "error" in row.keys() else "",
                    task_type=row["task_type"] if "task_type" in row.keys() else "wasm",
                    requirements_json=row["requirements_json"] if "requirements_json" in row.keys() else "{}",
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return tasks

    async def transition(
        self,
        task_id: str,
        new_state: TaskState,
        assignment_token: str | None = None,
        worker_id: str | None = None,
    ) -> bool:
        """Atomically transition a task to a new state."""
        task = await self.get(task_id)
        if task is None:
            return False

        if new_state not in VALID_TRANSITIONS.get(task.state, set()):
            return False

        # Verify assignment_token for transitions from ASSIGNED
        if task.state == TaskState.ASSIGNED:
            if assignment_token and assignment_token != task.current_assignment_token:
                return False

        now = time.time()

        if new_state == TaskState.ASSIGNED:
            token = uuid.uuid4().hex
            cursor = await self._db.execute(
                """UPDATE tasks SET state = ?, updated_at = ?, current_assignment_token = ?,
                   assigned_worker_id = ?, assigned_at = ?
                   WHERE id = ? AND state = ?""",
                (new_state, str(now), token, worker_id, now, task_id, task.state),
            )
        elif new_state == TaskState.TIMEOUT:
            if task.can_retry:
                # Requeue
                cursor = await self._db.execute(
                    """UPDATE tasks SET state = ?, retries = retries + 1, updated_at = ?,
                       current_assignment_token = NULL, assigned_worker_id = NULL
                       WHERE id = ? AND state = ?""",
                    (TaskState.QUEUED, str(now), task_id, task.state),
                )
            else:
                cursor = await self._db.execute(
                    """UPDATE tasks SET state = ?, updated_at = ?
                       WHERE id = ? AND state = ?""",
                    (TaskState.FAILED, str(now), task_id, task.state),
                )
        else:
            cursor = await self._db.execute(
                "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ? AND state = ?",
                (new_state, str(now), task_id, task.state),
            )

        await self._db.commit()
        return cursor.rowcount > 0  # rowcount = rows actually updated by this statement

    async def assign(self, task_id: str, worker_id: str) -> tuple[bool, str]:
        """Assign a task to a worker. Returns (success, assignment_token)."""
        token = uuid.uuid4().hex
        now = time.time()
        cursor = cursor = await self._db.execute(
            """UPDATE tasks SET state = ?, current_assignment_token = ?,
               assigned_worker_id = ?, assigned_at = ?, updated_at = ?
               WHERE id = ? AND state = ?""",
            (TaskState.ASSIGNED, token, worker_id, now, str(now), task_id, TaskState.QUEUED),
        )
        await self._db.commit()
        success = cursor.rowcount > 0  # rowcount = rows actually updated by this statement
        return success, token

    async def complete(self, task_id: str, output_hash: str, assignment_token: str = "") -> bool:
        """Mark a task as validated. Verifies assignment_token if provided."""
        # Always verify token (reject if missing or mismatched)
        if not assignment_token:
            return False
        cursor = await self._db.execute(
            "SELECT current_assignment_token FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        if not row or row["current_assignment_token"] != assignment_token:
            return False
        now = time.time()
        cursor = await self._db.execute(
            "UPDATE tasks SET output_hash = ?, updated_at = ? WHERE id = ?",
            (output_hash, str(now), task_id),
        )
        # Increment output blob ref_count so GC doesn't delete it
        if output_hash:
            await self._db.execute(
                "UPDATE blobs SET ref_count = ref_count + 1 WHERE hash = ?", (output_hash,)
            )
        await self._db.commit()
        return await self.transition(task_id, TaskState.COMPLETED)

    async def create_challenge(self, task_id: str, token_a: str, token_b: str) -> None:
        """Create a challenge record for double-execution verification."""
        await self._db.execute(
            "INSERT INTO challenges (task_id, token_a, token_b) VALUES (?, ?, ?)",
            (task_id, token_a, token_b),
        )
        await self._db.commit()

    async def resolve_challenge(
        self, task_id: str, assignment_token: str, output_hash: str
    ) -> bool:
        """Handle a challenge result. Returns True if challenge passed and task should complete."""
        cursor = await self._db.execute(
            "SELECT * FROM challenges WHERE task_id = ? AND status = 'pending'",
            (task_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return True  # No active challenge, regular flow � complete normally

        # Determine if this is worker A or B
        col = "result_a" if assignment_token == row["token_a"] else "result_b"
        await self._db.execute(
            f"UPDATE challenges SET {col} = ? WHERE task_id = ?",
            (output_hash, task_id),
        )
        await self._db.commit()

        # Check if both results are in
        cursor = await self._db.execute("SELECT * FROM challenges WHERE task_id = ?", (task_id,))
        row = await cursor.fetchone()
        if row and row["result_a"] and row["result_b"]:
            if row["result_a"] == row["result_b"]:
                await self._db.execute(
                    "UPDATE challenges SET status = 'matched' WHERE task_id = ?",
                    (task_id,),
                )
                await self._db.commit()
                return True  # Challenge passed!
            else:
                await self._db.execute(
                    "UPDATE challenges SET status = 'mismatched' WHERE task_id = ?",
                    (task_id,),
                )
                await self._db.commit()
                return False  # Mismatch � task should be re-challenged
        return False  # Waiting for second result
