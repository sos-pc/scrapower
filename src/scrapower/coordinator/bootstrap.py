"""Bootstrap tokens — hand a launched worker a short-lived credential.

Why: launching a Kaggle worker means pushing a notebook, and Kaggle stores that
notebook's source *and its version history*. Substituting the real API key (and
the WireGuard proxy password) into it therefore parks long-lived secrets in a
third party's storage. Kaggle Secrets cannot help: the push API has no secrets
field, and `UserSecretsClient` resolves server-side against secrets a human
attached in the web UI — impossible for kernels created programmatically under a
fresh name each launch (kaggle-cli issue #582, open with no response).

So the notebook only ever carries an opaque, short-lived token, which the worker
trades once at startup for the credentials it needs. A leaked notebook then
yields nothing usable after the TTL.

Deliberate design points:
  - TTL is generous (hours, not minutes): Kaggle *queues* kernels, and a token
    that expires before the kernel is scheduled would strand the worker with no
    way to authenticate.
  - Exchange is idempotent within the TTL rather than strictly single-use, so a
    network blip during startup doesn't cost a whole GPU session. Reuse is
    counted and logged instead of blocked.
  - The token grants exactly one thing: the worker credentials. It is not an
    API key and cannot be used against any other endpoint.
"""

from __future__ import annotations

import logging
import secrets
import time

log = logging.getLogger(__name__)

# Must comfortably exceed the provider's scheduling delay (Kaggle kernels are
# queued; observed startups are minutes, but that is not guaranteed).
DEFAULT_TTL_SEC = 2 * 3600

# Reuse is allowed (see module docstring) but not unbounded: past this, the
# token is almost certainly leaked rather than retried.
MAX_USES = 20

_audit = logging.getLogger("audit")


def new_token() -> str:
    """An opaque token with no structure to guess or forge."""
    return secrets.token_urlsafe(32)


async def issue_token(
    db, provider: str, account_id: str, ttl_sec: int = DEFAULT_TTL_SEC
) -> str:
    """Mint a bootstrap token for a worker about to be launched."""
    token = new_token()
    now = time.time()
    await db.execute(
        """INSERT INTO bootstrap_tokens
           (token, provider, account_id, expires_at, used_count, created_at)
           VALUES (?, ?, ?, ?, 0, ?)""",
        (token, provider, account_id, now + ttl_sec, now),
    )
    await db.commit()
    log.info(
        "bootstrap: issued token for %s (provider=%s, ttl=%ds)", account_id, provider, ttl_sec
    )
    return token


class BootstrapError(Exception):
    """Exchange refused. ``reason`` is safe to return to the caller."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


async def redeem_token(db, token: str) -> dict:
    """Validate a token and return the metadata of the worker it was issued for.

    Raises BootstrapError with a distinct reason per failure so a stranded
    worker's logs say *why* (unknown vs expired vs exhausted), instead of the
    single opaque 401 that made the old "silent failure" class of bug so hard
    to diagnose.
    """
    if not token:
        raise BootstrapError("missing_token")

    cursor = await db.execute(
        "SELECT provider, account_id, expires_at, used_count FROM bootstrap_tokens WHERE token = ?",
        (token,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise BootstrapError("unknown_token")

    now = time.time()
    if now > row["expires_at"]:
        raise BootstrapError("expired_token")
    if row["used_count"] >= MAX_USES:
        raise BootstrapError("token_exhausted")

    await db.execute(
        "UPDATE bootstrap_tokens SET used_count = used_count + 1, last_used_at = ? WHERE token = ?",
        (now, token),
    )
    await db.commit()

    if row["used_count"] > 0:
        # Not an error (retries are expected), but worth seeing in the log.
        log.info(
            "bootstrap: token for %s redeemed again (use #%d)",
            row["account_id"],
            row["used_count"] + 1,
        )
    return {
        "provider": row["provider"],
        "account_id": row["account_id"],
        "use_count": row["used_count"] + 1,
    }


async def purge_expired(db) -> int:
    """Drop tokens past their TTL. Returns how many were removed."""
    cursor = await db.execute(
        "DELETE FROM bootstrap_tokens WHERE expires_at < ?", (time.time(),)
    )
    await db.commit()
    if cursor.rowcount:
        log.info("bootstrap: purged %d expired token(s)", cursor.rowcount)
    return cursor.rowcount
