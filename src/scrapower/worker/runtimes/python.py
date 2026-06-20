"""Python runtime sandbox — executes Python scripts via subprocess.

Used for tasks with runtime="python" (whisper, scraping, LLM inference).
NOT sandboxed — only use on trusted workers (Oracle, HF Spaces, homelab).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


class PythonRuntime:
    """Execute a Python script with JSON input, capture JSON output.

    The script receives input as a JSON string on stdin (or first argv).
    It must output a JSON object on stdout with at least:
      {"output_hash": "sha256..."}  or  {"output_bytes": "hex..."}
    """

    async def execute(self, executable: bytes, input_data: bytes) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "script.py"
            script.write_bytes(executable)

            proc = subprocess.run(
                ["python", str(script)],
                input=input_data,
                capture_output=True,
                timeout=600,  # 10 minutes max
                cwd=tmp,
            )

            stdout = proc.stdout.decode("utf-8", errors="replace").strip()
            stderr = proc.stderr.decode("utf-8", errors="replace")[:4096]

            if proc.returncode != 0:
                return {
                    "output_bytes": stderr.encode(),
                    "output_hash": hashlib.sha256(stderr.encode()).hexdigest(),
                    "exit_code": proc.returncode,
                    "stderr": stderr,
                }

            # The script should output JSON with output_hash or output_bytes
            try:
                result = json.loads(stdout)
            except json.JSONDecodeError:
                # Plain text output
                output = stdout.encode()
                result = {
                    "output_bytes": output,
                    "output_hash": hashlib.sha256(output).hexdigest(),
                }

            # If output_bytes is hex-encoded, decode it
            if "output_bytes" in result and isinstance(result["output_bytes"], str):
                try:
                    result["output_bytes"] = bytes.fromhex(result["output_bytes"])
                except ValueError:
                    result["output_bytes"] = result["output_bytes"].encode()

            result.setdefault("exit_code", 0)
            result.setdefault("stderr", stderr)
            return result
