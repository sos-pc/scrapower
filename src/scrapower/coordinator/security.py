"""Security middleware — API key auth + rate limiting + audit logging."""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from collections import defaultdict

import structlog
from fastapi import HTTPException, Request

API_KEY = os.environ.get("SCRAPOWER_API_KEY", "")

if not API_KEY:
    API_KEY = secrets.token_hex(32)
    _log = structlog.get_logger()
    _log.warning("no SCRAPOWER_API_KEY set, generated temporary key")

# Rate limiting: per-IP counters
_rate_limits: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60  # seconds
_RATE_MAX_REQUESTS = 30  # per window

# Audit log for security events
_audit_log = structlog.get_logger("audit")


def _log_audit(event: str, request: Request, **extra) -> None:
    """Log a security event with client info."""
    ip = request.client.host if request.client else "unknown"
    path = request.url.path if hasattr(request, "url") else "?"
    _audit_log.warning(
        event,
        ip=ip,
        path=path,
        user_agent=request.headers.get("user-agent", "?"),
        **extra,
    )


def verify_api_key(request: Request) -> bool:
    """Check if request has valid API key in header or query param."""
    token = request.headers.get("X-API-Key", "") or request.query_params.get("api_key", "")
    if not token or not API_KEY:
        return False
    return (
        hashlib.sha256(token.encode()).hexdigest() == hashlib.sha256(API_KEY.encode()).hexdigest()
    )


def check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed, False if rate limited."""
    now = time.time()
    window_start = now - _RATE_WINDOW
    _rate_limits[ip] = [t for t in _rate_limits[ip] if t > window_start]

    if len(_rate_limits[ip]) >= _RATE_MAX_REQUESTS:
        return False

    _rate_limits[ip].append(now)
    return True


async def require_auth(request: Request):
    """FastAPI dependency — requires valid API key. Raises HTTPException if missing."""
    if not verify_api_key(request):
        _log_audit("AUTH_FAILED", request)
        raise HTTPException(
            status_code=401,
            detail={"error": "UNAUTHORIZED", "hint": "Add X-API-Key header"},
        )


async def rate_limit(request: Request):
    """FastAPI dependency — rate limits by IP. Raises HTTPException if exceeded."""
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(ip):
        _log_audit("RATE_LIMITED", request, exceeded=_RATE_MAX_REQUESTS)
        raise HTTPException(status_code=429, detail={"error": "RATE_LIMITED"})
