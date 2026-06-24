---
title: Scrapower Worker
emoji: ⚡
colorFrom: blue
colorTo: purple
sdk: docker
suggested_hardware: cpu-basic
pinned: false
---

# Scrapower Worker (HF Spaces)

CPU worker for the Scrapower distributed compute platform.
Executes WASM and Python tasks in sandboxed subprocesses.

**Mode B (HTTP pull/submit)** — polls the coordinator for tasks.
Auto-stops after idle timeout. Managed by HuggingFaceHarvester.

## Setup

This Space is created and managed automatically by the Scrapower
HuggingFaceHarvester. Manual setup is not required.

Required secrets (set via Harvester):
- `COORDINATOR_URL` — URL of the Scrapower coordinator
- `SCRAPOWER_API_KEY` — API key for authentication
