# Roadmap Scrapower v2

> Agrégateur de calcul distribué gratuit — navigateur natif, friction zéro, P2P-ready.

---

## ✅ Phase 1 — Fondations (v0.1) — **FAIT**

- [x] Coordinateur central (FastAPI + SQLite + WebSocket)
- [x] 3 types de workers : navigateur (WASM+WebGPU), natif Python, embedded
- [x] Protocole Worker v2.1, load balancing intra-tick
- [x] WebGPU fonctionnel (matmul 256×256 en ~100ms)
- [x] Déploiement Oracle Cloud + CI/CD GitHub Actions
- [x] 44 tests, lint 0, mypy clean

---

## 🔜 Phase 2 — Plateforme (v0.2)

**Rendre le projet utilisable par des tiers.**

- [ ] **SDK Python `scrapower`** — `pip install scrapower` → `scrapower.submit(wasm, input)`
- [ ] **CLI** — `scrapower serve/submit/status/worker`
- [ ] **Dashboard** — workers, tâches, santé en temps réel
- [ ] **Worker keepalive** — reconnexion automatique + backoff
- [ ] **Cache WASM (IndexedDB)** — un module jamais re-téléchargé
- [ ] **Service Worker** — le worker survit en arrière-plan

---

## 📋 Phase 3 — P2P (v0.3) ← pivot

**Introduire libp2p pour décentraliser le réseau de workers.**

- [ ] **libp2p + WebRTC** — communication directe worker↔worker
  - Le coordinateur actuel devient bootstrap node + signalling server
  - Transfert de blobs en P2P (plus de bottleneck coordinateur)
- [ ] **Kademlia DHT** — découverte et routage dynamique des workers
  - `task.hash → DHT.lookup → N workers responsables`
  - Tolérance de panne : un worker part, le DHT reroute
  - Scale horizontal : chaque worker ajoute de la capacité de routage
- [ ] **GossipSub** — broadcast de tâches disponibles entre workers
- [ ] **Stockage distribué** — IndexedDB + DHT pour cache de blobs P2P
  - Un worker peut servir un blob à un autre sans coordinateur

---

## 🚀 Phase 4 — Capacités navigateur (v0.4)

**Exploiter TOUT ce qu'un navigateur peut fournir.**

- [ ] **SIMD WASM** — calcul vectoriel CPU (physique, finance, cracking)
- [ ] **Canvas/OffscreenCanvas** — traitement d'images distribué
- [ ] **WebCodecs** — transcodage vidéo/audio distribué
- [ ] **Web Audio API** — FFT, traitement de signal
- [ ] **Web Crypto** — signatures Ed25519, preuves d'exécution
- [ ] **File System Access API** — accès aux fichiers locaux (si autorisé)

---

## ⚡ Phase 5 — Intelligence (v0.5)

**Optimisation automatique et workloads avancés.**

- [ ] **Scheduler IA** — prédiction durée, matching optimal, work stealing
- [ ] **MoE LLM distribué** — Mixtral 8×7B sur 8 workers GPU navigateurs
  - Quantification 4-bit → 4 Go par expert → tient dans WASM
  - Batch processing, pas temps réel
- [ ] **CDN éclaté** — les workers servent des fichiers statiques
- [ ] **Observabilité** — Prometheus, logs JSON, alertes
- [ ] **Vérification ZK** — preuves à divulgation nulle via Web Crypto

---

## 🌐 Phase 6 — Scale (v1.0)

- [ ] **Harvester** — Colab, GitHub Actions, Oracle (workers cloud gratuits)
- [ ] **Python runtime** — fonctions Python sandboxées
- [ ] **Réputation workers** — score, blacklist, priorité
- [ ] **Multi-tenant** — isolation client_id, quotas, priorités

---

## 💭 Horizon (v2.0+)

- [ ] Marketplace — crédits de calcul, offre/demande
- [ ] Fédération de coordinateurs
- [ ] WASI preview 2 — sandboxing standardisé
- [ ] Compute-to-earn mobile — app iOS/Android
- [ ] Intégration IPFS — stockage décentralisé des blobs
- [ ] Token crypto — ERC-20 pour crédits

---

## 📊 Métriques

| Phase | Workers | Tâches/jour | Latence | Uptime |
|-------|---------|-------------|---------|--------|
| v0.1  | 5-15    | 100         | < 2s    | 99%    |
| v0.2  | 20-50   | 1 000       | < 1s    | 99.5%  |
| v0.3  | 50-500  | 10 000      | < 500ms | 99.9%  |
| v0.4  | 500-5K  | 100 000     | < 200ms | 99.95% |
| v1.0  | 5K+     | 1M+         | < 100ms | 99.99% |

---

## 🔑 Dette technique à résorber

- [ ] `main.py` : extraire HTML, découper lifespan
- [ ] `index.ts` : sandbox dans un fichier séparé
- [ ] `client.py` : séparer download/execute/upload
- [ ] Docstrings sur toutes les fonctions publiques
- [ ] Coverage ≥ 80%
