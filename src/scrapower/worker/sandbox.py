"""Sandbox abstraction for task execution.

Pluggable runtimes: wasm (wasmtime), python (subprocess), native (TRUST).
"""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from typing import Any


class Sandbox(ABC):
    """Abstract sandbox for executing tasks."""

    @abstractmethod
    async def execute(self, executable: bytes, input_data: bytes) -> dict[str, Any]:
        """Execute a task. Returns dict with output_hash, duration_ms, exit_code."""
        ...


class MockSandbox(Sandbox):
    """Sandbox that simulates execution for testing."""

    async def execute(self, executable: bytes, input_data: bytes) -> dict[str, Any]:
        start = time.time()
        fake_output = f"mock-result-{hashlib.sha256(executable).hexdigest()[:8]}"
        output_bytes = fake_output.encode()
        output_hash = hashlib.sha256(output_bytes).hexdigest()
        duration_ms = int((time.time() - start) * 1000)
        return {
            "output_hash": output_hash,
            "output_bytes": output_bytes,
            "duration_ms": duration_ms,
        }
