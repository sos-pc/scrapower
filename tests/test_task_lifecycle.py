"""Task state machine, assignment races, and completion integrity."""

from __future__ import annotations

from scrapower.coordinator import blob_store as bs
from scrapower.coordinator.task_manager import VALID_TRANSITIONS, TaskState


async def _mk(task_service, task_id: str, state: TaskState = TaskState.QUEUED):
    return await task_service.submit(
        task_id=task_id,
        client_id="c",
        runtime="python",
        executable_hash="",
        input_hash="",
        initial_state=state,
    )


async def test_assign_is_atomic_single_winner(db, task_service, task_manager):
    """Two workers racing for the same task: exactly one wins."""
    await _mk(task_service, "t" * 32)
    ok1, tok1 = await task_service.assign("t" * 32, "worker-1")
    ok2, tok2 = await task_service.assign("t" * 32, "worker-2")

    assert ok1 is True and tok1
    assert ok2 is False, "the second worker must lose the race"
    task = await task_manager.get("t" * 32)
    assert task.assigned_worker_id == "worker-1"
    assert task.current_assignment_token == tok1


async def test_complete_requires_matching_token(db, blob_dir, task_service):
    task_id = "b" * 32
    await _mk(task_service, task_id)
    ok, token = await task_service.assign(task_id, "worker-1")
    out = await bs.store_blob(db, blob_dir, b"result")

    assert await task_service.complete(task_id, out, "") is False, "empty token rejected"
    assert await task_service.complete(task_id, out, "wrong-token") is False
    assert await task_service.complete(task_id, out, token) is True


async def test_complete_rolls_back_when_output_blob_missing(db, task_service, task_manager):
    """A submit naming a blob that was never uploaded must leave no ghost result.

    The old code claimed the transaction would roll back but never called
    rollback(), so the orphan `UPDATE tasks SET output_hash` was flushed by the
    next commit() — a COMPLETED-looking task pointing at nothing.
    """
    task_id = "c" * 32
    await _mk(task_service, task_id)
    ok, token = await task_service.assign(task_id, "worker-1")

    assert await task_service.complete(task_id, "f" * 64, token) is False

    # Force a later commit on the shared connection: a missing rollback would
    # surface the discarded output_hash here.
    await db.execute("UPDATE tasks SET assigned_at = '1' WHERE id = ?", (task_id,))
    await db.commit()

    task = await task_manager.get(task_id)
    assert (task.output_hash or "") == "", "no ghost output_hash"
    assert task.state == TaskState.ASSIGNED, "task stays recoverable by requeue_stale"


async def test_timeout_requeues_until_max_retries_then_fails(db, task_service, task_manager):
    task_id = "d" * 32
    await _mk(task_service, task_id)

    for expected_retries in (1, 2, 3):
        ok, _ = await task_service.assign(task_id, "w")
        assert ok
        assert await task_manager.transition(task_id, TaskState.TIMEOUT)
        task = await task_manager.get(task_id)
        assert task.retries == expected_retries
        assert task.state == TaskState.QUEUED, "requeued while retries remain"

    # Fourth strike: retries are exhausted -> FAILED, not requeued.
    ok, _ = await task_service.assign(task_id, "w")
    assert await task_manager.transition(task_id, TaskState.TIMEOUT)
    assert (await task_manager.get(task_id)).state == TaskState.FAILED


async def test_requeue_stale_recovers_silent_worker(db, task_service, task_manager):
    task_id = "e" * 32
    await _mk(task_service, task_id)
    await task_service.assign(task_id, "worker-gone")
    # Backdate the assignment beyond the silence window.
    await db.execute("UPDATE tasks SET assigned_at = '1000000000.0' WHERE id = ?", (task_id,))
    await db.commit()

    assert await task_service.requeue_stale(silence_timeout_sec=90) == 1
    assert (await task_manager.get(task_id)).state == TaskState.QUEUED


async def test_fresh_assignment_is_not_requeued(db, task_service, task_manager):
    task_id = "f" * 32
    await _mk(task_service, task_id)
    await task_service.assign(task_id, "worker-alive")

    assert await task_service.requeue_stale(silence_timeout_sec=90) == 0
    assert (await task_manager.get(task_id)).state == TaskState.ASSIGNED


async def test_downloading_task_is_cancellable(task_service, task_manager):
    """Audit A5: a task used to be uncancellable while DOWNLOADING."""
    task_id = "g" * 32
    await _mk(task_service, task_id, state=TaskState.PENDING)
    await task_manager.transition(task_id, TaskState.DOWNLOADING)

    assert TaskState.CANCELLED in VALID_TRANSITIONS[TaskState.DOWNLOADING]
    assert await task_service.cancel(task_id) is True
    assert (await task_manager.get(task_id)).state == TaskState.CANCELLED


