# Roadmap Scrapower

> Agrégateur de calcul distribué — friction zéro, browser + Kaggle + Modal + HF Spaces.

---

## ✅ v0.4 — Fiabilisation & Mode B (JUIN 2026)

- [x] **Mode B HTTP pull/submit** — protocole principal, plus de timeout WebSocket
- [x] **Notebook Kaggle Mode B** — pull HTTP, idle 300s auto-stop
- [x] **Harvester Kaggle** — 3 comptes round-robin, auto-start/stop, backoff 429
- [x] **Transcription Whisper** — flux complet YouTube → audio → blob → worker
- [x] **VPN CyberGhost** — container OpenVPN + SOCKS5, yt-dlp via proxy
- [x] **Deno JS runtime** — extraction YouTube fonctionnelle
- [x] **Téléchargement audio natif** — `-f bestaudio/best`, zéro ffmpeg
- [x] **Blob ref_count** — incrémenté dans create/complete, GC respecte ref_count
- [x] **Cleanup TTL** — tâches >24h supprimées, blobs libérés
- [x] **`run_prepare()`** — helper générique pour le cycle PENDING→QUEUED
- [x] **DB nettoyée** — 324 vieilles tâches supprimées
- [x] **`ws_assign_enabled`** — toggle configurable, plus d'env var magique
- [x] **Isolation client corrigée** — `_check_owner` strict
- [x] **Auth worker token** — vérifié contre `SCRAPOWER_API_KEY`
- [x] **Réputation workers** — score basé sur challenges
- [x] **Sécurité** — audit 25 vulnérabilités, 22 corrigées
- [x] **Cookies YouTube** — export fenêtre privée, endpoint `POST /transcribe/update-cookies`
- [x] **Flux universel Mode B→Mode A** — worker DL direct sinon fallback coordonnateur
- [x] **`exit_code` propagation** — worker→submit→coordinator, exit_code=2 déclenche fallback

---

## 🔜 v0.5 — Harvester unifié & Modal (EN COURS)

### Harvester unifié
- [ ] **`WorkerProvider` ABC** — interface commune pour tous les providers éphémères
- [ ] **`EphemeralHarvester`** — boucle générique : quota check → launch → cleanup
- [ ] **Quota en pourcentage** — `remaining_pct()` comparable entre toutes les sources
- [ ] **Tri par capacité restante** — le compte le moins entamé en priorité, sans favoritisme plateforme
- [ ] **KaggleHarvester → `WorkerProvider`** — décorer l'existant, ne pas casser

### Modal
- [ ] **ModalHarvester** — Sandbox.create() avec GPU T4, idle_timeout, auto-cleanup
- [ ] **`deploy/modal/worker.py`** — script Mode B exécuté dans le Sandbox
- [ ] **Auth Modal** — `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` dans `.env`
- [ ] **`Dockerfile`** — ajouter package `modal`

### Observabilité
- [ ] **`/stats` enrichi** — statut Modal (quota %, sandbox actifs, GPU utilisés)

---

## 🔮 v0.6 — Optimisations & nouveaux usages

### Matching GPU intelligent
- [ ] **`gpu_model` / `gpu_vram_mb`** dans les capabilities worker
- [ ] **`gpu_min_vram_mb`** dans la définition de tâche
- [ ] **Matching GPU** — T4 pour turbo, L40S+ pour large-v3, T4 minimum pour LLM 7B
- [ ] **`pick_gpu()`** — le GPU le moins cher qui satisfait les besoins

### Workers
- [ ] **`network.can_download_youtube`** dans les capabilities — éviter les fallbacks inutiles
- [ ] **`cost_priority`** dans les capabilities — pour futur matching avancé

### Nouveaux runtimes
- [ ] **LLM Inference** — llama.cpp/WebLLM sur workers GPU
- [ ] **Web scraping distribué** — tâches de scraping parallélisées
- [ ] **Mode FaaS** — endpoint `/faas/{func_hash}` exécute WASM et renvoie réponse
- [ ] **Modal Modèle B** — invoke direct `transcribe.remote()` sans polling

### Qualité & DX
- [ ] **OAuth YouTube** — tokens refresh automatiques (si yt-dlp rouvre cette voie)
- [ ] **Chunking audio** — découpage des fichiers longs en segments pour parallélisation
- [ ] **Sauvegarde transcripts** — export automatique (fichier, Google Docs, etc.)
- [ ] **Vérification challenge améliorée** — taux adaptatif, blacklist auto
- [ ] **Meilleur modèle Whisper** — large-v3-turbo ou équivalent pour qualité supérieure
- [ ] **SDK Python** — `pip install scrapower`, soumission de tâches en 3 lignes
- [ ] **Observabilité** — Prometheus, logs JSON, alertes

---

## 💭 Horizon (v1.0)

- [ ] Multi-tenant — isolation client_id, quotas, priorités
- [ ] Fédération de coordinateurs
- [ ] Compute-to-earn mobile
- [ ] Intégration IPFS — stockage décentralisé des blobs
- [ ] Mode VPS simulé sur Oracle ARM
- [ ] Autres providers : Google Colab, AWS Lambda, GitHub Actions (ToS permitting)

> **Note :** Golem Network et token ERC-20 retirés. Friction zéro > tokenomie.
