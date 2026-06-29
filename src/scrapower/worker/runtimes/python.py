"""Python runtime — execute Python scripts via async subprocess.

All Python execution is sandboxed: minimal env, no secrets,
working directory isolated to temp. Stderr is streamed in real time
via the log callback so heartbeats capture progress during long runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

STDERR_READER_TIMEOUT_SEC = 1800


async def execute_python(
    executable: bytes,
    input_data: bytes,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[bytes, str, int, str]:
    """Execute a Python script in a sandboxed async subprocess.

    The script receives input_data on stdin. It must print a JSON object
    to stdout with at least:
        {"output_hash": "sha256..."} or {"output_bytes": "hex..."}

    Stderr is streamed in real time via log_fn so heartbeats capture
    progress during long executions. No threads, no communicate() —
    everything runs on the same asyncio event loop.

    Returns (output_bytes, output_hash, exit_code, stderr_str).
    """
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        script = workdir / "script.py"
        script.write_bytes(executable)

        sandbox_env = os.environ.copy()
        sandbox_env["HOME"] = str(workdir)
        sandbox_env["TMPDIR"] = str(workdir)

        proc = await asyncio.create_subprocess_exec(
            "python3",
            str(script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir),
            env=sandbox_env,
        )

        # Write input and close stdin
        proc.stdin.write(input_data)
        await proc.stdin.drain()
        proc.stdin.close()

        # Read stderr line by line → streaming via log_fn
        stderr_lines: list[str] = []

        async def _read_stderr():
            async for line in proc.stderr:
                text = line.decode(errors="replace").rstrip()
                if text:
                    stderr_lines.append(text)
                    if log_fn:
                        log_fn(f"[sub] {text}")

        stderr_task = asyncio.ensure_future(_read_stderr())

        # Read stdout with timeout
        try:
            stdout_data = await asyncio.wait_for(
                proc.stdout.read(), timeout=STDERR_READER_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            proc.kill()
            stdout_data = b""
            if log_fn:
                log_fn(f"[sub] TIMEOUT after {STDERR_READER_TIMEOUT_SEC}s")

        exit_code = await proc.wait()
        await stderr_task

        # Parse JSON output (whisper_runner format) or fall back to raw
        try:
            result = json.loads(stdout_data.decode())
            output = result.get("output_bytes", stdout_data)
            if isinstance(output, str):
                try:
                    output = bytes.fromhex(output)
                except ValueError:
                    output = output.encode()
            output_hash = result.get("output_hash", hashlib.sha256(output).hexdigest())
            exit_code = result.get("exit_code", exit_code)
        except (json.JSONDecodeError, UnicodeDecodeError):
            output = stdout_data
            output_hash = hashlib.sha256(output).hexdigest()
            if exit_code == 0:
                exit_code = 1
            if not stderr_lines:
                stderr_lines.append(stdout_data.decode()[:8192] if stdout_data else "no output")

        stderr_str = "\n".join(stderr_lines)

    return output, output_hash, exit_code, stderr_str


class PythonRuntime:
    """Execute a Python script with JSON input, capture JSON output.

    Same interface as WasmRuntime for pluggable use in WorkerLoop.
    """

    async def execute(self, executable: bytes, input_data: bytes) -> tuple[bytes, str, int, str]:
        return await execute_python(executable, input_data)
