"""Native worker — connects to coordinator, executes tasks."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import aiohttp

from .sandbox import MockSandbox, Sandbox


class WorkerClient:
    """Implements Worker Protocol v2.1 Mode A (WebSocket)."""

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
        self._sandbox = sandbox or MockSandbox()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session_id: str | None = None
        self._running = False
        self._heartbeat_interval = 10

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def connect(self):
        """Connect to coordinator, complete handshake."""
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._url)

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

        msg = await self._ws.receive_json()
        if msg["type"] != "session":
            raise RuntimeError(f"Expected session, got {msg['type']}")
        self._session_id = msg["session_id"]
        self._heartbeat_interval = msg.get("heartbeat_interval_ms", 10000) // 1000

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

    async def run(self):
        """Main loop: heartbeat + receive messages."""
        self._running = True
        await self.connect()
        next_hb = asyncio.get_event_loop().time() + self._heartbeat_interval

        try:
            while self._running and self._ws and not self._ws.closed:
                now = asyncio.get_event_loop().time()
                if now >= next_hb:
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
                    next_hb = now + self._heartbeat_interval

                try:
                    msg = await asyncio.wait_for(self._ws.receive_json(), timeout=1.0)
                except TimeoutError:
                    continue
                except Exception:
                    break

                msg_type = msg.get("type", "")
                if msg_type == "heartbeat_ack":
                    pass
                elif msg_type == "task_assign":
                    await self._ws.send_json(
                        {
                            "type": "task_accept",
                            "session_id": self._session_id,
                            "task_id": msg["task"]["id"],
                            "assignment_token": msg["task"]["assignment_token"],
                        }
                    )
                    await self._execute(msg["task"])
                elif msg_type == "keepalive":
                    await self._execute(msg["task"])
        finally:
            self._running = False
            if self._ws and not self._ws.closed:
                await self._ws.close()
            if self._session:
                await self._session.close()

    async def _execute(self, task: dict):
        """Download blobs, execute task, upload result, submit."""
        http_url = self._url.replace("ws://", "http://").replace("/worker/ws", "")
        exec_hash = task["payload"]["executable_hash"]
        input_hash = task["payload"]["input_hash"]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{http_url}/blobs/{exec_hash}") as r:
                    executable = await r.read()
                async with session.get(f"{http_url}/blobs/{input_hash}") as r:
                    input_data = await r.read()

                result = await self._sandbox.execute(executable, input_data)

                output = result.get("output_bytes", result.get("output_hash", "ok").encode())
                if isinstance(output, str):
                    output = output.encode()
                async with session.put(f"{http_url}/blobs", data=output) as r:
                    upload_resp = await r.json()
                    output_hash = upload_resp.get("hash", hashlib.sha256(output).hexdigest())

                status = "success"
                exit_code = 0
                stderr = ""
        except Exception as e:
            status = "error"
            output_hash = ""
            exit_code = 1
            stderr = str(e)[:4096]

        await self._ws.send_json(
            {
                "type": "task_result",
                "session_id": self._session_id,
                "task_id": task["id"],
                "assignment_token": task.get("assignment_token", ""),
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

    async def disconnect(self):
        """Send bye and close."""
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
