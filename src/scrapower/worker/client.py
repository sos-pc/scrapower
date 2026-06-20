"""Native Python worker — connects to coordinator, executes tasks.

Implements Worker Protocol v2.1 Mode A (persistent WebSocket).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

import aiohttp

from .sandbox import Sandbox


class WorkerClient:
    """Worker that connects to a Scrapower coordinator via WebSocket.

    Handles handshake, heartbeat, task reception, execution, and result submission.
    """

    def __init__(
        self,
        coordinator_url: str,
        worker_id: str | None = None,
        auth_token: str | None = None,
        runtimes: list[str] | None = None,
        sandbox: Sandbox | None = None,
    ):
        self._url = coordinator_url
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._auth_token = auth_token
        self._runtimes = runtimes or ["wasm"]
        self._sandbox: Sandbox = sandbox or _default_sandbox()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session_id: str | None = None
        self._running = False
        self._heartbeat_interval = 10

    @property
    def session_id(self) -> str | None:
        return self._session_id

    # ── Connection lifecycle ──────────────────────────────

    async def connect(self):
        """Open WebSocket, complete handshake, send capabilities."""
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._url)

        await self._send_hello()
        await self._receive_session()
        await self._send_capabilities()

    async def disconnect(self):
        """Send bye and close the WebSocket cleanly."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.send_json(
                {
                    "type": "bye",
                    "session_id": self._session_id,
                    "reason": "user_disconnect",
                }
            )
            await self._ws.close()
        if self._session:
            await self._session.close()

    async def run(self):
        """Main loop: heartbeat + receive messages."""
        self._running = True
        await self.connect()
        next_hb = asyncio.get_event_loop().time() + self._heartbeat_interval

        try:
            while self._running and self._ws and not self._ws.closed:
                now = asyncio.get_event_loop().time()
                if now >= next_hb:
                    await self._send_heartbeat()
                    next_hb = now + self._heartbeat_interval
                try:
                    msg = await asyncio.wait_for(self._ws.receive_json(), timeout=1.0)
                except TimeoutError:
                    continue
                except Exception:
                    break
                await self._handle_message(msg)
        finally:
            self._running = False
            await self.disconnect()

    # ── Protocol messages (outgoing) ──────────────────────

    async def _send_hello(self):
        auth = {"method": "none"}
        if self._auth_token:
            auth = {"method": "token", "value": self._auth_token}
        await self._ws.send_json(
            {
                "type": "hello",
                "version": "2.1",
                "mode": "persistent",
                "worker_id": self._worker_id,
                "auth": auth,
            }
        )

    async def _receive_session(self):
        msg = await self._ws.receive_json()
        if msg["type"] != "session":
            raise RuntimeError(f"Expected session, got {msg['type']}")
        self._session_id = msg["session_id"]
        self._heartbeat_interval = msg.get("heartbeat_interval_ms", 10000) // 1000

    async def _send_capabilities(self):
        await self._ws.send_json(
            {
                "type": "capabilities",
                "session_id": self._session_id,
                "payload": {
                    "runtimes": self._runtimes,
                    "resources": {
                        "cpu_cores": 4,
                        "ram_mb": 8192,
                        "disk_mb": 51200,
                        "gpu": {"supported": False},
                    },
                    "lifecycle": {
                        "mode": "persistent",
                        "max_lifetime_sec": None,
                        "expected_remaining_sec": None,
                        "idle_timeout_sec": None,
                    },
                    "verification": {"can_challenge": False, "challenge_timeout_max_sec": 0},
                    "network": {"connectivity": "outgoing_only"},
                    "limits": {"max_task_duration_ms": 60000, "max_concurrent_tasks": 2},
                },
            }
        )

    async def _send_heartbeat(self):
        await self._ws.send_json(
            {
                "type": "heartbeat",
                "session_id": self._session_id,
                "current_load_pct": 0.0,
                "tasks_in_progress": 0,
                "uptime_sec": 0,
                "expected_remaining_sec": None,
            }
        )

    async def _send_result(
        self, task_id: str, token: str, status: str, output_hash: str, exit_code: int, stderr: str
    ):
        assert self._ws is not None
        await self._ws.send_json(
            {
                "type": "task_result",
                "session_id": self._session_id,
                "task_id": task_id,
                "assignment_token": token,
                "status": status,
                "result": {
                    "output_hash": output_hash,
                    "execution_metadata": {
                        "duration_ms": 0,
                        "exit_code": exit_code,
                        "stderr": stderr,
                    },
                },
                "verification_data": None,
            }
        )

    # ── Message handling ──────────────────────────────────

    async def _handle_message(self, msg: dict):
        msg_type = msg.get("type", "")
        if msg_type == "heartbeat_ack":
            pass
        elif msg_type in ("task_assign", "keepalive"):
            if msg_type == "task_assign":
                assert self._ws is not None
                await self._ws.send_json(
                    {
                        "type": "task_accept",
                        "session_id": self._session_id,
                        "task_id": msg["task"]["id"],
                        "assignment_token": msg["task"]["assignment_token"],
                    }
                )
            await self._execute_task(msg["task"])

    # ── Task execution ────────────────────────────────────

    async def _execute_task(self, task: dict):
        """Download blobs, execute in sandbox, upload result, submit."""
        token = task.get("assignment_token", "")
        try:
            executable, input_data = await self._download_blobs(
                task["payload"]["executable_hash"],
                task["payload"]["input_hash"],
            )
            output, output_hash = await self._run_sandbox(
                executable, input_data, task.get('assignment_token', ''),
                runtime=task.get('runtime', 'wasm')
            )
            status, exit_code, stderr = "success", 0, ""
        except Exception as e:
            output_hash = ""
            status, exit_code, stderr = "error", 1, str(e)[:4096]

        await self._send_result(task["id"], token, status, output_hash, exit_code, stderr)

    async def _download_blobs(self, exec_hash: str, input_hash: str) -> tuple[bytes, bytes]:
        """Download executable and input blobs from the coordinator."""
        http_url = self._url.replace("ws://", "http://").replace("/worker/ws", "")
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{http_url}/blobs/{exec_hash}") as r:
                executable = await r.read()
            async with session.get(f"{http_url}/blobs/{input_hash}") as r:
                input_data = await r.read()
        return executable, input_data

    async def _run_sandbox(
        self, executable: bytes, input_data: bytes, assignment_token: str = "", runtime: str = "wasm"
    ) -> tuple[bytes, str]:
        """Execute in the configured sandbox, return (output, output_hash)."""
        http_url = self._url.replace("ws://", "http://").replace("/worker/ws", "")

