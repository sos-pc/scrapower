# Roadmap Scrapower

> Agrégateur de calcul distribué — friction zéro, browser + Kaggle + Modal + HF Spaces.

---

## ✅ v0.5 — Harvester unifié & Modal (JUIN 2026)

### Harvester unifié
- [x] **`WorkerProvider` ABC** — interface commune pour tous les providers éphémères
- [x] **`EphemeralHarvester`** — boucle génétique : quota check → launch → cleanup
- [x] **Quota en pourcentage** — `remaining_pct()` comparable entre toutes les sources
- [x] **Tri par capacité restante** — le compte le moins entamé en priorité
- [x] **KaggleHarvester → `WorkerProvider`** — décoré sans casser l'existant

### Modal
- [x] **ModalHarvester** — Sandbox.create() GPU T4 + CUDA, idle_timeout=2min, round-robin
- [x] **`deploy/modal/worker.py`** — Mode B avec exit_code, auto-détection GPU
- [x] **Auth** — `MODAL_ACCOUNTS` + `KAGGLE_ACCOUNTS` multi-comptes
- [x] **Workers actifs** — 3 Kaggle + 2 Modal = 5 comptes GPU

### Transcription
- [x] **`POST /transcribe/batch`** — playlist → N tâches via yt-dlp --flat-playlist
- [x] **Script collecte** — `scripts/batch_collect.py` poll + sauvegarde fichiers

### Déjà fait en v0.4
- [x] Mode B HTTP pull/submit, flux universel Mode B→Mode A, exit_code propagation
- [x] Transcription Whisper turbo, téléchargement natif (zéro ffmpeg)
- [x] VPN CyberGhost, cookies YouTube (fenêtre privée)
- [x] Isolation client, auth worker, sécurité

---

## 🔜 v0.6 — Homelab VPN + fiabilisation

### Homelab VPN exit node (priorité P0)
- [ ] **WireGuard server sur homelab** — IP résidentielle pour tous les workers
- [ ] **Config auto** — chaque worker reçoit la config WireGuard
- [ ] **Fallback CyberGhost** — si homelab down, bascule sur VPN commercial
- [ ] **DuckDNS** — IP dynamique → nom de domaine stable

**Impact** : Modal, Kaggle, et tout futur worker peuvent télécharger depuis YouTube sans blocage. Plus besoin de fallback coordinateur.

### Corrections phase de test
- [ ] **`network.ip_reputation`** dans les capabilities — "residential" | "datacenter" | "vpn"
- [ ] **Coordinateur décide au prepare** — si tous les workers sont `datacenter` → pré-DL audio
- [ ] **`last_error` visible** — `GET /tasks/{id}` expose le dernier message d'erreur
- [ ] **`COOLDOWN_SEC`** 120→60s pour Modal
- [ ] **Max concurrent workers** — le harvester vérifie le nombre actif avant de lancer

### Monitoring
- [ ] **`/stats` enrichi** — quota par provider, workers actifs, statut VPN homelab

---

## 🔮 v0.7 — Optimisations & nouveaux usages

### Matching GPU intelligent
- [ ] **`gpu_model` / `gpu_vram_mb`** dans les capabilities
- [ ] **`gpu_min_vram_mb`** dans la définition de tâche
- [ ] **Matching** — T4 pour turbo, L40S+ pour large-v3/LLM

### Nouveaux runtimes
- [ ] **LLM Inference** — llama.cpp/WebLLM sur workers GPU
- [ ] **Web scraping distribué** — tâches parallélisées
- [ ] **Mode FaaS** — endpoint `/faas/{func_hash}`

### Qualité
- [ ] **Meilleur modèle Whisper** — large-v3-turbo
- [ ] **Chunking audio** — découpage longues vidéos
- [ ] **Sauvegarde transcripts** — export Google Docs, etc.

### Volume Modal partagé
- [ ] **Audio sur Volume** — écriture coordinateur, lecture concurrente workers
- [ ] **Modèles LLM sur Volume** — partage de poids entre workers

---

## 💭 Horizon (v1.0)

- [ ] Multi-tenant — isolation client_id, quotas
- [ ] Fédération de coordinateurs
- [ ] SDK Python — `pip install scrapower`
- [ ] Observabilité Prometheus, alertes
- [ ] Autres providers : Google Colab, AWS Lambda
