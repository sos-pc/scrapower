"""Security utilities — token encryption, CSP headers, hash validation."""

from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Derive a Fernet key from SCRAPOWER_SECRET env var (or generate one)
_secret = os.environ.get("SCRAPOWER_SECRET", "")
if not _secret:
    import secrets

    _secret = secrets.token_hex(32)

_kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"scrapower-v1", iterations=480000)
_key = base64.urlsafe_b64encode(_kdf.derive(_secret.encode()))
_fernet = Fernet(_key)


def encrypt_token(token: str) -> str:
    """Encrypt a provider token for storage. Returns base64 string."""
    return _fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a provider token. Returns original string."""
    return _fernet.decrypt(encrypted.encode()).decode()


def is_valid_blob_hash(hash_hex: str) -> bool:
    """Check if string is a valid 64-char lowercase hex SHA-256 hash."""
    return len(hash_hex) == 64 and all(c in "0123456789abcdef" for c in hash_hex)


# Content Security Policy for the worker page
CSP_HEADER = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "connect-src 'self' wss: ws: https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
)

CSP_HEADER_VALUE = {"Content-Security-Policy": CSP_HEADER}
