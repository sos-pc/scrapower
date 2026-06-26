"""Worker tracking for Mode B (HTTP pull) workers.

Tracks ephemeral workers via lightweight timestamp entries.
Workers touch in on pull/heartbeat; stale entries are purged.
"""

from __future__ import annotations

import time


class SessionManager:
    """Tracks Mode B worker liveness via pull/heartbeat timestamps."""

    def __init__(self, heartbeat_interval_sec: int = 10, heartbeat_miss_threshold: int = 3):
        # Mode B workers (HTTP pull) don't create sessions — we track
        # them via a simple last_seen timestamp, updated on pull/heartbeat.
        self._mode_b_workers: dict[str, float] = {}  # worker_id → last_seen

    def touch_mode_b(self, worker_id: str) -> None:
        """Record that a Mode B worker is alive (called on pull/heartbeat).

        Mode B workers (Kaggle, Modal, HF Spaces) use stateless HTTP pull
        instead of persistent connections. This lightweight tracking gives
        the harvester visibility into how many workers are actually connected."""
        self._mode_b_workers[worker_id] = time.time()

    def mode_b_active_count(self, prefix: str = "", max_age_sec: float = 90) -> int:
        """Count Mode B workers seen recently, optionally filtered by prefix.

        Workers that haven't been seen in max_age_sec are purged and not
        counted. The default 90s covers 3 heartbeat intervals (30s each)
        during task execution."""
        now = time.time()
        stale = [wid for wid, ts in self._mode_b_workers.items() if now - ts > max_age_sec]
        for wid in stale:
            del self._mode_b_workers[wid]
        if prefix:
            return sum(
                1
                for wid, ts in self._mode_b_workers.items()
                if wid.startswith(prefix) and now - ts <= max_age_sec
            )
        return len(self._mode_b_workers)
