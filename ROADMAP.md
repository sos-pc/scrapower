# Roadmap Scrapower

> Agrégateur de calcul distribué — friction zéro, browser + Kaggle + HF Spaces.

---

## ✅ v0.4 — Fiabilisation & Mode B (JUIN 2026)

- [x] **Mode B HTTP pull/submit** — protocole principal, plus de timeout WebSocket
- [x] **Notebook Kaggle Mode B** — pull HTTP, idle 300s auto-stop
- [x] **Harvester Kaggle** — 2 comptes round-robin, auto-start/stop, backoff 429
- [x] **Transcription Whisper** — flux complet YouTube → audio → blob → worker
- [x] **VPN CyberGhost** — container OpenVPN + SOCKS5, yt-dlp via proxy
- [x] **Deno JS runtime** — extraction YouTube fonctionnelle
- [x] **Téléchargement audio async** — PENDING→DOWNLOADING→QUEUED, non-bloquant
- [x] **Blob ref_count** — incrémenté dans create/complete, GC respecte ref_count
- [x] **Cleanup TTL** — tâches >24h supprimées, blobs libérés
- [x] **`run_prepare()`** — helper générique pour le cycle PENDING→QUEUED
- [x] **DB nettoyée** — 324 vieilles tâches supprimées
- [x] **`ws_assign_enabled`** — toggle configurable, plus d'env var magique
- [x] **Isolation client corrigée** — `_check_owner` strict
- [x] **Auth worker token** — vérifié contre `SCRAPOWER_API_KEY`
- [x] **Réputation workers** — score basé sur challenges
- [x] **Sécurité** — audit 25 vulnérabilités, 22 corrigées

---

## 🔜 v0.5 — Scale & nouveaux runtimes

- [ ] **LLM Inference** — llama.cpp/WebLLM sur workers GPU
- [ ] **Web scraping distribué** — tâches de scraping parallélisées
- [ ] **Mode FaaS** — endpoint `/faas/{func_hash}` exécute WASM et renvoie réponse
- [ ] **Observabilité** — Prometheus, logs JSON, alertes
- [ ] **Multi-tenant** — isolation client_id, quotas, priorités
- [ ] **Vérification challenge améliorée** — taux adaptatif, blacklist auto
- [ ] **SDK Python** — `pip install scrapower`, soumission de tâches en 3 lignes

---

## 💭 Horizon (v1.0)

- [ ] Fédération de coordinateurs
- [ ] Compute-to-earn mobile
- [ ] Intégration IPFS — stockage décentralisé des blobs
- [ ] Mode VPS simulé sur Oracle ARM

> **Note :** Golem Network et token ERC-20 retirés. Friction zéro > tokenomie.
