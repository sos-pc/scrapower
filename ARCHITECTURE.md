# Scrapower — Architecture

## Overview

Scrapower is a distributed computing aggregator that dispatches tasks (WASM or Python) to ephemeral workers (Kaggle notebooks, Modal Sandboxes, HuggingFace Spaces, browser tabs).

```
Client ──POST /transcribe──→ Coordinator (Oracle)
                                  │
                                  ├─ EphemeralHarvester (quota-based)
                                  │   ├─ KaggleHarvester (3 comptes, T4 GPU)
                                  │   └─ ModalHarvester (2 comptes, T4 GPU)
                                  │
                                  ├─ Blob store (SHA-256 content-addressed)
                                  ├─ SQLite (tasks, blobs, sessions)
                                  ├─ VPN (CyberGhost, yt-dlp fallback)
                                  └─ Universal fallback (Mode B → Mode A)
```

## Protocol: Mode B (HTTP pull/submit) — PRIMARY

Workers poll for tasks via HTTP. No persistent connection needed. Works for Kaggle, Modal, and any ephemeral worker.

```
Worker                              Coordinator
  │                                     │
  │── POST /worker/pull ──────────────→│  capabilities + worker_id
  │←── {task, assignment_token} ───────│  atomic assign from QUEUED pool
  │                                     │
  │   [execute 2-15 min]                │
  │                                     │
  │── PUT /blobs?token=... ───────────→│  upload output
  │── POST /worker/submit ────────────→│  {task_id, token, output_hash, exit_code}
  │←── {accepted: true} ───────────────│  task → COMPLETED
```

The `exit_code` in the submit payload controls the flow:
- `0` → success → COMPLETED
- `1` → general error → retry
- `2` → DOWNLOAD_FAILED → coordinator fallback (download audio) → retry

Mode A (WebSocket push) is retained for browser workers. Toggle via `SCRAPOWER_WS_ASSIGN_ENABLED`.

## Task Lifecycle

```
PENDING → DOWNLOADING → QUEUED → ASSIGNED → COMPLETED
                ↓                      ↓
              FAILED                TIMEOUT → QUEUED (retry, fallback if exit_code=2)
                                          ↓
                                       FAILED (max retries=3)
```

- **PENDING**: Task created, input not yet ready
- **DOWNLOADING**: Coordinator or worker preparing input
- **QUEUED**: Ready for dispatch
- **ASSIGNED**: Pulled by a worker, token-protected
- **COMPLETED**: Worker submitted valid result
- **TIMEOUT**: Worker didn't respond or returned error → requeue
- **FAILED**: Max retries exhausted

## Transcription Flow (Universal Mode B → Mode A)

```
1. POST /transcribe {url, model} → task PENDING
2. Background: input config stored as blob (instant, no download)
3. Task → QUEUED
4. Harvester detects queued task → launches worker (Kaggle or Modal, chosen by quota %)
5. Worker pulls task (POST /worker/pull)
6. Worker downloads executable + input blobs
7. Mode B: worker tries yt-dlp download from YouTube
   ├─ SUCCESS → whisper → submit (exit_code=0) → COMPLETED
   └─ FAIL (exit_code=2) → coordinator fallback:
        ├─ yt-dlp via VPN → audio blob (native format, no ffmpeg)
        ├─ input updated with audio_hash
        └─ requeue → worker retries Mode A (blob download)
8. Worker downloads audio blob → whisper on GPU → submit → COMPLETED
9. Client polls GET /results/{task_id} → transcript
```

This universal flow minimizes coordinator CPU: the fast path (Mode B) uses zero Oracle resources. The fallback (Mode A) kicks in only when workers can't download directly.

## Harvester (EphemeralHarvester)

Unified harvester managing all GPU worker providers via the `WorkerProvider` interface:

```
EphemeralHarvester
├── KaggleHarvester   (3 comptes, T4, 30h/sem ×3)
└── ModalHarvester    (2 comptes, T4, $30/mois ×2)
```

**Priority**: providers are sorted by `remaining_pct()` — the account with the highest remaining quota percentage is used first. No platform is favored. A Kaggle account at 93% runs before a Modal account at 80%.

**Lifecycle**: every 15s, the harvester:
1. Queries `remaining_pct()` from all providers
2. Filters out providers with < 5% quota
3. Sorts by quota % descending
4. Checks for queued GPU tasks
5. Launches one worker on the top provider
6. Runs cleanup on all providers

### WorkerProvider ABC

Each provider implements:
- `remaining_pct()` — quota as percentage (0-100), comparable across platforms
- `has_quota()` — true if above minimum threshold
- `launch_worker()` — creates a worker (kernel/sandbox)
- `cleanup_stale()` — removes dead/orphaned workers
- `status()` — returns ProviderStatus

### KaggleHarvester

- Uses `kaggle kernels push` to create notebook workers
- Quota via `kaggle quota --csv` (GPU hours remaining)
- Cleanup: deletes COMPLETE/ERROR kernels, kills RUNNING > 1h
- Round-robin across accounts

### ModalHarvester

- Uses `modal.Sandbox.create()` with CUDA image (nvidia/cuda:12.4.0)
- GPU T4 ($0.59/h), idle_timeout=2min, max 6h per sandbox
- Worker script runs Mode B polling loop, auto-exits after idle
- Round-robin across accounts via `_next_account()`
- Budget tracking: local counter (seconds × $/sec rate)

## VPN (CyberGhost)

A dedicated Docker container runs OpenVPN + SOCKS5 proxy. Used only for coordinator-side fallback downloads (Mode A).

YouTube blocks datacenter IPs. The VPN provides a clean exit IP. Only YouTube requests are routed through the proxy: `yt-dlp --proxy socks5://127.0.0.1:1080`.

Cookies are exported from a private browser window (as per yt-dlp docs), uploaded to the blob store, and made available to workers that need them.

## Blob Store

Content-addressed by SHA-256. Immutable. Reference-counted.

```
PUT /blobs  →  hash = SHA-256(data)
GET /blobs/{hash} → data
```

Ref_count is incremented when a task references a blob, decremented on cleanup. GC only deletes blobs with ref_count=0.

## Scheduler

- **Mode B (HTTP pull)**: Workers pull tasks. The pull handler does atomic assign.
- **Mode A (WS push)**: Scheduler pushes tasks to connected WS workers (disabled by default).
- **requeue_stale()**: ASSIGNED tasks past their deadline are requeued.
- **cleanup_expired()**: Old COMPLETED/FAILED tasks are deleted, blob refs released.

## Workers

### Active worker types

| Type | GPU | RAM | Internet | Protocol |
|------|-----|-----|----------|----------|
| Kaggle (notebook) | T4 ×1 | 30 GB | Yes | Mode B |
| Modal (sandbox) | T4 ×1 | 30 GB | Yes | Mode B |
| HF Spaces | None | 16 GB | Yes | Mode A (WS) |
| Browser (WASM) | WebGPU | Variable | Limited | Mode A (WS) |

### Capabilities

Workers declare their capabilities on connection/pull:
```json
{
  "runtimes": ["wasm", "python"],
  "resources": {
    "cpu_cores": 4, "ram_mb": 30720,
    "gpu": {"supported": true, "type": "cuda", "model": "T4", "vram_mb": 16384}
  },
  "lifecycle": {"mode": "ephemeral", "max_lifetime_sec": 21600},
  "network": {"connectivity": "outgoing_only"}
}
```

Matching: `gpu_required=true` tasks are only assigned to workers with `gpu.supported=true`. More granular GPU matching (model, VRAM) is planned for v0.6.
