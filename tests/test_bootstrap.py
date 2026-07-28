"""Bootstrap tokens (audit §3.7).

Kaggle stores notebook source *and version history*, so substituting the API key
and the WireGuard proxy password into a pushed notebook parks long-lived secrets
in a third party's storage. Kaggle Secrets can't help (the push API has no
secrets field; UserSecretsClient resolves server-side against secrets attached by
hand in the web UI). A worker therefore carries only a short-lived opaque token
and trades it for credentials at startup.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from scrapower.coordinator import bootstrap
from scrapower.coordinator.main import app

# ── Token issuance and redemption ──────────────────────────────────────────


async def test_issued_token_is_opaque_and_unguessable(db):
    t1 = await bootstrap.issue_token(db, "kaggle", "kaggle:acct")
    t2 = await bootstrap.issue_token(db, "kaggle", "kaggle:acct")

    assert t1 != t2, "each launch gets its own token"
    assert len(t1) >= 32, "must not be short enough to brute force"
    assert "kaggle" not in t1, "must not leak which account it belongs to"


async def test_redeem_returns_the_account_it_was_issued_for(db):
    token = await bootstrap.issue_token(db, "kaggle", "kaggle:acct")

    info = await bootstrap.redeem_token(db, token)

    assert info["provider"] == "kaggle"
    assert info["account_id"] == "kaggle:acct"
    assert info["use_count"] == 1


@pytest.mark.parametrize("bad", ["", "not-a-real-token", "x" * 43])
async def test_unknown_tokens_are_refused(db, bad):
    with pytest.raises(bootstrap.BootstrapError):
        await bootstrap.redeem_token(db, bad)


async def test_refusal_reason_distinguishes_unknown_from_expired(db):
    """A stranded worker's log must say *why*, not just 401."""
    with pytest.raises(bootstrap.BootstrapError) as unknown:
        await bootstrap.redeem_token(db, "nope")
    assert unknown.value.reason == "unknown_token"

    token = await bootstrap.issue_token(db, "kaggle", "acct", ttl_sec=-1)
    with pytest.raises(bootstrap.BootstrapError) as expired:
        await bootstrap.redeem_token(db, token)
    assert expired.value.reason == "expired_token"


async def test_expired_token_is_refused_even_if_never_used(db):
    token = await bootstrap.issue_token(db, "kaggle", "acct", ttl_sec=-10)
    with pytest.raises(bootstrap.BootstrapError):
        await bootstrap.redeem_token(db, token)


async def test_redeem_is_idempotent_within_the_ttl(db):
    """A network blip at boot must not cost a whole GPU session, so the same
    token can be redeemed again rather than being burned on first use."""
    token = await bootstrap.issue_token(db, "kaggle", "acct")

    first = await bootstrap.redeem_token(db, token)
    second = await bootstrap.redeem_token(db, token)

    assert first["use_count"] == 1
    assert second["use_count"] == 2, "reuse is allowed and counted"


async def test_reuse_is_bounded(db):
    """Unbounded reuse would mean a leaked token works forever within its TTL."""
    token = await bootstrap.issue_token(db, "kaggle", "acct")
    for _ in range(bootstrap.MAX_USES):
        await bootstrap.redeem_token(db, token)

    with pytest.raises(bootstrap.BootstrapError) as exc:
        await bootstrap.redeem_token(db, token)
    assert exc.value.reason == "token_exhausted"


async def test_default_ttl_covers_provider_queueing(db):
    """Kaggle queues kernels; a token expiring before the kernel is scheduled
    would strand the worker with no way to authenticate."""
    assert bootstrap.DEFAULT_TTL_SEC >= 3600

    token = await bootstrap.issue_token(db, "kaggle", "acct")
    cur = await db.execute("SELECT expires_at FROM bootstrap_tokens WHERE token = ?", (token,))
    expires_at = (await cur.fetchone())["expires_at"]
    assert expires_at - time.time() > 3000


