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
2. Coordinator checks worker capabilities:
   ├─ Workers with ip_reputation=residential → Mode B (worker DL, 0 CPU Oracle)
   └─ All workers ip_reputation=datacenter → Mode A (pre-DL, fallback)
3. Input stored as blob, task → QUEUED
4. Harvester detects queued task → launches worker (chosen by quota %)
5. Worker pulls task (POST /worker/pull)
6. Worker downloads executable + input blobs
7. Mode B: worker runs yt-dlp via homelab WireGuard VPN
   → IP résidentielle → YouTube sert le contenu → whisper
8. Mode A: worker downloads audio blob from coordinator
   → whisper on GPU → submit → COMPLETED
9. Client polls GET /results/{task_id} → transcript
```

### Decision flow (coordinator `_prepare_whisper_input`)

```
Any active worker has ip_reputation != "datacenter" ?
  YES → store {url, cookies_hash} (Mode B, worker DL)
  NO  → download audio via homelab VPN/CyberGhost
         store {audio_hash} (Mode A, worker reads blob)
```

This eliminates the wasteful fallback cycle observed in testing: every task going
through Mode B failure → fallback → Mode A retry. The coordinator knows ahead of time
whether workers can download directly.

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

## VPN

### Homelab WireGuard (primary)

A WireGuard server on the homelab provides a residential IP exit node for all workers:

```
┌──────────────────────────────────────────────────────┐
│  HOMELAB (IP résidentielle)                          │
│  ┌──────────────────────┐                            │
│  │ WireGuard server     │                            │
│  │ Port UDP 51820       │                            │
│  └──────────┬───────────┘                            │
└─────────────┼────────────────────────────────────────┘
              │
    ┌─────────┼─────────┬──────────────┐
    ▼         ▼         ▼              ▼
┌───────┐ ┌───────┐ ┌───────┐    ┌───────────┐
│ Modal │ │ Kaggle│ │Coord. │    │ Browser   │
│Sandbox│ │Kernel │ │Oracle │    │ (futur)   │
│ wg0   │ │ wg0   │ │ wg0   │    │           │
└───────┘ └───────┘ └───────┘    └───────────┘

Tous les workers → yt-dlp → homelab:51820 → YouTube
IP résidentielle → pas de blocage anti-bot
```

**Avantages** :
- IP résidentielle = pas de blocage YouTube/Cloudflare
- Gratuit (juste la connexion internet existante)
- Utilisable par tous les workers, pas seulement Oracle
- WireGuard = intégré au kernel, ultra-léger
- DuckDNS pour IP dynamique

### CyberGhost (fallback)

Docker container OpenVPN + SOCKS5. Utilisé uniquement si le homelab est down ou
pour le coordinateur Oracle en secours.

YouTube blocks datacenter IPs. The VPN provides a clean exit IP. Only YouTube
requests are routed through the proxy: `yt-dlp --proxy socks5://127.0.0.1:1080`.

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
  "network": {
    "connectivity": "outgoing_only",
    "ip_reputation": "residential"  // "residential" | "vpn" | "datacenter"
  }
}
```

**`ip_reputation`** is a generic, task-agnostic signal. It tells the coordinator
whether outbound requests from this worker are likely to be blocked by anti-bot
systems (YouTube, Cloudflare, etc.).

| Value | Source IP | YouTube | Web scraping | Use case |
|-------|-----------|---------|-------------|----------|
| `residential` | Homelab VPN | ✅ Passe | ✅ Passe | Mode B (worker DL) |
| `vpn` | CyberGhost | ⚠️ Variable | ⚠️ Variable | Fallback |
| `datacenter` | Modal, Kaggle | ❌ Bloqué | ❌ Bloqué | Mode A (pré-DL) |

This replaces the earlier task-specific `can_download_youtube` — it applies to
any future task type (scraping, API calls, dataset downloads).