# Python runtime: execute script in thread pool (non-blocking)
        if runtime == "python":
            import asyncio, subprocess as sp, tempfile, pathlib
            loop = asyncio.get_running_loop()
            with tempfile.TemporaryDirectory() as tmp:
                script = pathlib.Path(tmp) / "script.py"
                script.write_bytes(executable)
                try:
                    proc = await loop.run_in_executor(None, lambda: sp.run(["python3", str(script)], input=input_data, capture_output=True, timeout=600))
                    if proc.returncode != 0:
                        err = proc.stderr.decode()[:4096]
                        result = {"output_bytes": err.encode(), "output_hash": hashlib.sha256(err.encode()).hexdigest(), "exit_code": proc.returncode}
                    else:
                        out = proc.stdout
                        try:
                            result = json.loads(out.decode())
                            if "output_bytes" in result and isinstance(result["output_bytes"], str):
                                try:
                                    result["output_bytes"] = bytes.fromhex(result["output_bytes"])
                                except ValueError:
                                    result["output_bytes"] = result["output_bytes"].encode()
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            result = {"output_bytes": out, "output_hash": hashlib.sha256(out).hexdigest(), "exit_code": 0}
                except sp.TimeoutExpired:
                    result = {"output_bytes": b"timeout", "output_hash": hashlib.sha256(b"timeout").hexdigest(), "exit_code": -1}
                except Exception as e:
                    err = str(e).encode()
                    result = {"output_bytes": err, "output_hash": hashlib.sha256(err).hexdigest(), "exit_code": -1}
        else:
            sbox = self._sandboxes.get(runtime, self._sandbox)
            result = await sbox.execute(executable, input_data)
        output: bytes = result.get("output_bytes") or result.get("output_hash", "ok").encode()
        async with aiohttp.ClientSession() as session:
            token_param = f"?assignment_token={assignment_token}" if assignment_token else ""
            async with session.put(f"{http_url}/blobs{token_param}", data=output) as r:
                upload_resp = await r.json()
                output_hash = upload_resp.get("hash", hashlib.sha256(output).hexdigest())
        return output, output_hash


# ── Default sandbox (mock, overridable) ─────────────────────


def _default_sandbox() -> Sandbox:
    from .sandbox import MockSandbox

    return MockSandbox()
