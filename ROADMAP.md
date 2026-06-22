# Roadmap Scrapower

> Agrégateur de calcul distribué — friction zéro, browser + Kaggle + Modal + HF Spaces.

---

## ✅ v0.5 — Harvester unifié & Modal (JUIN 2026)

### Harvester unifié
- [x] **`WorkerProvider` ABC** — interface commune pour tous les providers éphémères
- [x] **`EphemeralHarvester`** — boucle générique : quota check → launch → cleanup
- [x] **Quota en pourcentage** — `remaining_pct()` comparable entre toutes les sources
- [x] **Tri par capacité restante** — le compte le moins entamé en priorité, sans favoritisme plateforme
- [x] **KaggleHarvester → `WorkerProvider`** — décoré sans casser l'existant

### Modal
- [x] **ModalHarvester** — Sandbox.create() GPU T4 + CUDA, idle_timeout=2min, round-robin
- [x] **`deploy/modal/worker.py`** — script Mode B avec exit_code, auto-détection GPU
- [x] **Auth Modal** — `MODAL_ACCOUNTS` JSON multi-comptes + fallback `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`
- [x] **`Dockerfile`** — package `modal` ajouté
- [x] **Workers actifs** — 3 Kaggle + 2 Modal = 5 comptes GPU

### Déjà fait en v0.4
- [x] Mode B HTTP pull/submit, flux universel Mode B→Mode A, exit_code propagation
- [x] Transcription Whisper turbo, téléchargement audio natif (zéro ffmpeg)
- [x] VPN CyberGhost, cookies YouTube (export fenêtre privée), fallback coordinator
- [x] Isolation client, auth worker, réputation, sécurité (22/25 corrigées)

---

## 🔜 v0.6 — Observabilité & optimisations

### Monitoring
- [ ] **`/stats` enrichi** — statut par provider (quota %, workers actifs, GPU), temps de traitement
- [ ] **Logs structurés** — JSON logs pour debugging worker Modal/Kaggle

### Matching GPU
- [ ] **`gpu_model` / `gpu_vram_mb`** dans les capabilities worker
- [ ] **`gpu_min_vram_mb`** dans la définition de tâche
- [ ] **Matching GPU** — T4 pour turbo, L40S+ pour large-v3/LLM

### Workers
- [ ] **`network.can_download_youtube`** dans les capabilities — éviter les fallbacks inutiles

### Qualité transcription
- [ ] **Meilleur modèle Whisper** — large-v3-turbo ou équivalent pour qualité supérieure
- [ ] **Chunking audio** — découpage des fichiers longs en segments pour parallélisation
- [ ] **Sauvegarde transcripts** — export automatique (fichier, Google Docs, etc.)

---

## 🔮 v0.7 — Nouveaux runtimes

- [ ] **LLM Inference** — llama.cpp/WebLLM sur workers GPU (Modal H100, Kaggle T4)
- [ ] **Web scraping distribué** — tâches de scraping parallélisées
- [ ] **Mode FaaS** — endpoint `/faas/{func_hash}` exécute WASM et renvoie réponse

---

## 💭 Horizon (v1.0)

- [ ] Multi-tenant — isolation client_id, quotas, priorités
- [ ] Fédération de coordinateurs
- [ ] SDK Python — `pip install scrapower`
- [ ] Observabilité Prometheus, alertes
- [ ] Intégration IPFS — stockage décentralisé des blobs
- [ ] Autres providers : Google Colab, AWS Lambda

> **Note :** Golem Network et token ERC-20 retirés. Friction zéro > tokenomie.
