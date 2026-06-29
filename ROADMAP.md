# Roadmap Scrapower

> Agrégateur de calcul distribué — friction zéro, Kaggle + Modal + HF Spaces.

---

## ✅ v0.5 — Harvester unifié & Modal (JUIN 2026)

- [x] `WorkerProvider` ABC + `EphemeralHarvester` — quota check → launch → cleanup
- [x] `ModalHarvester` — Sandbox T4 GPU, idle_timeout, round-robin multi-comptes
- [x] `HuggingFaceHarvester` — déploiement auto, wake HTTP, CPU-only
- [x] `KaggleHarvester` — kernels GPU T4, cooldown, cleanup cross-compte
- [x] Mode B HTTP pull/submit — tous les workers
- [x] `POST /transcribe` + `/transcribe/batch` — playlist → N tâches
- [x] Capability matching — GPU → Kaggle/Modal, CPU → HF
- [x] Auth multi-comptes — `MODAL_ACCOUNTS` + `KAGGLE_ACCOUNTS`

---

## ✅ v0.6 — Homelab VPN + fiabilisation (JUIN 2026)

- [x] WireGuard homelab → SOCKS5 Oracle → workers téléchargent via IP résidentielle
- [x] Heartbeat Mode B — urllib thread, `task_valid` fix, `current_assignment_token`
- [x] Mode A supprimé — 9 fichiers, `_maintenance_loop` unifié (15s)
- [x] Fallback coordinator supprimé — `_download_audio`, `prepare_audio_fallback`
- [x] Worker deadlock fix — `_read_stderr` thread retiré (Modal + HF)
- [x] `requeue_stale` atomique 90s, `cleanup_expired` 5min
- [x] Rate limit pull — auth 30/min, anon 6/min
- [x] `/stats` enrichi — quota Kaggle par compte, `mode_b_workers_active`

---

## ✅ v0.7 — AccountRegistry & distribution parallèle (JUIN 2026)

- [x] `AccountRegistry` — liste plate de comptes, quota, GPU capabilities
- [x] Décision par compte — `candidates_for_task()`, tri par quota décroissant
- [x] Lancement parallèle — `asyncio.gather` sur N comptes
- [x] `remaining_pct()` par compte — plus d'agrégat provider
- [x] Modal billing API — `modal.billing` par compte
- [x] Kaggle quota API — `kaggle quota --csv` par compte
- [x] `/stats` unifié — une liste `accounts`, plus de doublons
- [x] Heartbeat async — `aiohttp` remplace `urllib` thread (fix P8)
- [x] HF Spaces unifié comme compte avec `lifecycle: persistent`

### Queue adaptative (CPU/GPU mixing)
- [x] **`gpu_required` → sémaphore** — tâches CPU traversent même si queue GPU pleine ✅ (v0.7)
- [x] **FIFO par compte** — `AccountRegistry.candidates_for_task()` trie par quota ✅ (v0.7)
- [x] **Lancement parallèle** — `asyncio.gather` sur plusieurs comptes ✅ (v0.7)
- [x] **`remaining_pct()` → `remaining_credits_per_account()`** — granularité compte ✅ (v0.7)
- [x] **Modal billing API** — `modal.billing` ✅ (v0.7)
- [x] **Heartbeat async** — remplace le thread urllib (P8) ✅ (v0.7)
- [ ] **Kaggle GPU quota API** — `kaggle.api.kaggle_api_extended` pour heures restantes par compte
- [ ] **Provider API-first** — privilégier les APIs natives (Modal billing, Kaggle quota) sur nos estimations
- [ ] **`/stats` unifié** — une table unique tous comptes confondus ✅ (v0.7)

### Logs workers → coordinator (streaming)
- [x] **Logs temps réel** — stderr streamé via heartbeat async + `asyncio.create_subprocess_exec` ✅ (`332a65d`)
- [ ] **Rétention TTL** — logs supprimés après 7j dans `cleanup_expired`
- [ ] **Ring buffer par tâche** — 1000 dernières lignes max
- [ ] **`GET /tasks/{id}/logs?tail=100&follow=true`** — SSE tail + follow

### CLI / UX simplifiée
- [ ] **`scrapower` CLI** — `scrapower submit`, `scrapower status`, `scrapower logs`
- [ ] **`scrapower serve`** — unifier `docker compose up` + `scrapower serve`
- [ ] **Dashboard web minimal** — `/` : queue, workers actifs, dernières tâches
- [ ] **Webhook callback** — `POST https://mon-app.com/hook` quand tâche terminée
- [ ] **API key par client** — isolation, quotas par clé

### Task chunking
- [ ] **Découpage audio long** — >30min → N segments → N tâches parallèles
- [ ] **Merge results** — rassembler les segments dans l'ordre
- [ ] **Configurable** — `min_chunk_sec=300`, `max_chunks=20`
- [ ] **Économie** — chunks GPU en parallèle sur comptes différents

---

## 🔮 v0.8 — Multi-workload & runtimes

### Tâches génériques (pas que transcription)
- [ ] **Endpoint `/tasks` unifié** — `task_type: "whisper" | "python" | "wasm" | "translate"`
- [ ] **`POST /translate`** — sous-titres pour PotPlayer/VLC (entrée: SRT/VTT, sortie: SRT traduit)
- [ ] **`POST /infer`** — LLM inference distribué (voir v0.9)
- [ ] **`POST /faas/{func_hash}`** — exécution Python/WASM arbitraire
- [ ] **Runner registry** — `whisper_runner.py`, `translate_runner.py`, `llm_runner.py`

### Résultat caching
- [ ] **Cache par URL + model** — même vidéo + même modèle → résultat caché
- [ ] **TTL configurable** — 24h par défaut, invalidable
- [ ] **`ETag` / `If-None-Match`** — cache HTTP standard

### Dead letter queue
- [ ] **Max retries par tâche** — après N échecs → DLQ au lieu de boucler
- [ ] **Inspection DLQ** — `GET /tasks?status=failed`
- [ ] **Retry manuel** — `POST /tasks/{id}/retry`

---

## 🔮 v0.9 — LLM distribué

- [ ] **`llm_runner.py`** — llama.cpp ou vllm, remplace `whisper_runner.py`
- [ ] **`POST /infer`** — `{"model": "mistral-7b", "prompt": "...", "max_tokens": 500}`
- [ ] **Modèles sur Volume Modal** — poids partagés entre workers (pas de re-download)
- [ ] **Streaming tokens** — SSE token par token vers le client
- [ ] **Matching GPU** — T4 pour 7B, L40S+ pour 13B/34B
- [ ] **Quantization** — GGUF Q4_K_M pour tenir en VRAM T4 (16GB)

---

## 💭 Horizon (v1.0)

- [ ] **Multi-tenant** — isolation client_id, quotas, billing
- [ ] **Fédération** — plusieurs coordinateurs (Oracle + homelab + autres)
- [ ] **SDK Python** — `pip install scrapower`, `from scrapower import Client`
- [ ] **Observabilité** — Prometheus metrics, Grafana dashboard
- [ ] **Autres providers** — Colab, Lambda, RunPod
