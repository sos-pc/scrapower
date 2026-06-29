# Scrapower — Architecture

## Overview

Scrapower is a distributed computing aggregator that dispatches typed tasks
(whisper, wasm, python, fetch) to ephemeral GPU workers (Kaggle, Modal) via
an HTTP pull protocol. A quota-based harvester picks the least-used provider.

```
Client ──POST /tasks {type, requirements}──→ Coordinator (Oracle)
                                                  │
                                                  ├─ EphemeralHarvester (quota-based)
                                                  │   ├─ KaggleHarvester (N comptes, T4 GPU)
                                                  │   └─ ModalHarvester (N comptes, T4 GPU)
                                                  │
                                                  ├─ Task matching: _match_capabilities()
                                                  ├─ Blob store (SHA-256 content-addressed)
                                                  ├─ SQLite (tasks, blobs, sessions)
                                                  ├─ WireGuard homelab VPN (SOCKS5 proxy)
                                                  └─ Reactive fallback (worker DL fail → coordinator DL)
```

## Protocol: Mode B (HTTP pull/submit) — PRIMARY

Workers poll for tasks via HTTP. No persistent connection needed.
Works for Kaggle, Modal, and any ephemeral worker.

```
Worker                              Coordinator
  │                                     │
  │── POST /worker/pull ──────────────→│  capabilities + worker_id + X-API-Key
  │←── {task, assignment_token} ───────│  atomic assign from QUEUED pool
  │                                     │
  │   [execute 2-15 min]                │
  │                                     │
  │── PUT /blobs?token=... ───────────→│  upload output
  │── POST /worker/submit ────────────→│  {task_id, token, output_hash, exit_code}
  │←── {accepted: true} ───────────────│  task → COMPLETED
```

The `exit_code` controls the flow:
- `0` → success → COMPLETED
- `1` → general error → retry
- `2` → DOWNLOAD_FAILED → coordinator fallback (download audio) → retry

Mode A (WebSocket push) is retained for future browser workers.
Toggle via `SCRAPOWER_WS_ASSIGN_ENABLED`.

## Task Types & Matching

Tasks declare their type and requirements; workers declare their capabilities.
The coordinator matches them via `_match_capabilities()`.

### Task definition

```json
POST /tasks {
  "type": "whisper",                        // whisper | wasm | python | fetch
  "requirements": {                         // optional
    "gpu": true,                            // requires GPU
    "network": "outbound",                  // needs internet access
    "ram_mb": 4096                          // minimum RAM
  },
  "runtime": "python",                      // execution engine (wasm | python)
  "executable_hash": "...",
  "input_hash": "..."
}
```

### Worker capabilities

```json
{
  "task_types": ["whisper", "python", "wasm"],
  "runtimes": ["wasm", "python"],
  "resources": {
    "cpu_cores": 4, "ram_mb": 30720,
    "gpu": {"supported": true, "type": "cuda", "model": "T4", "vram_mb": 16384}
  },
  "network": {"connectivity": "outgoing_only"},
  "lifecycle": {"mode": "ephemeral", "max_lifetime_sec": 21600}
}
```

### Matching logic (`_match_capabilities`)

1. `task.type` in `worker.task_types` (fallback to `worker.runtimes` for old workers)
2. `task.runtime` in `worker.runtimes`
3. `task.requirements.gpu` → worker must have GPU
4. `task.requirements.ram_mb` → worker must have enough (floor: 128 MB)
5. `task.requirements.network == "outbound"` → worker must have `outgoing_only` connectivity
6. `worker.lifecycle.remaining` >= `task.deadline_ms`

Used by both Mode B pull handler and Mode A scheduler (same function).

## Task Lifecycle

```
PENDING → DOWNLOADING → QUEUED → ASSIGNED → COMPLETED
                ↓                      ↓
              FAILED                TIMEOUT → QUEUED (retry, fallback if exit_code=2)
                                          ↓
                                       FAILED (max retries=3)
```

## Transcription Flow

```
1. POST /transcribe {url, model} → task PENDING (type="whisper")
2. Input stored as blob {url, cookies_hash}, task → QUEUED
3. Harvester detects queued task → launches worker (best quota %)
4. Worker pulls task (POST /worker/pull)
5. Worker downloads audio via homelab WireGuard SOCKS5 proxy
   → yt-dlp --proxy socks5://... → whisper GPU → transcript
6. Worker submits result → COMPLETED
7. Client polls GET /results/{task_id} → transcript

If download fails (exit_code=2):
  → Coordinator fallback: download audio via homelab VPN
  → Store as blob → task requeued with audio_hash
  → Worker retries with blob (no internet needed)
```

> **Note**: The coordinator-side download is a TEMPORARY BANDAGE.
> Target: workers always download themselves via WireGuard.
> Remove fallback once WG is confirmed stable on all workers.

## Harvester (EphemeralHarvester)

Unified harvester managing all GPU worker providers via `WorkerProvider`.

```
EphemeralHarvester
├── KaggleHarvester   (N comptes, T4, 30h/sem each)
└── ModalHarvester    (N comptes, T4, $30/mois each)
```

**Priority**: providers sorted by `remaining_pct()` — highest first.
A Kaggle account at 93% runs before a Modal account at 80%.

