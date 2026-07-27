"""The worker must check HTTP status before trusting a response body (audit §4).

Exercised against a real aiohttp server so the client code runs unmodified.
"""

from __future__ import annotations

import asyncio

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from scrapower.worker.loop import WorkerLoop  # noqa: E402

# Safety net: run() must terminate on its own, but a regression must fail the
# test rather than hang CI.
RUN_TIMEOUT = 20


async def _run_until_logged(loop: WorkerLoop, needles: tuple[str, ...], timeout: float = 10.0):
    """Start run() and wait until one of `needles` shows up in its log buffer.

    Used for branches whose back-off is deliberately slow (429): we assert the
    behaviour is reported without waiting out the full retry budget.
    """
    task = asyncio.ensure_future(loop.run())
    deadline = asyncio.get_event_loop().time() + timeout
    try:
        while asyncio.get_event_loop().time() < deadline:
            if any(n in line for line in loop._log_lines for n in needles):
                return True
            if task.done():
                return any(n in line for line in loop._log_lines for n in needles)
            await asyncio.sleep(0.05)
        return False
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

CAPS = {"task_types": ["python"], "runtimes": ["python"], "resources": {"ram_mb": 1024}}


@pytest.fixture
async def server():
    """A stub coordinator whose responses each test can program."""
    routes = web.RouteTableDef()
    state: dict = {"blob_status": 200, "blob_body": b"", "pull_status": 200, "pull_json": {}}

    @routes.get("/blobs/{h}")
    async def blobs(request):
        return web.Response(body=state["blob_body"], status=state["blob_status"])

    @routes.post("/worker/pull")
    async def pull(request):
        if state["pull_status"] != 200:
            return web.json_response({"error": "nope"}, status=state["pull_status"])
        return web.json_response(state["pull_json"])

    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    state["url"] = f"http://127.0.0.1:{port}"
    yield state
    await runner.cleanup()


def _loop(url: str) -> WorkerLoop:
    return WorkerLoop(
        worker_id="w-test",
        coordinator_url=url,
        api_key="k",
        capabilities=CAPS,
        poll_interval_sec=0,
        idle_timeout_sec=0,
    )


# ── (a) blob download ──────────────────────────────────────────────────────


async def test_missing_blob_raises_instead_of_becoming_the_executable(server):
    """A 404 body must never be handed to the runtime as a Python script."""
    server["blob_status"] = 404
    server["blob_body"] = b'{"error": "NOT_FOUND"}'
    loop = _loop(server["url"])

    async with aiohttp.ClientSession() as session:
        with pytest.raises(RuntimeError) as exc:
            await loop._fetch_blob(session, "a" * 64, "executable")

    assert "404" in str(exc.value)
    assert "executable" in str(exc.value), "the message must name which blob failed"


@pytest.mark.parametrize("status", [400, 401, 403, 500, 503])
async def test_any_non_200_blob_response_is_an_error(server, status):
    server["blob_status"] = status
    server["blob_body"] = b"some error body"
    loop = _loop(server["url"])

    async with aiohttp.ClientSession() as session:
        with pytest.raises(RuntimeError):
            await loop._fetch_blob(session, "b" * 64, "input")


async def test_successful_blob_download_returns_bytes(server):
    server["blob_status"] = 200
    server["blob_body"] = b"print('hello')"
    loop = _loop(server["url"])

    async with aiohttp.ClientSession() as session:
        assert await loop._fetch_blob(session, "c" * 64, "executable") == b"print('hello')"


# ── (c) pull loop ──────────────────────────────────────────────────────────


async def test_unauthorized_pull_is_not_mistaken_for_an_empty_queue(server):
    """401 used to fall through to task=None, indistinguishable from no work."""
    server["pull_status"] = 401
    loop = _loop(server["url"])

    await asyncio.wait_for(loop.run(), timeout=RUN_TIMEOUT)  # idle_timeout 0 => exits promptly

    logged = "\n".join(loop._log_lines)
    assert "UNAUTHORIZED" in logged or "401" in logged, (
        f"a rejected key must be reported, got: {logged}"
    )


async def test_rate_limited_pull_is_reported(server):
    """429 backs off deliberately slowly, so assert on the report, not the exit."""
    server["pull_status"] = 429
    loop = _loop(server["url"])

    assert await _run_until_logged(loop, ("RATE LIMITED", "429")), (
        f"a 429 must be reported distinctly, got: {loop._log_lines}"
    )


async def test_worker_gives_up_instead_of_spinning_on_permanent_failure(server):
    """A revoked key used to trap the worker in an endless pull loop.

    The idle deadline was only evaluated on the "queue empty" branch, so a
    permanently failing pull spun forever — burning GPU quota until the platform
    killed the worker at max lifetime.
    """
    server["pull_status"] = 401
    loop = _loop(server["url"])  # idle_timeout_sec=0

    await asyncio.wait_for(loop.run(), timeout=RUN_TIMEOUT)  # must return by itself

    logged = "\n".join(loop._log_lines)
    assert "stopping" in logged.lower(), f"worker must stop, not spin: {logged}"


async def test_empty_queue_exits_on_idle_without_error_noise(server):
    """The genuine "no work" case must stay quiet and stop on idle timeout."""
    server["pull_status"] = 200
    server["pull_json"] = {"type": "pull_response", "task": None}
    loop = _loop(server["url"])

    await asyncio.wait_for(loop.run(), timeout=RUN_TIMEOUT)

    logged = "\n".join(loop._log_lines)
    assert "Idle" in logged
    for noise in ("UNAUTHORIZED", "RATE LIMITED", "unexpected HTTP"):
        assert noise not in logged, f"quiet path must not log {noise!r}"
