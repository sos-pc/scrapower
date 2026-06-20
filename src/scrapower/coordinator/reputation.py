"""Worker reputation scoring.

Tracks per-worker trust (0.0–1.0) based on challenge results:
  - matched    → score approaches 1.0  (asymptotic gain)
  - mismatched → score halved          (rapid decay)
  - N mismatches in window → blacklist

Also provides the adaptive challenge rate for the scheduler:
  - new / untrusted worker → high challenge rate (close to 100%)
  - trusted worker         → low challenge rate (minimum 1%)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_REPUTATION = 0.50  # neutral starting score
MAX_MISMATCHES = 3  # blacklist after this many mismatches in window
MISMATCH_WINDOW_SEC = 3600  # 1-hour sliding window for mismatch counting
MIN_CHALLENGE_RATE = 0.01  # never fully trust — always 1% sampling


@dataclass
class ReputationScore:
    worker_id: str
    score: float  # 0.0 (blacklisted) … 1.0 (fully trusted)
    mismatch_count: int  # in current window
    blacklisted: bool
    first_seen: str
    last_seen: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class ReputationService:
    """Read/write worker reputation from the SQLite workers table."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    # -- CRUD ---------------------------------------------------------------

    async def _upsert_worker(self, worker_id: str) -> None:
        """Ensure a row exists for this worker (lazy creation)."""
        now = str(time.time())
        await self._db.execute(
            """INSERT OR IGNORE INTO workers (id, first_seen, last_seen)
               VALUES (?, ?, ?)""",
            (worker_id, now, now),
        )
        # Always touch last_seen
        await self._db.execute(
            "UPDATE workers SET last_seen = ? WHERE id = ?",
            (now, worker_id),
        )
        await self._db.commit()

    async def get(self, worker_id: str) -> ReputationScore:
        """Return the current reputation for a worker.

        If the worker has never been seen, returns a neutral default
        (but does *not* persist it — persistence happens on first
        challenge resolution or explicit registration).
        """
        cursor = await self._db.execute(
            "SELECT reputation_score, first_seen, last_seen FROM workers WHERE id = ?",
            (worker_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return ReputationScore(
                worker_id=worker_id,
                score=DEFAULT_REPUTATION,
                mismatch_count=0,
                blacklisted=False,
                first_seen="",
                last_seen="",
            )

        # Count recent mismatches
        cutoff = str(time.time() - MISMATCH_WINDOW_SEC)
        cursor2 = await self._db.execute(
            """SELECT COUNT(*) as cnt FROM challenges
               WHERE status = 'mismatched'
                 AND created_at > ?
                 AND (token_a IN (SELECT current_assignment_token FROM tasks WHERE assigned_worker_id = ?)
                      OR token_b IN (SELECT current_assignment_token FROM tasks WHERE assigned_worker_id = ?))""",
            (cutoff, worker_id, worker_id),
        )
        mismatch_row = await cursor2.fetchone()
        mismatch_count = mismatch_row["cnt"] if mismatch_row else 0
        blacklisted = mismatch_count >= MAX_MISMATCHES

        return ReputationScore(
            worker_id=worker_id,
            score=row["reputation_score"],
            mismatch_count=mismatch_count,
            blacklisted=blacklisted,
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )

    async def record_matched(self, worker_id: str) -> None:
        """Called when a challenge completes with matched hashes.

        Increases score:  score += 0.10 * (1.0 - score)
        """
        await self._upsert_worker(worker_id)
        await self._db.execute(
            "UPDATE workers SET reputation_score = reputation_score + 0.10 * (1.0 - reputation_score) WHERE id = ?",
            (worker_id,),
        )
        await self._db.commit()

    async def record_mismatched(self, worker_id: str) -> None:
        """Called when a challenge completes with mismatched hashes.

        Halves the score (rapid decay).
        """
        await self._upsert_worker(worker_id)
        await self._db.execute(
            "UPDATE workers SET reputation_score = reputation_score * 0.5 WHERE id = ?",
            (worker_id,),
        )
        await self._db.commit()

    # -- Helpers for scheduler -----------------------------------------------

    async def challenge_rate(self, worker_id: str) -> float:
        """Return the probability [0..1] that this worker's task
        should be double-executed for verification.

        Formula:  max(MIN_CHALLENGE_RATE, 1.0 - reputation)
          score 0.0 → 1.00  (always challenge)
          score 0.5 → 0.50
          score 0.9 → 0.10
          score 1.0 → 0.01  (never fully trust)
        """
        rep = await self.get(worker_id)
        if rep.blacklisted:
            return 1.0  # always challenge blacklisted workers
        return max(MIN_CHALLENGE_RATE, 1.0 - rep.score)

    async def is_blacklisted(self, worker_id: str) -> bool:
        rep = await self.get(worker_id)
        return rep.blacklisted
