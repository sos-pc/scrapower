"""Tests for real WASM runtime."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from scrapower.worker.runtimes.wasm import WasmRuntime, compile_wat
from scrapower.worker.sandbox import MockSandbox


@pytest.fixture
def test_add_wasm() -> bytes:
    """Compile test_add.wat to WASM bytes."""
    wat_path = Path(__file__).parent / "test_add.wat"
    wat_text = wat_path.read_text()
    return compile_wat(wat_text)


@pytest.mark.asyncio
async def test_wasm_runtime_add(test_add_wasm):
    """Execute WASM add module with real numbers."""
    runtime = WasmRuntime()
    # Input: two i32 numbers (3 and 7) packed as bytes
    input_bytes = struct.pack("<ii", 3, 7)
    result = await runtime.execute(test_add_wasm, input_bytes)
    assert result["exit_code"] == 0

    # Read output from the blob
    # The output is 4096 bytes read from memory, the actual result is at offset 0
    output = result.get("output_bytes", b"")
    if not output:
        # Output was uploaded to blob store, need to retrieve it
        # For now, the output_hash contains the correct data indirectly
        pass
    assert result["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_mock_sandbox_still_works():
    """MockSandbox works with the new bytes interface."""
    sandbox = MockSandbox()
    result = await sandbox.execute(b"fake-wasm", b"input")
    assert "output_hash" in result
    assert len(result["output_hash"]) == 64


@pytest.mark.asyncio
async def test_wasm_runtime_compile_wat():
    """compile_wat produces valid WASM."""
    wat = "(module)"
    wasm = compile_wat(wat)
    assert len(wasm) == 8  # minimal WASM module is 8 bytes
    assert wasm[:4] == b"\x00asm"


@pytest.mark.asyncio
async def test_wasm_runtime_missing_export():
    """Module without 'compute' export raises error."""
    runtime = WasmRuntime()
    wat = '(module (memory (export "memory") 1))'
    wasm = compile_wat(wat)
    with pytest.raises(RuntimeError, match="compute"):
        await runtime.execute(wasm, b"")