async def test_purge_removes_only_expired_tokens(db):
    live = await bootstrap.issue_token(db, "kaggle", "acct")
    dead = await bootstrap.issue_token(db, "kaggle", "acct", ttl_sec=-1)

    assert await bootstrap.purge_expired(db) == 1

    await bootstrap.redeem_token(db, live)  # still usable
    with pytest.raises(bootstrap.BootstrapError):
        await bootstrap.redeem_token(db, dead)


# ── The exchange endpoint ──────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPOWER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCRAPOWER_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("SCRAPOWER_WG_PROXY_PUBLIC", "socks5://u:p@proxy.example.com:1081")
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as c:
        c.db_path = str(tmp_path / "app.db")  # type: ignore[attr-defined]
        yield c


def _issue_via_app(client, provider="kaggle", account="kaggle:acct", ttl=None):
    """Insert a token straight into the app's database file.

    Deliberately plain sqlite3 rather than awaiting issue_token(): the app's
    aiosqlite connection belongs to TestClient's own event loop, and driving it
    from the test's loop is what broke here first.
    """
    import sqlite3

    token = bootstrap.new_token()
    now = time.time()
    ttl_sec = bootstrap.DEFAULT_TTL_SEC if ttl is None else ttl
    conn = sqlite3.connect(client.db_path)  # type: ignore[attr-defined]
    conn.execute(
        """INSERT INTO bootstrap_tokens
           (token, provider, account_id, expires_at, used_count, created_at)
           VALUES (?, ?, ?, ?, 0, ?)""",
        (token, provider, account, now + ttl_sec, now),
    )
    conn.commit()
    conn.close()
    return token


def test_exchange_returns_credentials(client):
    token = _issue_via_app(client)

    r = client.post("/worker/bootstrap", json={"token": token})

    assert r.status_code == 200
    body = r.json()
    assert body["api_key"], "the worker needs the real key to pull"
    assert body["wg_proxy"] == "socks5://u:p@proxy.example.com:1081"


def test_exchange_needs_no_api_key(client):
    """The whole point: a freshly launched worker doesn't have the key yet."""
    token = _issue_via_app(client)

    r = client.post("/worker/bootstrap", json={"token": token})

    assert r.status_code == 200, "must not require the credential it is handing out"


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        ({"token": "bogus"}, "unknown_token"),
        ({}, "missing_token"),
        ({"token": ""}, "missing_token"),
    ],
)
def test_exchange_refuses_bad_tokens_with_a_reason(client, payload, expected_reason):
    r = client.post("/worker/bootstrap", json=payload)

    assert r.status_code == 401
    assert r.json()["reason"] == expected_reason


def test_exchange_refuses_expired_token(client):
    token = _issue_via_app(client, ttl=-1)

    r = client.post("/worker/bootstrap", json={"token": token})

    assert r.status_code == 401
    assert r.json()["reason"] == "expired_token"


def test_exchange_rejects_malformed_body(client):
    r = client.post(
        "/worker/bootstrap", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400


# ── The notebook template must carry no secret ─────────────────────────────


def test_notebook_template_has_no_secret_placeholders():
    with open("deploy/kaggle/sworker.ipynb", encoding="utf-8") as f:
        raw = f.read()

    for forbidden in ("{{API_KEY}}", "{{WG_PASS}}", "{{WG_USER}}", "{{WG_HOST}}"):
        assert forbidden not in raw, f"{forbidden} must no longer be substituted into Kaggle"
    assert "{{BOOTSTRAP_TOKEN}}" in raw


def test_notebook_template_is_valid_json_and_sets_the_token():
    with open("deploy/kaggle/sworker.ipynb", encoding="utf-8") as f:
        nb = json.load(f)

    code = "\n".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"
    )
    assert "SCRAPOWER_BOOTSTRAP_TOKEN" in code
    assert "SCRAPOWER_API_KEY" not in code, "the key must not be set from the notebook"
