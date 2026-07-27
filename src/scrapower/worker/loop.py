"""Worker loop — Mode B HTTP pull/submit for Scrapower.

Connects to a coordinator, pulls tasks, executes them via pluggable
runtimes, submits results, and sends heartbeats during execution.
Auto-stops after idle timeout to save resources.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from .runtimes.python import execute_python

HEARTBEAT_INTERVAL_SEC = 30
STDERR_READER_TIMEOUT_SEC = 7200


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

        # Set by _heartbeat when the coordinator reports the assignment is no
        # longer ours; the running subprocess watches it and aborts.
        self._abort = asyncio.Event()

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

    def _auth_headers(self) -> dict:
        """API-key header sent on every authenticated worker endpoint."""
        return {"X-API-Key": self.api_key} if self.api_key else {}

    async def _fetch_blob(self, session: aiohttp.ClientSession, blob_hash: str, kind: str) -> bytes:
        """Download a blob, refusing to treat an error body as content.

        Without the status check a 404 JSON body became the task's executable
        and blew up as a SyntaxError, which read like a broken task rather than
        a missing blob.
        """
        async with session.get(
            f"{self.coordinator_url}/blobs/{blob_hash}",
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            body = await r.read()
            if r.status != 200:
                raise RuntimeError(
                    f"{kind} blob {blob_hash[:12]} -> HTTP {r.status}: "
                    f"{body[:200].decode(errors='replace')}"
                )
            return body

    # -- Task execution --------------------------------------------------

    async def _run_task(
        self, executable: bytes, input_data: bytes, rt: str
    ) -> tuple[bytes, str, int, str]:
        """Execute a task. Stderr is streamed via log_fn (set by caller)."""
        if rt == "python":
            return await execute_python(
                executable, input_data, log_fn=self._log, cancel_event=self._abort
            )
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
                    headers=self._auth_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status == 200:
                        ack = await r.json()
                        if not ack.get("task_valid"):
                            self._log("Heartbeat: task reassigned, aborting")
                            self._abort.set()  # kills the running subprocess
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
                            headers=self._auth_headers(),
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as r:
                            if r.status >= 500:
                                self._log(f"Pull 5xx ({r.status}), retry {attempt + 1}/3")
                                await asyncio.sleep(2**attempt)
                                continue
                            # Distinguish real failures from "queue is empty":
                            # both used to fall through to task=None and look
                            # identical in the logs until the idle timeout.
                            if r.status == 401:
                                self._log("Pull UNAUTHORIZED (401) - API key rejected")
                                await asyncio.sleep(2**attempt)
                                continue
                            if r.status == 429:
                                self._log("Pull RATE LIMITED (429) - backing off")
                                await asyncio.sleep(max(5, 2**attempt))
                                continue
                            if r.status != 200:
                                body = (await r.text())[:200]
                                self._log(f"Pull unexpected HTTP {r.status}: {body}")
                                await asyncio.sleep(2**attempt)
                                continue
                            data = await r.json()
                            break
                    except Exception as e:
                        self._log(f"Pull error: {e}, retry {attempt + 1}/3")
                        await asyncio.sleep(2**attempt)
                        continue

                if data is None:
                    # Apply the idle deadline here too. Otherwise a worker whose
                    # key was revoked (or that is permanently rate-limited) spins
                    # on this branch forever, burning GPU quota doing nothing
                    # until the platform kills it at max lifetime.
                    self._log("Pull failed after 3 retries")
                    if time.time() - self._last_task_time > self.idle_timeout_sec:
                        self._log(
                            f"No work obtainable for {self.idle_timeout_sec}s - stopping"
                        )
                        break
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

                # Download blobs. Status must be checked: a 404 body would
                # otherwise be handed to the runtime as the "executable" and
                # reported as a task bug instead of a missing blob.
                try:
                    executable = await self._fetch_blob(
                        session, task["payload"]["executable_hash"], "executable"
                    )
                    input_data = await self._fetch_blob(
                        session, task["payload"]["input_hash"], "input"
                    )
                except Exception as e:
                    self._log(f"Blob download failed: {e}")
                    await asyncio.sleep(self.poll_interval_sec)
                    continue

                # Start heartbeat during task execution
                self._abort.clear()  # fresh abort flag per task
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

                # Aborted mid-execution: the task belongs to another worker now,
                # so uploading and submitting would only be rejected on a stale
                # token. Skip straight to the next pull.
                if self._abort.is_set():
                    self._log("Task aborted (reassigned) - skipping upload/submit")
                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass
                    await asyncio.sleep(self.poll_interval_sec)
                    continue

                self._log(f"Result: {output_hash[:12]}... exit_code={exit_code}")

                # UPLOAD + SUBMIT — retry up to 3 times
                submitted = False
                for attempt in range(3):
                    # Upload result blob. A rejected upload used to fall back to
                    # the locally computed hash, so the worker "succeeded" here
                    # and then burned its 3 submit attempts on a blob the
                    # coordinator never stored — with the real cause (413 too
                    # large, 401 bad token) never logged.
                    try:
                        async with session.put(
                            f"{self.coordinator_url}/blobs?assignment_token={tok}",
                            data=output,
                            timeout=aiohttp.ClientTimeout(
                                total=min(300, max(30, 10 + len(output) // 50_000))
                            ),
                        ) as r:
                            if r.status != 200:
                                detail = (await r.text())[:200]
                                self._log(
                                    f"Blob upload REJECTED (HTTP {r.status}) "
                                    f"size={len(output)}B attempt {attempt + 1}/3: {detail}"
                                )
                                await asyncio.sleep(1)
                                continue
                            up = await r.json()
                        uploaded_hash = up.get("hash", "")
                        if not uploaded_hash:
                            self._log("Blob upload returned no hash - retrying")
                            await asyncio.sleep(1)
                            continue
                        if uploaded_hash != output_hash:
                            # The coordinator hashes what it actually stored, so
                            # trust it over our local computation.
                            self._log(
                                f"Upload hash differs (local={output_hash[:12]} "
                                f"stored={uploaded_hash[:12]}) - using stored"
                            )
                        output_hash = uploaded_hash
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
                            headers=self._auth_headers(),
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as r:
                            if r.status not in (200, 400, 403):
                                # 400/403 carry a JSON submit_ack we still want to
                                # read; anything else (401, 5xx) is not a verdict
                                # on the result, so surface it instead of letting
                                # r.json() raise something unrelated.
                                detail = (await r.text())[:200]
                                self._log(
                                    f"Submit HTTP {r.status} (attempt {attempt + 1}/3): {detail}"
                                )
                                await asyncio.sleep(1)
                                continue
                            result = await r.json()
                        accepted = result.get("accepted", False)
                        reason = result.get("reason", "")
                        self._log(
                            f"Submit: accepted={accepted}"
                            + (f" reason={reason}" if reason else "")
                        )
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
