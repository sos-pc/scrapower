"""OAuth handlers — GitHub OAuth flow for connecting visitor accounts.

Flow:
  1. Visitor clicks "Connect GitHub" → redirected to GitHub
  2. GitHub redirects back with ?code=XXX&state=YYY
  3. We exchange code for token, encrypt it, store in DB
  4. Visitor sees "GitHub connected ✓" on the worker page
"""

from __future__ import annotations

import secrets
import time

import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .crypto_utils import encrypt_token
from .security import verify_api_key

router = APIRouter(prefix="/auth")

# OAuth config — must be set via env vars
GITHUB_CLIENT_ID = ""
GITHUB_CLIENT_SECRET = ""
COORDINATOR_URL = ""

# CSRF state storage (in-memory, valid for 10 minutes)
_oauth_states: dict[str, float] = {}


def configure_oauth(client_id: str, client_secret: str, coordinator_url: str):
    """Configure OAuth credentials (called at startup)."""
    global GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, COORDINATOR_URL
    GITHUB_CLIENT_ID = client_id
    GITHUB_CLIENT_SECRET = client_secret
    COORDINATOR_URL = coordinator_url.rstrip("/")


@router.get("/github/login")
async def github_login(request: Request):
    """Redirect visitor to GitHub OAuth authorization page."""
    if not GITHUB_CLIENT_ID:
        return JSONResponse({"error": "OAUTH_NOT_CONFIGURED"}, status_code=500)

    # Generate CSRF state token
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = time.time()

    # Clean expired states
    _clean_expired_states()

    redirect_uri = f"{COORDINATOR_URL}/auth/github/callback"
    github_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=workflow"
        f"&state={state}"
    )
    return RedirectResponse(github_url)


@router.get("/github/callback")
async def github_callback(request: Request):
    """Handle GitHub OAuth callback. Exchange code for token."""
    if not GITHUB_CLIENT_SECRET:
        return JSONResponse({"error": "OAUTH_NOT_CONFIGURED"}, status_code=500)

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")

    # Verify CSRF state
    if not state or state not in _oauth_states:
        return JSONResponse({"error": "INVALID_STATE"}, status_code=400)
    del _oauth_states[state]

    # Exchange code for access token
    token = await _exchange_code(code)
    if not token:
        return JSONResponse({"error": "TOKEN_EXCHANGE_FAILED"}, status_code=400)

    # Verify token has workflow scope
    if not await _verify_scope(token):
        return JSONResponse(
            {"error": "INSUFFICIENT_SCOPE", "hint": "Token must have workflow scope"},
            status_code=400,
        )

    # Encrypt and store
    encrypted = encrypt_token(token)
    visitor_id = request.query_params.get("state", f"gh-{secrets.token_hex(4)}")[:16]

    db = request.app.state.db
    if db:
        await db.execute(
            """INSERT OR REPLACE INTO provider_tokens (visitor_id, provider, token_encrypted, created_at)
               VALUES (?, 'github', ?, ?)""",
            (visitor_id, encrypted, str(time.time())),
        )
        await db.commit()

    return RedirectResponse(f"/?github=connected")


@router.delete("/github/revoke")
async def github_revoke(request: Request):
    """Revoke a visitor's GitHub token."""
    if not verify_api_key(request):
        return JSONResponse({"error": "UNAUTHORIZED"}, status_code=401)

    body = await request.json()
    visitor_id = body.get("visitor_id", "")

    db = request.app.state.db
    if db:
        await db.execute(
            "DELETE FROM provider_tokens WHERE visitor_id = ? AND provider = 'github'",
            (visitor_id,),
        )
        await db.commit()

    return JSONResponse({"status": "revoked"})


async def _exchange_code(code: str) -> str | None:
    """Exchange OAuth code for access token."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
            return data.get("access_token")


async def _verify_scope(token: str) -> bool:
    """Verify the token has 'workflow' scope."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        ) as r:
            if r.status != 200:
                return False
            # Check scopes via X-OAuth-Scopes header
            scopes = r.headers.get("X-OAuth-Scopes", "")
            return "workflow" in scopes


def _clean_expired_states():
    """Remove OAuth states older than 10 minutes."""
    now = time.time()
    expired = [s for s, t in _oauth_states.items() if now - t > 600]
    for s in expired:
        del _oauth_states[s]
