"""Session management for worker connections.

Tracks active sessions, handles heartbeat timeouts, manages worker state.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkerSession:
    """State for one connected worker."""

    session_id: str
    worker_id: str
    ws: Any = None  # WebSocket connection for pushing messages
    auth_level: int = 0
    capabilities: dict[str, Any] | None = None
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    tasks_in_progress: int = 0
    is_zombie: bool = False
    peer_ip: str = ""


class SessionManager:
    """Manages all active worker sessions."""

    def __init__(self, heartbeat_interval_sec: int = 10, heartbeat_miss_threshold: int = 3):
        self._sessions: dict[str, WorkerSession] = {}
        self._heartbeat_interval = heartbeat_interval_sec
        self._heartbeat_threshold = heartbeat_miss_threshold

    def create(
        self, worker_id: str, ws: Any = None, auth_level: int = 0, peer_ip: str = ""
    ) -> WorkerSession:
        """Create a new session for a worker."""
        session_id = uuid.uuid4().hex[:16]
        session = WorkerSession(
            session_id=session_id,
            worker_id=worker_id,
            ws=ws,
            auth_level=auth_level,
            peer_ip=peer_ip,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> WorkerSession | None:
        """Get session by ID."""
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> WorkerSession | None:
        """Remove and return a session."""
        return self._sessions.pop(session_id, None)

    def heartbeat(self, session_id: str) -> bool:
        """Record a heartbeat. Returns True if session is alive."""
        session = self._sessions.get(session_id)
        if session is None or session.is_zombie:
            return False
        session.last_heartbeat = time.time()
        return True

    def set_capabilities(self, session_id: str, capabilities: dict[str, Any]) -> bool:
        """Set worker capabilities. Returns True if session exists."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.capabilities = capabilities
        return True

    @property
    def active_sessions(self) -> list[WorkerSession]:
        """Return sessions that are not zombies."""
        return [s for s in self._sessions.values() if not s.is_zombie]

    @property
    def external_workers_connected(self) -> bool:
        """True if any non-embedded worker is connected."""
        return any(s.worker_id != "_embedded" and not s.is_zombie for s in self._sessions.values())

    async def zombie_watchdog(self, on_zombie=None):
        """Detect dead WS sessions. DOES NOT handle task lifecycle.

        Task requeue is handled by TaskService.requeue_stale (periodic scan)
        and TaskService.requeue_for_worker (immediate, via on_zombie callback).

        Args:
            on_zombie: Optional async callback(session) when zombie detected.
        """
        while True:
            now = time.time()
            timeout = self._heartbeat_interval * self._heartbeat_threshold
            for session in list(self._sessions.values()):
                if not session.is_zombie and (now - session.last_heartbeat) > timeout:
                    session.is_zombie = True
                    if on_zombie:
                        await on_zombie(session)
            # Cleanup: remove zombies older than 1 hour
            to_remove = [
                sid
                for sid, s in self._sessions.items()
                if s.is_zombie and (now - s.last_heartbeat) > 3600
            ]
            for sid in to_remove:
                session = self._sessions.pop(sid, None)
                if session:
                    # Flush final logs before the session disappears
                    try:
                        from pathlib import Path as _Path

                        log_dir = _Path("data/logs")
                        log_dir.mkdir(parents=True, exist_ok=True)
                        log_path = log_dir / f"{session.worker_id}.log"
                        import time as _time

                        with open(log_path, "a") as f:
                            f.write(
                                f"=== {_time.strftime('%Y-%m-%d %H:%M:%S')} [zombie-purge] ===\n"
                            )
                            f.write(
                                f"Session removed after 1h zombie. "
                                f"Last heartbeat: {session.last_heartbeat:.0f}, "
                                f"worker_id={session.worker_id}\n"
                            )
                    except Exception:
                        pass
            await asyncio.sleep(self._heartbeat_interval)
