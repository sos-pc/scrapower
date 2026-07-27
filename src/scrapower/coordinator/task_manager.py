"""Task lifecycle management.

States: PENDING → QUEUED → ASSIGNED → COMPLETED | FAILED | TIMEOUT

Each task has a unique assignment_token per assignment attempt to prevent races.
"""

from __future__ import annotations

import logging
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
    TaskState.DOWNLOADING: {
        TaskState.QUEUED,
        TaskState.FAILED,
        TaskState.PENDING,
        TaskState.CANCELLED,
    },
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
    runtime: str = "python"
    gpu_required: bool = False
    output_hash: str = ""
    error: str = ""
    task_type: str = "whisper"
    requirements_json: str = "{}"
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED)

    @property
    def can_retry(self) -> bool:
        return self.retries < self.max_retries


def _col(row, name: str, default):
    """Read a column, tolerating its absence.

    db.py applies migrations best-effort (``ALTER TABLE`` in a try/except), so a
    column added later may be missing on an old database. Reads go through here
    rather than being guarded ad hoc — the previous code checked the columns that
    were always present and read the migration-added ones unguarded, which was
    exactly backwards.
    """
    try:
        if name not in row.keys():
            return default
    except AttributeError:  # not a sqlite3.Row-like mapping
        return default
    value = row[name]
    return default if value is None else value


def _row_to_task(row) -> Task:
    """Rebuild a Task from a ``SELECT *`` row.

    Single source of truth for get() and get_queued(): both used to reconstruct
    Task by hand and each forgot different columns, so deadline_ms silently read
    60000 and max_retries 3 whatever the database said — making
    ``task.can_retry`` and ``_match_capabilities()`` reason on wrong values.
    """
    assigned_at = _col(row, "assigned_at", None)
    return Task(
        id=row["id"],
        client_id=row["client_id"],
        state=TaskState(row["state"]),
        definition_json=_col(row, "definition_json", "{}"),
        retries=int(_col(row, "retries", 0)),
        max_retries=int(_col(row, "max_retries", 3)),
        current_assignment_token=_col(row, "current_assignment_token", None),
        assigned_worker_id=_col(row, "assigned_worker_id", None),
        assigned_at=float(assigned_at) if assigned_at else None,
        deadline_ms=int(_col(row, "deadline_ms", 60000)),
        executable_hash=_col(row, "executable_hash", ""),
        input_hash=_col(row, "input_hash", ""),
        runtime=_col(row, "runtime", "python"),
        gpu_required=bool(_col(row, "gpu_required", False)),
        output_hash=_col(row, "output_hash", ""),
        error=_col(row, "error", ""),
        task_type=_col(row, "task_type", "whisper"),
        requirements_json=_col(row, "requirements_json", "{}"),
        created_at=_col(row, "created_at", ""),
        updated_at=_col(row, "updated_at", ""),
    )


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
        task_type: str = "whisper",
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
        await self._db.execute(
            """INSERT INTO tasks (id, client_id, state, definition_json, retries,
               executable_hash, input_hash, runtime, gpu_required, deadline_ms,
               max_retries, task_type, requirements_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                task.task_type,
                task.requirements_json,
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
        cursor = await self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return None if row is None else _row_to_task(row)

    async def get_queued(self, limit: int = 100) -> list[Task]:
        cursor = await self._db.execute(
            "SELECT * FROM tasks WHERE state = ? ORDER BY created_at ASC LIMIT ?",
            (TaskState.QUEUED, limit),
        )
        return [_row_to_task(row) async for row in cursor]

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
        cursor = await self._db.execute(
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
            logging.getLogger(__name__).warning(
                "complete rejected: empty token task=%s", task_id[:12]
            )
            return False
        cursor = await self._db.execute(
            "SELECT current_assignment_token FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        if not row:
            logging.getLogger(__name__).warning(
                "complete rejected: task not found task=%s", task_id[:12]
            )
            return False
        if row["current_assignment_token"] != assignment_token:
            logging.getLogger(__name__).warning(
                "complete rejected: token mismatch task=%s db=%s... submit=%s...",
                task_id[:12],
                (row["current_assignment_token"] or "none")[:12],
                assignment_token[:12],
            )
            return False
        now = time.time()
        cursor = await self._db.execute(
            "UPDATE tasks SET output_hash = ?, updated_at = ? WHERE id = ?",
            (output_hash, str(now), task_id),
        )
        # Increment output blob ref_count so GC doesn't delete it.
        # The rowcount also serves as an implicit existence check: if the
        # worker never uploaded the blob (or uploaded a different one),
        # rowcount=0 and we reject the submit — preventing ghost tasks
        # where COMPLETED points to a nonexistent result.
        if output_hash:
            cursor = await self._db.execute(
                "UPDATE blobs SET ref_count = ref_count + 1 WHERE hash = ?", (output_hash,)
            )
            if cursor.rowcount == 0:
                # Blob not found. Roll back so the UPDATE tasks (output_hash)
                # above is discarded — otherwise it lingers uncommitted and
                # gets flushed by the next commit(), leaving a ghost result on
                # an ASSIGNED task. Task stays ASSIGNED; requeue_stale recovers
                # it after deadline_ms.
                await self._db.rollback()
                logging.getLogger(__name__).warning(
                    "complete rejected: blob not found task=%s hash=%s",
                    task_id[:12],
                    output_hash[:12],
                )
                return False
        await self._db.commit()
        return await self.transition(task_id, TaskState.COMPLETED)
