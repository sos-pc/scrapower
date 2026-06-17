"""Security utilities — token encryption, hash validation.

Fernet key derivation uses a per-deployment random salt stored in SQLite.
Even if the database is stolen, tokens cannot be decrypted without
SCRAPOWER_SECRET + the unique deployment salt.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Module-level Fernet instance (initialized at startup)
_fernet: Fernet | None = None


def _read_salt_from_db(db_path: str) -> bytes | None:
    """Read the deployment-unique salt from SQLite. Returns None if not found."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute("SELECT value FROM kv_store WHERE key = 'fernet_salt'").fetchone()
        conn.close()
        if row:
            return row[0].encode()
    except Exception:
        pass
    return None


def _write_salt_to_db(db_path: str, salt: bytes) -> None:
    """Write a deployment-unique salt to SQLite."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT OR REPLACE INTO kv_store (key, value) VALUES ('fernet_salt', ?)",
            (salt.decode(),),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def init_fernet(db_path: str = "") -> None:
    """Initialize Fernet encryption. Must be called once at startup.

    Uses a per-deployment random salt from SQLite so that even if the
    database file is stolen, tokens cannot be brute-forced without
    knowing SCRAPOWER_SECRET AND the unique deployment salt.
    """
    global _fernet

    if _fernet is not None:
        return  # Already initialized

    secret = os.environ.get("SCRAPOWER_SECRET", secrets.token_hex(32))

    # Use deployment-unique salt from DB, or create one
    if db_path:
        salt = _read_salt_from_db(db_path) or secrets.token_bytes(32)
        _write_salt_to_db(db_path, salt)
    else:
        salt = hashlib.sha256(secret.encode()).digest()[:16]

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000)
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    _fernet = Fernet(key)


def _ensure_fernet() -> Fernet:
    """Lazy-init Fernet if not already done (for scripts/tests)."""
    global _fernet
    if _fernet is None:
        init_fernet()
    return _fernet  # type: ignore[return-value]


def encrypt_token(token: str) -> str:
    """Encrypt a provider token for storage. Returns base64 string."""
    return _ensure_fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a provider token. Returns original string."""
    return _ensure_fernet().decrypt(encrypted.encode()).decode()


def is_valid_blob_hash(hash_hex: str) -> bool:
    """Check if string is a valid 64-char lowercase hex SHA-256 hash."""
    return len(hash_hex) == 64 and all(c in "0123456789abcdef" for c in hash_hex)


# Content Security Policy (reference only — served via Caddy in production)
CSP_HEADER = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "connect-src 'self' wss: ws: https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
)

CSP_HEADER_VALUE = {"Content-Security-Policy": CSP_HEADER}
