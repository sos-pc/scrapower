# Scrapower — Architecture

## Overview

Scrapower is a distributed computing aggregator that dispatches tasks (WASM or Python) to ephemeral workers (Kaggle notebooks, browser tabs, HuggingFace Spaces).

```
Client ──POST /transcribe──→ Coordinator (Oracle) ──pull──→ Worker (Kaggle T4 GPU)
                                  │
                                  ├─ yt-dlp ──socks5──→ VPN (CyberGhost)
                                  ├─ Blob store (SHA-256 content-addressed)
                                  └─ SQLite (tasks, blobs, sessions)
```

## Protocol: Mode B (HTTP pull/submit) — PRIMARY

Workers poll for tasks via HTTP. No persistent connection needed.

```
Worker                              Coordinator
  │                                     │
  │── POST /worker/pull ──────────────→│  capabilities + worker_id
  │←── {task, assignment_token} ───────│  atomic assign from QUEUED pool
  │                                     │
  │   [execute 2-15 min]                │
  │                                     │
  │── PUT /blobs?token=... ───────────→│  upload output
  │── POST /worker/submit ────────────→│  {task_id, token, output_hash}
  │←── {accepted: true} ───────────────│  task → COMPLETED
```

Mode A (WebSocket push) is retained for browser workers. Toggle via `SCRAPOWER_WS_ASSIGN_ENABLED`.

## Task Lifecycle

```
PENDING → DOWNLOADING → QUEUED → ASSIGNED → COMPLETED
                ↓                      ↓
              FAILED                TIMEOUT → QUEUED (retry)
                                          ↓
                                       FAILED (max retries)
```

- **PENDING**: Task created, input not yet ready (e.g. audio downloading)
- **DOWNLOADING**: Coordinator is preparing the input (yt-dlp, etc.)
- **QUEUED**: Ready for worker dispatch
- **ASSIGNED**: Pulled by a worker, token-protected
- **COMPLETED**: Worker submitted result (trust-based, not verified)
- **FAILED**: Prep failed or max retries exhausted

## Transcription Flow

```
1. POST /transcribe {url, model}
2. Task created: PENDING
3. Background: yt-dlp via VPN → audio blob
4. Input config built → second blob
5. Task → QUEUED
6. Kaggle harvester detects queued task → starts kernel
7. Worker pulls task (POST /worker/pull)
8. Worker downloads: executable blob (whisper_runner.py) + input blob (config)
9. Worker executes: faster-whisper on GPU
10. Worker uploads output blob
11. Worker submits (POST /worker/submit)
12. Task → COMPLETED
13. Client polls GET /results/{task_id} → transcript
```

## VPN (CyberGhost)

A dedicated Docker container runs OpenVPN + SOCKS5 proxy:

```
┌─────────────────────────────────────────┐
│  ORACLE SERVER                          │
│                                         │
│  coordinator ──socks5──→ vpn:1080       │
│  (all other    (127.0.0.1)  │           │
│   traffic                  │            │
│   direct)                  ▼            │
│                    ┌──────────────┐      │
│                    │ VPN container │      │
│                    │ OpenVPN       │      │
│                    │ Dante SOCKS5  │──────→ CyberGhost → YouTube
│                    └──────────────┘      │
└─────────────────────────────────────────┘
```

YouTube blocks datacenter IPs. The VPN gives us a clean exit IP. yt-dlp routes only YouTube requests through the proxy: `yt-dlp --proxy socks5://127.0.0.1:1080`.

Cookies are required. Set `SCRAPOWER_YT_COOKIES_HASH` to a blob containing Netscape-format YouTube cookies exported from a browser.

## Blob Store

Content-addressed by SHA-256. Immutable. Reference-counted.

```
PUT /blobs  →  hash = SHA-256(data)
GET /blobs/{hash} → data
```

Ref_count is incremented when a task references a blob, decremented on cleanup. GC only deletes blobs with ref_count=0.

## Scheduler

- **Mode B (HTTP pull)**: Workers pull tasks. The pull handler does atomic assign.
- **Mode A (WS push)**: Scheduler pushes tasks to connected WS workers.
- **requeue_stale()**: ASSIGNED tasks past their deadline are requeued.
- **cleanup_expired()**: Old COMPLETED/FAILED tasks are deleted, blob refs released.

## Harvester (Kaggle)

- Tick every 15s.
- Detects queued GPU tasks (`gpu_required=True`).
- Pushes notebook to Kaggle account (round-robin).
- Cooldown: 60s minimum between pushes per account.
- Exponential backoff on 429 errors.
- Kernel kills itself after 300s idle (no tasks).
