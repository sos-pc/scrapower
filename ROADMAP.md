# Roadmap Scrapower v2

> Agrégateur de calcul distribué gratuit — navigateur natif, friction zéro, P2P-ready.

---

## ✅ Phase 1 — Fondations (v0.1) — FAIT

- [x] Coordinateur central (FastAPI + SQLite + WebSocket)
- [x] 4 types de workers : navigateur (WASM+WebGPU), natif Python, embedded, GitHub Actions
- [x] Protocole Worker v2.1, load balancing intra-tick
- [x] WebGPU fonctionnel (matmul 256×256 en ~100ms)
- [x] Déploiement Oracle Cloud + CI/CD GitHub Actions
- [x] Docker multi-stage build + docker-compose
- [x] 44 tests, lint 0, mypy clean

---

## ✅ Phase 2 — Plateforme (v0.2) — FAIT

- [x] **Python runtime (Pyodide)** — navigateurs exécutent du Python natif
- [x] **Worker keepalive** — reconnexion automatique + backoff exponentiel
- [x] **Service Worker** — le worker survit en arrière-plan
- [x] **OAuth GitHub** — connexion visiteur en 1 clic
- [x] **Harvester GitHub Actions** — workers 7 GB RAM, 6h max
- [x] **Dashboard** — endpoint `/stats` avec capacité, workers, throughput
- [x] **Sécurité** — audit complet 25 vulnérabilités, 22 corrigées
- [x] **Auth worker** — vérification du token contre la clé API
- [x] **Vérification challenge** — 10% des tâches double-exécutées, comparaison des résultats
- [x] **Embed widget** — iframe intégrable sur n'importe quel site, consentement opt-in
- [x] **CORS** — middleware ASGI pour appels cross-origin

---

## ✅ Phase 3 — P2P (v0.3) — FAIT

- [x] **WebRTC Data Channels** — transfert direct worker↔worker
- [x] **Kademlia DHT** — découverte et routage dynamique des workers
- [x] **GossipSub** — broadcast P2P des annonces de blobs

---

## 🔜 Phase 4 — Fiabilité & Capacités (v0.4)

- [x] **Réputation workers** — score basé sur challenges matched/mismatched, blacklist automatique
- [x] **Challenge adaptatif** — nouveau worker = 100% challengé, fiable = 1%, suspect = 50%
- [x] **Isolation client** — bypass `anonymous` corrigé, `_check_owner` strict
- [x] **Auth worker** — token vérifié contre `SCRAPOWER_API_KEY`, comparaison constant-time (`hmac.compare_digest`)
- [x] **Correction `total_changes`** — remplacé par `cursor.rowcount` pour la détection de race condition
- [ ] **Dashboard temps réel** — WebSocket push des stats, workers live, challenges
- [ ] **Web Crypto** — signatures Ed25519 côté worker, preuves d'exécution
- [ ] **Multi-tenant** — isolation client_id, quotas, priorités par client
- [ ] **SIMD WASM** — calcul vectoriel CPU
- [ ] **Vanity Hash Finder** — démonstrateur de recherche distribuée (SHA-256 brute force)
- [ ] **Mode `redundant`** — double-exécution à 100% (optionnel, documenté) — standard BOINC/SETI@home

---

## 🔜 Phase 5 — Scale & Compute Unifié (v0.5)

- [ ] **Runtime LLM** — llama.cpp/WebLLM sur workers GPU, modèles GGUF dans blob store
- [ ] **Runtime Docker** — tâches `{"runtime": "docker", "image_hash": "..."}`
- [ ] **Mode FaaS** — endpoint `/faas/{func_hash}` → exécute WASM et renvoie la réponse HTTP
- [ ] **Google Cloud Run Harvester** — workers 4 GB RAM, 60 min
- [x] **HuggingFace Spaces** — worker Docker 16 GB RAM CPU, always-on, zéro coût
- [ ] **Modal** — worker GPU A100 (crédits $30/mois)
- [ ] **Kaggle Notebook** — worker GPU T4 30h/semaine (opt-in manuel)
- [ ] **Observabilité** — Prometheus, logs JSON, alertes
- [ ] **Vérification ZK** — preuves à divulgation nulle (pas de redondance, vérification O(1))
- [ ] **SDK Python** — `pip install scrapower`, soumission de tâches en 3 lignes

---

## 💭 Horizon (v1.0+)

- [ ] Marketplace — crédits de calcul, offre/demande
- [ ] Fédération de coordinateurs
- [ ] Compute-to-earn mobile
- [ ] Intégration IPFS — stockage décentralisé des blobs
- [ ] Mode VPS/K8s simulé — déploiement Docker persistant sur workers dédiés (Oracle ARM)

> **Note :** Golem Network et token ERC-20 retirés de la roadmap.
> Golem = distraction avant v1.0 (VM non-WASM, friction token).
> La valeur de Scrapower est la friction zéro, pas la tokenomie.

---

## 📊 Métriques

| Phase | Workers | Tâches/jour | Latence | Uptime |
|-------|---------|-------------|---------|--------|
| v0.1  | 1-5     | 100         | < 5s    | 95%    |
| v0.2  | 5-15    | 1 000       | < 3s    | 99%    |
| v0.3  | 10-50   | 5 000       | < 1s    | 99.5%  |
| v0.4  | 50-500  | 50 000      | < 500ms | 99.9%  |
| v1.0  | 500+    | 100 000+    | < 100ms | 99.99% |
