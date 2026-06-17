"""Security middleware for FastAPI — adds CSP and other security headers.

Uses pure ASGI middleware (not Starlette BaseHTTPMiddleware) to inject
headers below h11's HTTP/1.1 serialization layer. This avoids the
h11 < 0.14 validation bug that rejects valid CSP values containing
``:``, ``;``, and ``'`` characters on Python 3.10.
"""

from __future__ import annotations

from typing import Callable

# ── Content Security Policy ──────────────────────────────────────────
CSP_VALUE = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "connect-src 'self' wss: ws: https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
)

SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"content-security-policy", CSP_VALUE.encode("ascii")),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


class SecurityHeadersMiddleware:
    """Pure ASGI middleware that injects security headers into every response.

    Why ASGI (not Starlette BaseHTTPMiddleware):
        - Runs below h11's HTTP/1.1 serialization, so header values that
          contain ``:``, ``;``, ``'`` (common in CSP) never pass through
          h11's ``field_value`` regex validation during response construction.
        - Works uniformly on FileResponse, StreamingResponse, Response, etc.
        - Python-version and dependency-version independent.
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message: dict) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower(): v for k, v in message.get("headers", [])}
                merged = list(message.get("headers", []))
                for key, value in SECURITY_HEADERS:
                    if key not in existing:
                        merged.append((key, value))
                message["headers"] = merged
            await send(message)

        await self.app(scope, receive, _send)
