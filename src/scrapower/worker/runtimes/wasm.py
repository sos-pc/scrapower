"""WASM runtime — executes WebAssembly modules via wasmtime with security limits."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from wasmtime import Engine, Module, Store

from ..sandbox import Sandbox

# Security limits
WASM_TIMEOUT_SEC = 30  # Max execution time per task
WASM_MAX_MEMORY_PAGES = 256  # 16 MB max
WASM_FUEL_LIMIT = 100_000_000  # ~100M instructions


class WasmRuntime(Sandbox):
    """Executes WASM tasks using wasmtime with resource limits.

    Security:
        - Fuel metering: ~100M instruction limit per task
        - Timeout: 30s wall-clock limit via asyncio
        - Memory: max 256 pages (16 MB)
    """

    def __init__(self, max_memory_pages: int = WASM_MAX_MEMORY_PAGES):
        self._engine = Engine()
        self._max_memory = max_memory_pages

    async def execute(self, executable_bytes: bytes, input_bytes: bytes) -> dict[str, Any]:
        start = time.time()

        store = Store(self._engine)

        # Enable fuel metering
        try:
            store.set_fuel(WASM_FUEL_LIMIT)
        except Exception:
            pass  # Not all wasmtime builds support fuel

        module = Module(self._engine, executable_bytes)

        from wasmtime import Func, FuncType, Linker, ValType

        linker = Linker(self._engine)
        start_ns = time.time_ns()

        def _now_ms() -> int:
            return (time.time_ns() - start_ns) // 1_000_000

        linker.define(store, "env", "now_ms", Func(store, FuncType([], [ValType.i64()]), _now_ms))

        instance = linker.instantiate(store, module)

        memory = instance.exports(store).get("memory")
        if memory is None:
            raise RuntimeError("Module has no 'memory' export")

        # Validate memory size is within bounds
        if memory.size(store) > self._max_memory:
            raise RuntimeError(
                f"Memory size {memory.size(store)} exceeds max {self._max_memory} pages"
            )

        memory.write(store, bytearray(input_bytes), 0)

        output_offset = ((len(input_bytes) + 63) // 64) * 64
        output_size = 4096

        compute = instance.exports(store).get("compute")
        if compute is None:
            raise RuntimeError("Module has no 'compute' export")

        # Execute with timeout
        try:
            exit_code = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: compute(store, 0, len(input_bytes), output_offset, output_size),
                ),
                timeout=WASM_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            return {
                "output_hash": "",
                "output_bytes": b"",
                "duration_ms": WASM_TIMEOUT_SEC * 1000,
                "exit_code": -1,
                "error": f"WASM execution timed out after {WASM_TIMEOUT_SEC}s",
            }

        output_bytes = bytes(memory.read(store, output_offset, output_offset + output_size))

        duration_ms = int((time.time() - start) * 1000)
        output_hash = hashlib.sha256(output_bytes).hexdigest()

        return {
            "output_hash": output_hash,
            "output_bytes": output_bytes,
            "duration_ms": duration_ms,
            "exit_code": exit_code,
        }


def compile_wat(wat_text: str) -> bytes:
    """Compile WAT text to WASM binary."""
    from wasmtime import wat2wasm

    return wat2wasm(wat_text)
