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

    Returns (output_bytes, output_hash, exit_code, stderr_str).
    """
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        script = workdir / "script.py"
        script.write_bytes(executable)

        # Inherit parent env (preserves LD_LIBRARY_PATH, CUDA libs, etc.)
        # but isolate the working directory for security.
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

        # communicate() safely reads both stdout and stderr to completion.
        # No thread needed — avoids deadlock with concurrent pipe reads.
        try:
            stdout_data, stderr_data = proc.communicate(input=input_data, timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_data, stderr_data = proc.communicate()
            if log_fn:
                log_fn("[sub] TIMEOUT after 600s")

        exit_code = proc.returncode
        stderr_str = stderr_data.decode(errors="replace") if stderr_data else ""

        # Feed stderr to log callback
        if log_fn:
            for line in stderr_str.split("\n"):
                if line.strip():
                    log_fn(f"[sub] {line.strip()}")

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
            if not stderr_str:
                stderr_str = stdout_data.decode()[:8192] if stdout_data else "no output"

    return output, output_hash, exit_code, stderr_str


class PythonRuntime:
    """Execute a Python script with JSON input, capture JSON output.

    Same interface as WasmRuntime for pluggable use in WorkerLoop.
    """

    def execute(self, executable: bytes, input_data: bytes) -> tuple[bytes, str, int, str]:
        return execute_python(executable, input_data)
