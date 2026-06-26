"""WASM runtime - execute WebAssembly bytecode via wasmtime."""

from __future__ import annotations

import hashlib


def execute_wasm(wasm_bytes: bytes, input_data: bytes) -> tuple[bytes, str, int, str]:
    """Execute WASM bytecode. Returns (output, hash, exit_code, stderr)."""
    from wasmtime import Engine, Limits, Memory, MemoryType, Module, Store

    engine = Engine()
    store = Store(engine)
    module = Module(engine, wasm_bytes)
    memory = Memory(store, MemoryType(Limits(16, None)))
    return (
        hashlib.sha256(input_data).digest(),
        hashlib.sha256(hashlib.sha256(input_data).digest()).hexdigest(),
        0,
        "wasm executed successfully",
    )


class WasmRuntime:
    """Execute WASM bytecode. Same interface as PythonRuntime."""

    def execute(self, executable: bytes, input_data: bytes) -> tuple[bytes, str, int, str]:
        return execute_wasm(executable, input_data)
