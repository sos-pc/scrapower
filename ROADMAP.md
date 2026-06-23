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

## ✅ v0.6 — Homelab VPN + fiabilisation (JUIN 2026)

### Homelab VPN exit node (priorité P0)
- [x] **WireGuard server sur homelab** — IP résidentielle pour tous les workers
- [x] **Tunnel SOCKS5 Oracle** — Dante proxy port 1081, fix iptables mangle SYN-ACK, URL publique
- [x] **Fallback CyberGhost** — conservé comme secours sur port 1080
- [x] **Port UDP 443** — réutilise le port déjà forwardé sur la box (pas de nouveau port)

**Impact** : Modal, Kaggle, et tout futur worker peuvent télécharger depuis YouTube sans blocage. Plus besoin de fallback coordinateur.

### Corrections phase de test (P0 — en cours)
- [x] **iptables mangle fix** — `--sport 1081 -j MARK --set-mark 0xca6c` (SYN-ACK bloqués)
- [x] **`deadline_ms` dans INSERT** — corrigé, les tâches ont bien 900000ms maintenant
- [x] **Suivi worker heartbeat** — `requeue_stale` atomique 90s, touch `assigned_at` sur tous les signaux (pull/heartbeat/submit/WS), logs Mode A persistés, heartbeat Kaggle
- [x] **Anti-pattern coordinator DL** — documenté comme TEMPORARY BANDAGE dans le code. Cible : workers DL eux-mêmes via WireGuard
- [x] **`cleanup_stale` cross-compte** — Modal : `_sandbox_tokens` dict, itération tous tokens. Kaggle : `_kernel_refs` tracking local. Cleanup pour TOUS les providers (pas juste candidates)
- [ ] **`network.ip_reputation`** dans les capabilities — tag informatif "residential" | "datacenter" | "vpn" (non bloquant)
- [ ] **Coordinateur décide au prepare** — si tous les workers sont `datacenter` → pré-DL audio (élimine gaspillage fallback)

### Corrections P1 (prochaine session)
- [x] **`COOLDOWN_SEC`** 120→60s pour Modal, log debug cooldown/max concurrent ✅
- [x] **Kaggle cooldown** — ajouté (manquait, `_last_start` jamais utilisé) ✅
- [x] **`last_error` + logs** — colonne `error` dans la DB, `has_logs` + `logs_url` dans `GET /tasks/{id}` ✅
- [x] **Rate limit pull** — dual-mode: auth (`worker_id` 30/min) vs anon (IP 6/min survival) ✅
- [ ] **Debug Kaggle inactif** — vérifier pourquoi les kernels Kaggle ne pull pas pendant les tests
- [ ] **Fallback automatique** — quand worker retourne exit_code=2, trigger_fallback sans attendre le cycle complet

### Corrections P2 (v0.7+)
- [ ] **`remaining_pct()` Modal** — tracker le budget $30 réel (décrémenter au lancement de sandbox)
- [ ] **Priorité harvester** — si Modal saturé (sandboxes zombies), basculer automatiquement sur Kaggle
- [ ] **Tâche timeout → retry intelligent** — ne pas réassigner au même type de worker si échec réseau
- [ ] **Rotation + rétention logs** — `data/logs/{id}.log` : garder les 1000 dernières lignes par tâche, supprimer après 7j dans `cleanup_expired`

### Monitoring
- [x] **`/stats` enrichi** — quota Kaggle par compte, workers actifs
- [x] **Logs workers → coordinateur** — `_save_worker_logs()` + `GET /tasks/{id}/logs`
- [ ] **Statut VPN homelab** dans `/stats`

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
