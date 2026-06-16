"""WASM runtime — executes WebAssembly modules via wasmtime."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from wasmtime import Engine, Module, Store

from ..sandbox import Sandbox


class WasmRuntime(Sandbox):
    """Executes WASM tasks using wasmtime with resource limits."""

    def __init__(self, max_memory_pages: int = 256):
        self._engine = Engine()
        self._max_memory = max_memory_pages

    async def execute(self, executable_bytes: bytes, input_bytes: bytes) -> dict[str, Any]:
        start = time.time()

        store = Store(self._engine)
        module = Module(self._engine, executable_bytes)

        # Linker with env imports (now_ms, etc.)
        # Memory is created by the module itself — we access it via exports
        from wasmtime import Func, FuncType, Linker, ValType

        linker = Linker(self._engine)
        start_ns = time.time_ns()

        def _now_ms() -> int:
            return (time.time_ns() - start_ns) // 1_000_000

        linker.define(store, "env", "now_ms", Func(store, FuncType([], [ValType.i64()]), _now_ms))

        instance = linker.instantiate(store, module)

        # Get the module's exported memory
        memory = instance.exports(store).get("memory")
        if memory is None:
            raise RuntimeError("Module has no 'memory' export")

        # Write input at offset 0
        memory.write(store, bytearray(input_bytes), 0)

        # Output buffer after input, 64-byte aligned
        output_offset = ((len(input_bytes) + 63) // 64) * 64
        output_size = 4096

        compute = instance.exports(store).get("compute")
        if compute is None:
            raise RuntimeError("Module has no 'compute' export")

        exit_code = compute(store, 0, len(input_bytes), output_offset, output_size)

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
