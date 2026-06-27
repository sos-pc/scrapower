# Worker Package Refactor — Test Report (2026-06-27)

**Commit**: `fffb5d5` — Extract common worker code into scrapower.worker package
**Goal**: Single source of truth for all workers (Modal, Kaggle, HF Spaces)

---

## Architecture

```
src/scrapower/worker/          ← source unique (480 lignes)
├── entry.py                   ← main(), GPU detection, capabilities
├── loop.py                    ← WorkerLoop (pull/execute/heartbeat/submit)
└── runtimes/
    ├── python.py              ← PythonRuntime (sandboxed subprocess)
    └── wasm.py                ← WasmRuntime (wasmtime)

Deploy wrappers (thin):
├── deploy/modal/worker.py     ← 10 lignes (was 440)
├── deploy/hf-spaces/app.py    ← 80 lignes (was 380)
└── deploy/kaggle/sworker.ipynb ← 2 cells, {{PLACEHOLDER}} syntax
```

## Test Results

### Working
- **HF Spaces**: Full cycle OK (pull → heartbeat → download → execute → upload → submit)
- **Kaggle kernels**: 3 kernels started successfully (notebook push OK)
- **Coordinator**: Starts clean, routes tasks
- **Module imports**: All compile clean

### Issues Found

| # | Severity | Component | Symptom | Hypothesis |
|---|----------|-----------|---------|------------|
| M1 | 🔴 | Modal | Sandbox created then immediately terminated | `python -m scrapower.worker.entry` fails at import or PYTHONPATH |
| M2 | 🔴 | Kaggle | Kernels created but no pull activity | pip install git+... slow, or nest_asyncio crash, or notebook error |
| M3 | 🟡 | Coordinator | GPU task assigned to CPU worker | `_match_capabilities` doesn't check `gpu_required` |
| M4 | 🟡 | Worker | YouTube 403 on HF Spaces worker | WG_PROXY not configured on HF Spaces? |
| M5 | 🟠 | Modal | Workspace spend limit on some accounts | methammer Modal account exhausted — not a code bug |

### Account Status (known quota issues)
- **methammer** (Kaggle): 79% remaining → actually 0h (30.07/30h used) — EXHAUSTED
- **methammer** (Modal): Workspace spend limit exceeded — EXHAUSTED
- **piotjeremie** (Kaggle): 9.58h remaining
- **ultimatethunderbolts** (Kaggle): 23.73h remaining
- **piotjeremie** (Modal): has credits

---

## Next Steps

1. Get Modal sandbox logs → see why `python -m scrapower.worker.entry` crashes
2. Get Kaggle kernel logs → see if notebook cells execute or crash
3. Check WG_PROXY on HF Spaces → add to secrets/env
4. Fix `_match_capabilities` → respect `gpu_required`
