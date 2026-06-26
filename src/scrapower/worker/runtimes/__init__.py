"""Worker runtimes — pluggable execution backends.

Each runtime executes a task blob (executable + input) and returns
a result dict with output_bytes, output_hash, exit_code, and stderr.
"""

from .python import PythonRuntime
from .wasm import WasmRuntime

__all__ = ["PythonRuntime", "WasmRuntime"]
