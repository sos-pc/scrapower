"""Config reachability and the single staleness deadline (audit §6).

Two problems this covers: most fields could not be set in production at all
(no TOML reaches the container and the env map skipped them), and the
"how long before a worker is dead" value existed in four places with
unrelated values.
"""

from __future__ import annotations

import types

import pytest

from scrapower.coordinator.config import Config, load_config
from scrapower.coordinator.domain import TaskService
from scrapower.coordinator.task_manager import TaskManager, TaskState
from scrapower.coordinator.worker_gateway.session import SessionManager

# Fields that used to be loaded but read by nothing — dead knobs, now removed.
REMOVED_DEAD_FIELDS = [
    "max_task_retries",
    "task_accept_timeout_sec",
    "max_anonymous_workers",
    "keepalive_enabled",
    "keepalive_duration_sec",
    "default_verification_mode",
]


@pytest.mark.parametrize("field", REMOVED_DEAD_FIELDS)
def test_dead_config_fields_are_gone(field):
    assert not hasattr(Config(), field), f"{field} is read by nothing and should not exist"


# ── The derived staleness deadline ─────────────────────────────────────────


def test_stale_after_is_derived_from_the_heartbeat():
    cfg = Config()
    assert cfg.stale_after_sec == cfg.heartbeat_interval_sec * cfg.heartbeat_miss_threshold


def test_default_stale_after_preserves_the_previous_effective_value():
    """The old code hardcoded 90 in two places; the derived value must match,
    otherwise this refactor silently changes when workers get requeued."""
    assert Config().stale_after_sec == 90


def test_heartbeat_default_matches_what_workers_actually_send():
    """Config used to say 10s while workers heartbeat every 30s, so deriving
    10 x 3 = 30 would have requeued live workers mid-task."""
    from scrapower.worker.loop import HEARTBEAT_INTERVAL_SEC

    assert Config().heartbeat_interval_sec == HEARTBEAT_INTERVAL_SEC


@pytest.mark.parametrize(
    ("interval", "threshold", "expected"),
    [("30", "3", 90), ("10", "6", 60), ("60", "2", 120)],
)
def test_staleness_is_configurable_via_env(monkeypatch, interval, threshold, expected):
    monkeypatch.setenv("SCRAPOWER_HEARTBEAT_INTERVAL_SEC", interval)
    monkeypatch.setenv("SCRAPOWER_HEARTBEAT_MISS_THRESHOLD", threshold)
    assert Config().stale_after_sec == expected


# ── Env overrides now cover the fields that are actually used ──────────────


@pytest.mark.parametrize(
    ("env_var", "attr", "value", "expected"),
    [
        ("SCRAPOWER_HEARTBEAT_INTERVAL_SEC", "heartbeat_interval_sec", "45", 45),
        ("SCRAPOWER_HEARTBEAT_MISS_THRESHOLD", "heartbeat_miss_threshold", "5", 5),
        ("SCRAPOWER_CHECKPOINT_TTL_DAYS", "checkpoint_ttl_days", "14", 14),
        ("SCRAPOWER_BLOB_TTL_DAYS", "blob_ttl_days", "3", 3),
        ("SCRAPOWER_MAX_BLOB_SIZE_MB", "max_blob_size_mb", "25", 25),
        ("SCRAPOWER_DELIVERY_INTERVAL_SEC", "delivery_interval_sec", "60", 60),
        ("SCRAPOWER_TRANSCRIPTS_DIR", "transcripts_dir", "/tmp/t", "/tmp/t"),
        ("SCRAPOWER_LOG_LEVEL", "log_level", "DEBUG", "DEBUG"),
    ],
)
def test_env_override_reaches_the_field(monkeypatch, env_var, attr, value, expected):
    """In production only env vars get through — no TOML is mounted in the
    container — so every live field needs an override."""
    monkeypatch.setenv(env_var, value)
    assert getattr(Config(), attr) == expected


def _env_mapped_fields() -> set[str]:
    """Field names the env override map can reach."""
    import inspect
    import re

    src = inspect.getsource(Config._apply_env_overrides)
    return set(re.findall(r'"SCRAPOWER_[A-Z_]+":\s*\("(\w+)"', src))


def test_every_live_field_is_reachable_from_the_environment():
    """Guard against adding a field production can never set: in the container
    no TOML is mounted, so an env var is the only way in."""
    mapped = _env_mapped_fields()
    # blob_dir/db_path are derived from data_dir, which is itself overridable.
    exempt = {"blob_dir", "db_path"}

    unreachable = {f for f in vars(Config()) if f not in mapped} - exempt

    assert unreachable == set(), f"fields unreachable in production: {sorted(unreachable)}"


def test_env_map_only_targets_real_fields():
    cfg = Config()
    for attr in _env_mapped_fields():
        assert hasattr(cfg, attr), f"env map points at non-existent field {attr}"


# ── The deadline is honoured end to end ───────────────────────────────────


def test_session_manager_stores_its_arguments():
    """They used to be accepted and dropped, pinning the window at 90."""
    sm = SessionManager(heartbeat_interval_sec=20, heartbeat_miss_threshold=4)
    assert sm.heartbeat_interval_sec == 20
    assert sm.heartbeat_miss_threshold == 4
    assert sm.stale_after_sec == 80


def test_active_count_window_follows_the_config():
    sm = SessionManager(heartbeat_interval_sec=1, heartbeat_miss_threshold=1)
    sm.touch_mode_b("w1")
    # Backdate the sighting past the (tiny) configured window.
    sm._mode_b_workers["w1"] -= 5
    assert sm.mode_b_active_count() == 0, "must use stale_after_sec, not a hardcoded 90"


async def test_requeue_stale_uses_the_configured_deadline(db, tmp_path):
    """A short configured deadline must requeue sooner than the old fixed 90s."""
    cfg = types.SimpleNamespace(blob_dir=str(tmp_path), stale_after_sec=10)
    tm = TaskManager(db)
    service = TaskService(tm, db, cfg)

    task_id = "n" * 32
    await service.submit(
        task_id=task_id,
        client_id="c",
        runtime="python",
        executable_hash="",
        input_hash="",
        initial_state=TaskState.QUEUED,
    )
    await service.assign(task_id, "worker-silent")

    # 30s of silence: under the old hardcoded 90 this would survive.
    import time

    await db.execute(
        "UPDATE tasks SET assigned_at = ? WHERE id = ?", (str(time.time() - 30), task_id)
    )
    await db.commit()

    assert await service.requeue_stale() == 1
    assert (await tm.get(task_id)).state == TaskState.QUEUED


def test_load_config_still_reads_a_toml_when_one_exists(tmp_path, monkeypatch):
    """The TOML path is unused in prod but remains for local/dev use."""
    toml = tmp_path / "coordinator.toml"
    toml.write_text(
        "[worker_gateway]\nheartbeat_interval_sec = 15\nheartbeat_miss_threshold = 2\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    cfg = load_config(str(toml))

    assert cfg.heartbeat_interval_sec == 15
    assert cfg.stale_after_sec == 30
