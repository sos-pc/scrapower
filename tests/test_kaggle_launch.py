"""Kaggle kernel launch: proxy resolution must fail loudly, not silently.

The old code fell back to empty user/password/host when the proxy URL couldn't
be parsed, so the notebook was built with "socks5://:@:1081" and the worker
launched only to fail every YouTube download, with nothing in the logs naming
the real cause.
"""

from __future__ import annotations

import json

import pytest

from scrapower.coordinator.accounts import Account
from scrapower.coordinator.harvester.kaggle import KaggleHarvester, split_proxy_url

GOOD = "socks5://scrapower:s3cret@proxy.example.com:1081"


def test_splits_a_well_formed_proxy_url():
    assert split_proxy_url(GOOD) == ("scrapower", "s3cret", "proxy.example.com")


def test_host_keeps_dots_and_drops_only_the_port():
    user, passwd, host = split_proxy_url("socks5://u:p@a.b.c.example.com:1081")
    assert host == "a.b.c.example.com"


def test_password_containing_a_colon_is_preserved():
    """Only the first colon separates user from password."""
    assert split_proxy_url("socks5://u:pa:ss@h:1081") == ("u", "pa:ss", "h")


@pytest.mark.parametrize(
    "bad",
    [
        "",                                  # empty
        "proxy.example.com:1081",            # no scheme
        "socks5://proxy.example.com:1081",   # no credentials
        "socks5://user@proxy.example.com:1081",  # no password
        "socks5://:pass@proxy.example.com:1081",  # no user
        "socks5://user:pass@:1081",          # no host
        "socks5://user:@host:1081",          # empty password
    ],
)
def test_malformed_proxy_urls_raise(bad):
    with pytest.raises(ValueError):
        split_proxy_url(bad)


def test_error_message_names_what_is_missing():
    with pytest.raises(ValueError, match="password"):
        split_proxy_url("socks5://user:@host:1081")


# ── Launch behaviour ───────────────────────────────────────────────────────


@pytest.fixture
def harvester(tmp_path):
    """A harvester pointed at a minimal notebook template with placeholders."""
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "source": [
                    'URL = "{{COORDINATOR_URL}}"\n',
                    'KEY = "{{API_KEY}}"\n',
                    '_user = "{{WG_USER}}"\n',
                    '_pass = "{{WG_PASS}}"\n',
                    '_host = "{{WG_HOST}}"\n',
                ],
            },
            {"cell_type": "markdown", "source": "not code"},
        ]
    }
    path = tmp_path / "sworker.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    return KaggleHarvester(
        account_ids=["kaggle:acct"],
        coordinator_url="https://coord.example.com",
        api_key="test-key",
        notebook_template=str(path),
    )


def _account() -> Account:
    return Account(
        id="kaggle:acct",
        provider="kaggle",
        lifecycle="ephemeral",
        gpu_type="T4",
        gpu_vram_mb=16384,
        enabled=True,
        max_concurrent=3,
        credentials={"username": "acct", "token": "tok"},
    )


@pytest.fixture
def fake_kaggle_cli(monkeypatch):
    """Intercept the `kaggle kernels push` subprocess and capture what was sent."""
    import asyncio as _asyncio

    calls: list[dict] = []

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        # The pushed notebook lives in the directory passed after "-p".
        argv = list(args)
        nb_dir = argv[argv.index("-p") + 1] if "-p" in argv else None
        payload = None
        if nb_dir:
            with open(f"{nb_dir}/notebook.ipynb", encoding="utf-8") as f:
                payload = json.load(f)
        calls.append({"argv": argv, "notebook": payload})
        return _Proc()

    monkeypatch.setattr(_asyncio, "create_subprocess_exec", fake_exec)
    return calls


async def test_launch_refused_when_proxy_is_malformed(
    harvester, monkeypatch, caplog, fake_kaggle_cli
):
    """A garbage proxy must abort the launch, not burn a GPU on a doomed worker."""
    monkeypatch.setenv("SCRAPOWER_WG_PROXY_PUBLIC", "socks5://not-a-valid-proxy")

    ok = await harvester._start_kernel(_account())

    assert ok is False, "must refuse to launch"
    assert fake_kaggle_cli == [], "nothing may be pushed to Kaggle"
    assert any("refusing to launch" in r.message for r in caplog.records)


async def test_missing_proxy_warns_but_still_launches(
    harvester, monkeypatch, caplog, fake_kaggle_cli
):
    """No proxy is a deliberate config: keep the old behaviour but say so."""
    monkeypatch.delenv("SCRAPOWER_WG_PROXY_PUBLIC", raising=False)
    monkeypatch.delenv("SCRAPOWER_WG_PROXY", raising=False)

    ok = await harvester._start_kernel(_account())

    assert ok is True
    assert any("no WG proxy configured" in r.message for r in caplog.records)


async def test_valid_proxy_is_substituted_into_the_notebook(
    harvester, monkeypatch, fake_kaggle_cli
):
    monkeypatch.setenv("SCRAPOWER_WG_PROXY_PUBLIC", GOOD)

    assert await harvester._start_kernel(_account()) is True

    src = "".join(fake_kaggle_cli[0]["notebook"]["cells"][0]["source"])
    assert '_user = "scrapower"' in src
    assert '_pass = "s3cret"' in src
    assert '_host = "proxy.example.com"' in src
    assert "{{" not in src, "no placeholder may survive substitution"


async def test_public_proxy_wins_over_the_local_one(harvester, monkeypatch, fake_kaggle_cli):
    """_PUBLIC is what remote workers must get; the local one is only a fallback."""
    monkeypatch.setenv("SCRAPOWER_WG_PROXY_PUBLIC", GOOD)
    monkeypatch.setenv("SCRAPOWER_WG_PROXY", "socks5://local:pw@127.0.0.1:1081")

    await harvester._start_kernel(_account())

    src = "".join(fake_kaggle_cli[0]["notebook"]["cells"][0]["source"])
    assert "proxy.example.com" in src
    assert "127.0.0.1" not in src, "a loopback host would be unreachable from Kaggle"
