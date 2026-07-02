"""Worker loop — Mode B HTTP pull/submit for Scrapower.

Connects to a coordinator, pulls tasks, executes them via pluggable
runtimes, submits results, and sends heartbeats during execution.
Auto-stops after idle timeout to save resources.
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from typing import Any

import aiohttp

from .runtimes.python import execute_python

HEARTBEAT_INTERVAL_SEC = 30
STDERR_READER_TIMEOUT_SEC = 1800


class WorkerLoop:
    """Main worker loop: pull → execute → upload → submit → repeat.

    Configuration is passed at construction time. Call `run()` to start.
    """

    def __init__(
        self,
        *,
        worker_id: str,
        coordinator_url: str,
        api_key: str = "",
        capabilities: dict[str, Any],
        poll_interval_sec: int = 3,
        idle_timeout_sec: int = 120,
        heartbeat_interval_sec: int = HEARTBEAT_INTERVAL_SEC,
    ):
        self.worker_id = worker_id
        self.coordinator_url = coordinator_url.rstrip("/")
        self.api_key = api_key
        self.capabilities = capabilities
        self.poll_interval_sec = poll_interval_sec
        self.idle_timeout_sec = idle_timeout_sec
        self.heartbeat_interval_sec = heartbeat_interval_sec

        # Log buffer: accumulates stderr during execution, flushed on
        # pull/heartbeat. Enables debugging stuck workers.
        self._log_lines: list[str] = []
        self._log_task_id: str = ""
        self._log_token: str = ""

        # Stats
        self.total_completed: int = 0
        self._last_task_time: float = time.time()

    # -- Logging --------------------------------------------------------

    def _log(self, msg: str) -> None:
        """Append to memory buffer, print to stdout."""
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        self._log_lines.append(line)
        if len(self._log_lines) > 200:
            del self._log_lines[:-100]
        print(line)

    def _drain_logs(self) -> str:
        """Return recent logs for transmission to coordinator."""
        if not self._log_lines:
            return ""
        chunk = "\n".join(self._log_lines[-50:])
        self._log_lines.clear()
        return chunk

    # -- Task execution --------------------------------------------------

    async def _run_task(
        self, executable: bytes, input_data: bytes, rt: str
    ) -> tuple[bytes, str, int, str]:
        """Execute a task. Stderr is streamed via log_fn (set by caller)."""
        if rt == "python":
            return await execute_python(executable, input_data, log_fn=self._log)
        raise ValueError(f"Unknown runtime: {rt}")

    # -- Heartbeat (async, runs as background task) --------------------

    async def _heartbeat(self, session: aiohttp.ClientSession) -> None:
        """Send heartbeat every N seconds during task execution."""
        while self._log_task_id:
            logs = self._drain_logs()
            try:
                async with session.post(
                    f"{self.coordinator_url}/worker/heartbeat",
                    json={
                        "type": "heartbeat",
                        "worker_id": self.worker_id,
                        "task_id": self._log_task_id,
                        "assignment_token": self._log_token,
                        "logs": logs,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status == 200:
                        ack = await r.json()
                        if not ack.get("task_valid"):
                            self._log("Heartbeat: task reassigned, aborting")
                            self._log_task_id = ""
                            return
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._log(f"Heartbeat failed: {e}")
            await asyncio.sleep(self.heartbeat_interval_sec)

    # -- Main loop -------------------------------------------------------

    async def run(self) -> None:
        """Pull → execute → upload → submit → repeat. Exits on idle timeout."""
        self._log(f"Polling {self.coordinator_url}/worker/pull every {self.poll_interval_sec}s...")

        async with aiohttp.ClientSession() as session:
            while True:
                # Drain buffered logs before pull
                logs_chunk = self._drain_logs()

                # PULL (retry on 5xx / transient errors)
                data = None
                for attempt in range(3):
                    try:
                        async with session.post(
                            f"{self.coordinator_url}/worker/pull",
                            json={
                                "type": "pull",
                                "worker_id": self.worker_id,
                                "capabilities": self.capabilities,
                                "logs": logs_chunk,
                            },
                            headers=({"X-API-Key": self.api_key} if self.api_key else {}),
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as r:
                            if r.status >= 500:
                                self._log(f"Pull 5xx ({r.status}), retry {attempt + 1}/3")
                                await asyncio.sleep(2**attempt)
                                continue
                            data = await r.json()
                            break
                    except Exception as e:
                        self._log(f"Pull error: {e}, retry {attempt + 1}/3")
                        await asyncio.sleep(2**attempt)
                        continue

                if data is None:
                    self._log("Pull failed after 3 retries")
                    await asyncio.sleep(self.poll_interval_sec)
                    continue

                task = data.get("task")
                if not task:
                    if time.time() - self._last_task_time > self.idle_timeout_sec:
                        self._log(f"Idle for {self.idle_timeout_sec}s — stopping to save credits")
                        break
                    await asyncio.sleep(self.poll_interval_sec)
                    continue

                # EXECUTE
                self._last_task_time = time.time()
                tid = task["id"][:12]
                tok = task["assignment_token"]
                rt = task.get("runtime", "python")
                self._log(f"Task: {tid}... (runtime={rt})")

                # Download blobs
                try:
                    async with session.get(
                        f"{self.coordinator_url}/blobs/{task['payload']['executable_hash']}",
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as r:
                        executable = await r.read()
                    async with session.get(
                        f"{self.coordinator_url}/blobs/{task['payload']['input_hash']}",
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as r:
                        input_data = await r.read()
                except Exception as e:
                    self._log(f"Blob download failed: {e}")
                    continue

                # Start heartbeat during task execution
                self._log_task_id = task["id"]
                self._log_token = tok
                hb_task = asyncio.create_task(self._heartbeat(session))
                print(f"[HB] heartbeat task created for {task['id'][:12]}", flush=True)

                worker_stderr = ""
                output = b""
                output_hash = ""
                exit_code = 1
                try:
                    result = await self._run_task(executable, input_data, rt)
                    output, output_hash, exit_code, worker_stderr = result
                except Exception as e:
                    worker_stderr = f"{type(e).__name__}: {e}"
                    self._log(f"Error: {worker_stderr}")
                finally:
                    self._log_task_id = ""
                    self._log_token = ""

                self._log(f"OK: {output_hash[:12]}... exit_code={exit_code}")

                # UPLOAD + SUBMIT — retry up to 3 times
                submitted = False
                for attempt in range(3):
                    # Upload result blob
                    try:
                        async with session.put(
                            f"{self.coordinator_url}/blobs?assignment_token={tok}",
                            data=output,
                            timeout=aiohttp.ClientTimeout(
                                total=min(300, max(30, 10 + len(output) // 50_000))
                            ),
                        ) as r:
                            up = await r.json()
                        output_hash = up.get("hash", output_hash)
                    except Exception as e:
                        self._log(f"Blob upload failed (attempt {attempt + 1}/3): {e}")
                        await asyncio.sleep(1)
                        continue

                    # Submit result
                    try:
                        async with session.post(
                            f"{self.coordinator_url}/worker/submit",
                            json={
                                "type": "submit",
                                "task_id": task["id"],
                                "assignment_token": tok,
                                "result": {
                                    "output_hash": output_hash,
                                    "execution_metadata": {
                                        "exit_code": exit_code,
                                        "stderr": worker_stderr,
                                    },
                                },
                            },
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as r:
                            result = await r.json()
                        accepted = result.get("accepted", False)
                        self._log(f"Submit: accepted={accepted}")
                        if accepted:
                            self.total_completed += 1
                            self._log(f"Total completed: {self.total_completed}")
                            submitted = True
                            break
                        self._log(f"Submit rejected (attempt {attempt + 1}/3)")
                    except Exception as e:
                        self._log(f"Submit failed (attempt {attempt + 1}/3): {e}")

                    await asyncio.sleep(1)

                if not submitted:
                    self._log(
                        "Submit failed after 3 attempts — task will be requeued by stale check"
                    )

                # Stop heartbeat (task execution is done)
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

                await asyncio.sleep(self.poll_interval_sec)