async def test_terminal_states_are_final(task_service, task_manager):
    for state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
        assert VALID_TRANSITIONS[state] == set(), f"{state} must be terminal"

    task_id = "h" * 32
    await _mk(task_service, task_id)
    await task_service.cancel(task_id)
    # Nothing may move a cancelled task again.
    assert await task_manager.transition(task_id, TaskState.ASSIGNED) is False
    assert await task_manager.transition(task_id, TaskState.QUEUED) is False


async def test_task_mirrors_every_stored_column(db, task_manager):
    """Audit §1: get()/get_queued() each forgot different columns, so deadline_ms
    always read 60000 and max_retries always 3 whatever was stored."""
    task_id = "j" * 32
    await task_manager.create(
        task_id=task_id,
        client_id="client-x",
        runtime="python",
        executable_hash="e" * 64,
        input_hash="i" * 64,
        task_type="whisper",
        requirements_json='{"gpu": true, "network": "outbound"}',
        max_retries=7,
        deadline_ms=900000,
        gpu_required=True,
        initial_state=TaskState.QUEUED,
    )

    for label, task in (
        ("get", await task_manager.get(task_id)),
        ("get_queued", (await task_manager.get_queued())[0]),
    ):
        assert task.deadline_ms == 900000, f"{label} must read the stored deadline"
        assert task.max_retries == 7, f"{label} must read the stored max_retries"
        assert task.task_type == "whisper", label
        assert task.requirements_json == '{"gpu": true, "network": "outbound"}', label
        assert task.gpu_required is True, label
        assert task.runtime == "python", label
        assert task.executable_hash == "e" * 64, label
        assert task.input_hash == "i" * 64, label
        assert task.client_id == "client-x", label


async def test_can_retry_uses_the_stored_max_retries(db, task_manager):
    """transition() reads can_retry via get(); a hardcoded 3 broke retry logic."""
    task_id = "k" * 32
    await task_manager.create(
        task_id=task_id,
        client_id="c",
        runtime="python",
        executable_hash="",
        input_hash="",
        max_retries=1,
        initial_state=TaskState.QUEUED,
    )

    await task_manager.assign(task_id, "w")
    await task_manager.transition(task_id, TaskState.TIMEOUT)
    assert (await task_manager.get(task_id)).state == TaskState.QUEUED, "1 retry allowed"

    await task_manager.assign(task_id, "w")
    await task_manager.transition(task_id, TaskState.TIMEOUT)
    assert (
        await task_manager.get(task_id)
    ).state == TaskState.FAILED, "max_retries=1 exhausted, must not requeue again"


async def test_queued_task_carries_real_deadline_to_matching(db, task_manager):
    """_match_capabilities() compares deadline_ms against a worker's remaining
    lifetime; reading 60000 instead of 900000 would mis-assign long tasks."""
    from scrapower.coordinator.domain import _match_capabilities

    task_id = "m" * 32
    await task_manager.create(
        task_id=task_id,
        client_id="c",
        runtime="python",
        executable_hash="",
        input_hash="",
        task_type="whisper",
        deadline_ms=900000,
        gpu_required=True,
        initial_state=TaskState.QUEUED,
    )
    task = (await task_manager.get_queued())[0]

    dying_worker = {
        "task_types": ["whisper"],
        "runtimes": ["python"],
        "resources": {"ram_mb": 30720, "gpu": {"supported": True}},
        "network": {"connectivity": "outgoing_only"},
        "lifecycle": {"expected_remaining_sec": 300},  # 5 min < the real 15 min
    }
    assert _match_capabilities(task, dying_worker) is False


async def test_expired_pending_fails_with_reason_in_error_column(db, task_service, task_manager):
    """The reason belongs in `error`, not in `output_hash` (which clients read)."""
    task_id = "i" * 32
    await _mk(task_service, task_id, state=TaskState.PENDING)
    await db.execute("UPDATE tasks SET created_at = '1000000000.0' WHERE id = ?", (task_id,))
    await db.commit()

    await task_service.cleanup_expired(pending_ttl_sec=0)

    task = await task_manager.get(task_id)
    assert task.state == TaskState.FAILED
    assert "download lost" in (task.error or "")
    assert (task.output_hash or "") == "", "output_hash must not be used for messages"