**Lifecycle** (every 15s):
1. Query `remaining_pct()` from all providers
2. Filter < 5% quota
3. Sort by quota % descending
4. Check for queued tasks
5. Launch one worker on best provider
6. Run cleanup on all providers

### WorkerProvider interface

- `remaining_pct()` — quota % (0-100), cross-platform comparable
- `has_quota()` — above minimum threshold
- `launch_worker()` — creates a worker
- `cleanup_stale()` — removes dead workers
- `status()` — returns ProviderStatus

### KaggleHarvester

- `kaggle kernels push` to create notebook workers
- Quota: `kaggle quota --csv` per account (GPU hours)
- Cleanup: delete COMPLETE/ERROR, kill RUNNING > 1h
- Round-robin across accounts

### ModalHarvester

- `modal.Sandbox.create()` with CUDA image (nvidia/cuda:12.4.0)
- GPU T4 ($0.59/h), idle_timeout=2min, max 6h per sandbox
- **Worker delivery**: `deploy/modal/worker.py` is **auto-generated** by
  `scripts/bundle_modal_worker.py` from the canonical sources
  (`src/scrapower/worker/{loop,entry,runtimes/python,wasm}.py`).
  Modal Sandbox API requires a self-contained script (`python -c`),
  unlike Kaggle which installs the package via `pip install git+...`.
  Run the bundler after any source change and commit the result.
- **Budget tracking**: `modal.billing` API as single source of truth
  - Queries ALL accounts every 10 min (cached)
  - Survives coordinator restarts (persisted in `kv_store` DB)
  - Per-account margin: simple sandbox count (~2% per active sandbox)

## VPN

### Homelab WireGuard + SOCKS5 proxy (primary)

Workers route YouTube downloads through SOCKS5 proxy on Oracle,
which tunnels to homelab residential IP via WireGuard.

```
HOMELAB (residential IP)
  │ WireGuard server (UDP :443)
  └── WireGuard tunnel ──┐
                          ▼
ORACLE VM
  ├── WireGuard client (wg0, table 51820)
  ├── Dante SOCKS5 :1081 (auth, external: wg0)
  │     ↑
  │     │ yt-dlp --proxy socks5://scrapower:PASS@your-coordinator.example.com:1081
  │     │
  └── Coordinator (host network, port 8777)
```

**Critical iptables fix**: marks SOCKS5 reply packets to avoid routing loops:

```sh
iptables -t mangle -A OUTPUT -p tcp --sport 1081 -j MARK --set-mark 0xca6c
```

### CyberGhost (fallback)

Docker OpenVPN + SOCKS5. Used only if homelab is down.

## Blob Store

SHA-256 content-addressed, immutable, reference-counted.

```
PUT  /blobs        → hash = SHA-256(data)
GET  /blobs/{hash} → data
```

Ref_count incremented on task reference, decremented on cleanup.
GC deletes blobs at ref_count=0.

## Scheduler

- **Mode B (HTTP pull)**: Workers pull tasks. Pull handler does atomic assign + `_match_capabilities()`.
- **Mode A (WS push)**: Scheduler pushes tasks to connected WS workers. Uses same `_match_capabilities()`.
- **requeue_stale()**: ASSIGNED tasks past 90s timeout are requeued. All worker signals (pull, heartbeat, submit) reset `assigned_at`.
- **cleanup_expired()**: COMPLETED/FAILED tasks deleted after 24h, blob refs released.

## Workers

### Active

| Type | GPU | RAM | Internet | Protocol |
|------|-----|-----|----------|----------|
| Kaggle (notebook) | T4 | 30 GB | Via WG proxy | Mode B |
| Modal (sandbox) | T4 | 30 GB | Via WG proxy | Mode B |

### Planned (future)

| Type | GPU | RAM | Protocol |
|------|-----|-----|----------|
| Browser (WASM/WebGPU) | WebGPU | Variable | Mode A (WS) |
| HF Spaces (CPU) | None | 16 GB | Mode A (WS) |
| Cloud Run / Lambda | None | Variable | Mode B |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/tasks` | Submit a typed task |
| POST | `/transcribe` | Sugar: whisper task from YouTube URL |
| GET | `/tasks/{id}` | Task status + error + logs_url |
| GET | `/tasks/{id}/logs` | Worker stderr log |
| GET | `/results/{id}` | Task output blob |
| PUT | `/blobs` | Upload blob (auth: API key or assignment_token) |
| GET | `/blobs/{hash}` | Download blob |
| POST | `/worker/pull` | Mode B: worker polls for task |
| POST | `/worker/submit` | Mode B: worker returns result |
| POST | `/worker/heartbeat` | Worker liveness + logs during execution |
| WS | `/worker/ws` | Mode A: browser connection |
| GET | `/stats` | Infrastructure capacity |
| GET | `/health` | Health check |

## Security

- **API key**: `X-API-Key` header on client endpoints
- **Worker auth**: same header on `/worker/pull` (dual-mode: auth 30/min, anon 6/min)
- **`assignment_token`**: anti-race-condition on task submit
- **TLS**: Caddy reverse proxy, Let's Encrypt
- **Rate limiting**: per-worker_id (auth) or per-IP (anon), sliding window
