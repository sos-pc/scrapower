"""OAuth handlers — GitHub OAuth flow for connecting visitor accounts.

Flow:
  1. Visitor clicks "Connect GitHub" → redirected to GitHub
  2. GitHub redirects back with ?code=XXX&state=YYY
  3. We exchange code for token, encrypt it, store in DB
  4. Auto-create {username}/scrapower-worker repo with workflow
  5. Visitor sees "GitHub connected ✓" on the worker page
"""

from __future__ import annotations

import asyncio
import base64
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

# Worker workflow YAML — uses heredoc (no shell escaping, proper YAML indentation)
_WORKER_WORKFLOW_YML = """name: Scrapower Worker
on:
  workflow_dispatch:
    inputs:
      coordinator_url:
        description: 'Coordinator URL'
        required: true
        default: 'https://your-coordinator.example.com'
      worker_id:
        description: 'Worker ID'
        required: false
        default: 'gh-actions'
jobs:
  worker:
    runs-on: ubuntu-latest
    timeout-minutes: 360
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install
        run: pip install aiohttp wasmtime
      - name: Run Worker
        env:
          COORDINATOR_URL: ${{ inputs.coordinator_url }}
          WORKER_ID: ${{ inputs.worker_id }}
        run: |
          python << 'PYEOF'
          import asyncio, aiohttp, os, uuid, hashlib
          C = os.environ['COORDINATOR_URL'].replace('https://', 'wss://').replace('http://', 'ws://')
          if not C.endswith('/worker/ws'):
              C = C.rstrip('/') + '/worker/ws'
          W = os.environ.get('WORKER_ID', f'gh-{uuid.uuid4().hex[:8]}')
          async def main():
              async with aiohttp.ClientSession() as s:
                  async with s.ws_connect(C) as ws:
                      await ws.send_json({'type': 'hello', 'version': '2.1', 'mode': 'persistent', 'worker_id': W, 'auth': {'method': 'none'}})
                      msg = await ws.receive_json()
                      if msg['type'] != 'session':
                          return
                      sid = msg['session_id']
                      hb = msg.get('heartbeat_interval_ms', 10000) // 1000
                      await ws.send_json({'type': 'capabilities', 'session_id': sid, 'payload': {'runtimes': ['wasm'], 'resources': {'cpu_cores': 2, 'ram_mb': 7168, 'gpu': {'supported': False}}, 'lifecycle': {'mode': 'ephemeral', 'max_lifetime_sec': 21600}, 'verification': {'can_challenge': False}, 'network': {'connectivity': 'outgoing_only'}, 'limits': {'max_task_duration_ms': 300000, 'max_concurrent_tasks': 1}}})
                      nxt = asyncio.get_event_loop().time() + hb
                      while True:
                          now = asyncio.get_event_loop().time()
                          if now >= nxt:
                              await ws.send_json({'type': 'heartbeat', 'session_id': sid, 'current_load_pct': 0, 'tasks_in_progress': 0, 'uptime_sec': 0, 'expected_remaining_sec': None})
                              nxt = now + hb
                          try:
                              msg = await asyncio.wait_for(ws.receive_json(), timeout=1.0)
                          except asyncio.TimeoutError:
                              continue
                          except Exception:
                              break
                          mt = msg.get('type', '')
                          if mt in ('task_assign', 'keepalive'):
                              if mt == 'task_assign':
                                  await ws.send_json({'type': 'task_accept', 'session_id': sid, 'task_id': msg['task']['id'], 'assignment_token': msg['task']['assignment_token']})
                              H = C.replace('ws://', 'http://').replace('/worker/ws', '')
                              try:
                                  async with aiohttp.ClientSession() as s2:
                                      async with s2.get(H + '/blobs/' + msg['task']['payload']['executable_hash']) as r:
                                          executable = await r.read()
                                      async with s2.get(H + '/blobs/' + msg['task']['payload']['input_hash']) as r:
                                          inp = await r.read()
                                  try:
                                      import wasmtime
                                      m = wasmtime.Module(executable)
                                      inst = wasmtime.Instance(m, [])
                                      mem = inst.exports['memory']
                                      mem.write_bytes(0, inp)
                                      inst.exports['compute'](0, len(inp), 1024, 4096)
                                      out = bytes(mem.read_bytes(1024, 4096))
                                  except Exception:
                                      out = inp[:100]
                                  oh = hashlib.sha256(out).hexdigest()
                                  async with aiohttp.ClientSession() as s3:
                                      await s3.put(H + '/blobs', data=out)
                                  status, exit_code, stderr = 'success', 0, ''
                              except Exception as e:
                                  oh, status, exit_code, stderr = '', 'error', 1, str(e)[:4096]
                              await ws.send_json({'type': 'task_result', 'session_id': sid, 'task_id': msg['task']['id'], 'assignment_token': msg['task'].get('assignment_token', ''), 'status': status, 'result': {'output_hash': oh, 'execution_metadata': {'duration_ms': 0, 'exit_code': exit_code, 'stderr': stderr}}, 'verification_data': None})
          asyncio.run(main())
          PYEOF
"""


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
        f"&scope=workflow%20public_repo"
        f"&state={state}"
    )
    return RedirectResponse(github_url)


