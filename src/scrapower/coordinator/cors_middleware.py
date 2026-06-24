"""CORS middleware for Scrapower — allows cross-origin embed on any website.

When a site includes <script src=\"https://your-coordinator.example.com/embed.js\"></script>,
the browser worker needs to call our API (blobs, WebSocket) from a different origin.
This middleware adds the necessary CORS headers.
"""

from __future__ import annotations

from typing import Callable

# Allowed origins for CORS (wildcard for public embed, restrict in production)
ALLOWED_ORIGINS = ["*"]

# Headers exposed to cross-origin clients
EXPOSED_HEADERS = [
    b"content-type",
    b"content-length",
    b"x-request-id",
]


class CORSMiddleware:
    """Pure ASGI middleware that adds CORS headers to every response.

    Allows any website to embed the Scrapower worker widget and call our API.
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Handle preflight (OPTIONS) requests
        if scope["method"] == "OPTIONS":
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [
                        (b"access-control-allow-origin", b"*"),
                        (b"access-control-allow-methods", b"GET, PUT, POST, DELETE, OPTIONS"),
                        (
                            b"access-control-allow-headers",
                            b"content-type, x-api-key, x-client-id, authorization",
                        ),
                        (b"access-control-max-age", b"86400"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        async def _send(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower(): v for k, v in headers}
                if b"access-control-allow-origin" not in existing:
                    headers.append((b"access-control-allow-origin", b"*"))
                if b"access-control-expose-headers" not in existing:
                    headers.append((b"access-control-expose-headers", b", ".join(EXPOSED_HEADERS)))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, _send)
