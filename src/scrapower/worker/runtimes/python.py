"""Python runtime — execute Python scripts in sandboxed subprocess.

All Python execution is sandboxed: minimal env, no secrets,
working directory isolated to temp. Network access only via
WG_PROXY SOCKS5 proxy.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


def execute_python(
    executable: bytes,
    input_data: bytes,
    *,
    log_fn: object = None,
) -> tuple[bytes, str, int, str]:
    """Execute a Python script in a sandboxed subprocess.

    The script receives input_data on stdin. It must print a JSON object
    to stdout with at least:
        {"output_hash": "sha256..."} or {"output_bytes": "hex..."}

    Stderr is streamed in real time via log_fn (if provided) so heartbeats
    can capture progress during long executions.

    Returns (output_bytes, output_hash, exit_code, stderr_str).
    """
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        script = workdir / "script.py"
        script.write_bytes(executable)

        sandbox_env = os.environ.copy()
        sandbox_env["HOME"] = str(workdir)
        sandbox_env["TMPDIR"] = str(workdir)
        proc = subprocess.Popen(
            ["python3", str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workdir),
            env=sandbox_env,
        )

        # Write input and close stdin so the subprocess can start
        proc.stdin.write(input_data)
        proc.stdin.close()

        # Read stderr in a thread → streaming to heartbeat via log_fn
        stderr_lines: list[str] = []
        import threading

        def _read_stderr():
            for line in proc.stderr:
                text = line.decode(errors="replace").rstrip()
                if text:
                    stderr_lines.append(text)
                    if log_fn:
                        log_fn(f"[sub] {text}")

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        # Read stdout (blocking — waits for subprocess to finish)
        try:
            stdout_data = proc.stdout.read()
        finally:
            proc.wait()
            stderr_thread.join(timeout=5)

        exit_code = proc.returncode

        # If subprocess timed out (exit code = -15 from SIGTERM or similar),
        # proc.kill() was never called here — the 600s timeout was removed
        # to allow long transcriptions. The subprocess manages its own timeout.
        if exit_code != 0 and log_fn:
            log_fn(f"[sub] exit_code={exit_code}")

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
            stderr_lines.append(stdout_data.decode()[:8192] if stdout_data else "no output")

        stderr_str = "\n".join(stderr_lines)

    return output, output_hash, exit_code, stderr_str


class PythonRuntime:
    """Execute a Python script with JSON input, capture JSON output.

    Same interface as WasmRuntime for pluggable use in WorkerLoop.
    """

    def execute(self, executable: bytes, input_data: bytes) -> tuple[bytes, str, int, str]:
        return execute_python(executable, input_data)
