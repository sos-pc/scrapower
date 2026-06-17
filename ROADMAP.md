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
- [x] **Vérification** — mode "challenge" (10% double-exécution), "trust" (défaut)

---

## ✅ Phase 3 — P2P (v0.3) — FAIT

- [x] **WebRTC Data Channels** — transfert direct worker↔worker
- [x] **Kademlia DHT** — découverte et routage dynamique des workers
- [x] **GossipSub** — broadcast P2P des annonces de blobs

---

## 🔜 Phase 4 — Capacités navigateur (v0.4)

- [ ] **SIMD WASM** — calcul vectoriel CPU
- [ ] **Canvas/OffscreenCanvas** — traitement d'images distribué
- [ ] **WebCodecs** — transcodage vidéo/audio
- [ ] **Web Crypto** — signatures Ed25519, preuves d'exécution
- [ ] **Multi-tenant** — isolation client_id, quotas, priorités

---

## 🔜 Phase 5 — Scale (v0.5)

- [ ] **Google Cloud Run Harvester** — workers 4 GB RAM, 60 min
- [ ] **Hugging Face Spaces** — workers GPU gratuits (16 GB, T4)
- [ ] **Réputation workers** — score, blacklist, priorité
- [ ] **Observabilité** — Prometheus, logs JSON, alertes
- [ ] **Vérification ZK** — preuves à divulgation nulle

---

## 💭 Horizon (v1.0+)

- [ ] Marketplace — crédits de calcul, offre/demande
- [ ] Fédération de coordinateurs
- [ ] SDK Python `pip install scrapower`
- [ ] Compute-to-earn mobile
- [ ] Intégration IPFS — stockage décentralisé des blobs
- [ ] Token crypto — ERC-20 pour crédits

---

## 📊 Métriques

| Phase | Workers | Tâches/jour | Latence | Uptime |
|-------|---------|-------------|---------|--------|
| v0.1  | 1-5     | 100         | < 5s    | 95%    |
| v0.2  | 5-15    | 1 000       | < 3s    | 99%    |
| v0.3  | 10-50   | 5 000       | < 1s    | 99.5%  |
| v0.4  | 50-500  | 50 000      | < 500ms | 99.9%  |
| v1.0  | 500+    | 100 000+    | < 100ms | 99.99% |