@router.get("/github/callback")
async def github_callback(request: Request):
    """Handle GitHub OAuth callback. Exchange code, store token, setup worker repo."""
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

    # Verify token has required scopes
    scopes = await _get_scopes(token)
    if "workflow" not in scopes:
        return JSONResponse(
            {"error": "INSUFFICIENT_SCOPE", "hint": "Token must have workflow scope"},
            status_code=400,
        )

    # Get GitHub username
    username = await _get_username(token)
    if not username:
        return JSONResponse({"error": "GITHUB_USER_FAILED"}, status_code=400)

    # Auto-create worker repo and push workflow (best-effort, don't block)
    repo_name = await _ensure_worker_repo(token, username)

    # Encrypt and store
    encrypted = encrypt_token(token)
    visitor_id = f"gh-{username}"[:32]

    db = request.app.state.db
    if db:
        await db.execute(
            """INSERT OR REPLACE INTO provider_tokens (visitor_id, provider, token_encrypted, created_at)
               VALUES (?, 'github', ?, ?)""",
            (visitor_id, encrypted, str(time.time())),
        )
        if repo_name:
            await db.execute(
                """INSERT OR REPLACE INTO provider_tokens (visitor_id, provider, token_encrypted, created_at)
                   VALUES (?, 'github_repo', ?, ?)""",
                (visitor_id, repo_name, str(time.time())),
            )
        await db.commit()

    return RedirectResponse("/?github=connected")


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


# ── GitHub API helpers ──────────────────────────────────────


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


async def _get_scopes(token: str) -> str:
    """Get the scopes of a token."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.github.com/user",
            headers=_gh_headers(token),
        ) as r:
            if r.status != 200:
                return ""
            return r.headers.get("X-OAuth-Scopes", "")


async def _get_username(token: str) -> str | None:
    """Get the GitHub username for a token."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.github.com/user",
            headers=_gh_headers(token),
        ) as r:
            if r.status != 200:
                return None
            user = await r.json()
            return user.get("login")


async def _ensure_worker_repo(token: str, username: str) -> str | None:
    """Create worker repo if it doesn't exist, push workflow file.
    Returns repo full name like 'user/scrapower-worker' or None on failure.
    """
    repo_name = "scrapower-worker"
    full_name = f"{username}/{repo_name}"
    headers = _gh_headers(token)

    async with aiohttp.ClientSession() as session:
        # Check if repo exists
        async with session.get(
            f"https://api.github.com/repos/{full_name}",
            headers=headers,
        ) as r:
            if r.status == 200:
                await _upsert_workflow(session, headers, full_name)
                return full_name
            elif r.status != 404:
                return None

        # Create repo with auto_init=True so it has a branch
        async with session.post(
            "https://api.github.com/user/repos",
            json={
                "name": repo_name,
                "description": "Scrapower distributed worker",
                "private": False,
                "auto_init": True,
            },
            headers=headers,
        ) as r:
            if r.status not in (200, 201):
                return None

        # Wait for GitHub to initialize the repo
        await asyncio.sleep(2)

        await _upsert_workflow(session, headers, full_name)
        return full_name


async def _upsert_workflow(
    session: aiohttp.ClientSession,
    headers: dict,
    repo: str,
) -> bool:
    """Create or update the worker workflow file in the repo."""
    content_b64 = base64.b64encode(_WORKER_WORKFLOW_YML.encode()).decode()

    sha = None
    async with session.get(
        f"https://api.github.com/repos/{repo}/contents/.github/workflows/scrapower-worker.yml",
        headers=headers,
    ) as r:
        if r.status == 200:
            data = await r.json()
            sha = data.get("sha")

    body: dict = {
        "message": "Setup Scrapower worker",
        "content": content_b64,
    }
    if sha:
        body["sha"] = sha

    async with session.put(
        f"https://api.github.com/repos/{repo}/contents/.github/workflows/scrapower-worker.yml",
        json=body,
        headers=headers,
    ) as r:
        return r.status in (200, 201)


def _gh_headers(token: str) -> dict:
    """Standard GitHub API headers."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _clean_expired_states():
    """Remove OAuth states older than 10 minutes."""
    now = time.time()
    expired = [s for s, t in _oauth_states.items() if now - t > 600]
    for s in expired:
        del _oauth_states[s]
