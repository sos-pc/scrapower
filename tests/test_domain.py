"""Unit tests for SchedulingPolicy — pure logic, no server."""

from __future__ import annotations

from scrapower.coordinator.domain import SchedulingPolicy
from scrapower.coordinator.task_manager import Task, TaskState
from scrapower.coordinator.worker_gateway.session import WorkerSession


def _worker(worker_id: str, runtimes=None, gpu=False, ram=8192, remaining=None):
    """Create a WorkerSession with given capabilities."""
    s = WorkerSession(
        session_id=f"sid-{worker_id}",
        worker_id=worker_id,
    )
    s.capabilities = {
        "runtimes": runtimes or ["wasm"],
        "resources": {
            "ram_mb": ram,
            "gpu": {"supported": gpu},
        },
        "lifecycle": {
            "expected_remaining_sec": remaining,
        },
    }
    return s


def _task(runtime="wasm", gpu_required=False, client_id="alice", deadline_ms=60000):
    """Create a Task for matching tests."""
    return Task(
        id="task-1",
        client_id=client_id,
        state=TaskState.QUEUED,
        runtime=runtime,
        gpu_required=gpu_required,
        deadline_ms=deadline_ms,
    )


class TestSchedulingPolicy:
    def test_match_returns_compatible_worker(self):
        policy = SchedulingPolicy()
        workers = [_worker("w1")]
        task = _task()
        result = policy.match(task, workers)
        assert len(result) == 1
        assert result[0].worker_id == "w1"

    def test_excludes_incompatible_runtime(self):
        policy = SchedulingPolicy()
        workers = [_worker("w1", runtimes=["wasm"])]
        task = _task(runtime="python")
        result = policy.match(task, workers)
        assert len(result) == 0

    def test_excludes_low_ram(self):
        policy = SchedulingPolicy()
        workers = [_worker("w1", ram=64)]  # below 128 MB
        task = _task()
        result = policy.match(task, workers)
        assert len(result) == 0

    def test_gpu_task_only_matches_gpu_worker(self):
        policy = SchedulingPolicy()
        workers = [
            _worker("cpu-only", gpu=False),
            _worker("gpu-worker", gpu=True),
        ]
        task = _task(gpu_required=True)
        result = policy.match(task, workers)
        assert len(result) == 1
        assert result[0].worker_id == "gpu-worker"

    def test_gpu_task_no_workers_returns_empty(self):
        policy = SchedulingPolicy()
        workers = [_worker("w1", gpu=False)]
        task = _task(gpu_required=True)
        result = policy.match(task, workers)
        assert len(result) == 0

    def test_segregation_excludes_same_client(self):
        policy = SchedulingPolicy(enforce_segregation=True)
        workers = [_worker("alice")]  # worker_id == client_id
        task = _task(client_id="alice")
        result = policy.match(task, workers)
        assert len(result) == 0

    def test_segregation_allows_different_client(self):
        policy = SchedulingPolicy(enforce_segregation=True)
        workers = [_worker("bob")]
        task = _task(client_id="alice")
        result = policy.match(task, workers)
        assert len(result) == 1

    def test_embedded_deprioritized(self):
        """Embedded worker should be last in preference order."""
        policy = SchedulingPolicy()
        workers = [
            _worker("external"),
            _worker("_embedded"),
        ]
        task = _task()
        result = policy.match(task, workers)
        assert result[0].worker_id == "external"
        assert result[1].worker_id == "_embedded"

    def test_idle_first(self):
        """Less busy workers preferred."""
        policy = SchedulingPolicy()
        w1 = _worker("busy")
        w1.tasks_in_progress = 5
        w2 = _worker("idle")
        w2.tasks_in_progress = 0
        task = _task()
        result = policy.match(task, [w1, w2])
        assert result[0].worker_id == "idle"

    def test_lifecycle_short_lived_excluded(self):
        """Worker with low remaining time shouldn't get long tasks."""
        policy = SchedulingPolicy()
        workers = [
            _worker("short-lived", remaining=30),  # 30s left
            _worker("long-lived", remaining=3600),
        ]
        task = _task(deadline_ms=120_000)  # 120s task
        result = policy.match(task, workers)
        assert len(result) == 1
        assert result[0].worker_id == "long-lived"

    def test_no_capabilities_excluded(self):
        policy = SchedulingPolicy()
        w = WorkerSession(session_id="s1", worker_id="no-cap")
        w.capabilities = None
        result = policy.match(_task(), [w])
        assert len(result) == 0
