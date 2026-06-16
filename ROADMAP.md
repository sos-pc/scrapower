# Roadmap Scrapower

> Agrégateur de puissance de calcul distribuée — gratuit, hétérogène, scalable.

---

## ✅ Phase 1 — Fondations (v0.1) — **FAIT**

**Sortir un MVP fonctionnel, déployé, avec 3 types de workers.**

- [x] Coordinateur central (FastAPI + SQLite + WebSocket)
- [x] Worker navigateur (TypeScript, WebAssembly sandbox, widget)
- [x] Worker natif Python (aiohttp, WebSocket persistant)
- [x] Worker embarqué (fallback intégré au coordinateur)
- [x] Protocole Worker v2.1 (2 modes : WebSocket persistant + HTTP pull)
- [x] WebGPU — multiplication matricielle sur GPU navigateur
- [x] Distribution multi-worker avec load balancing intra-tick
- [x] Sécurité : API key, rate limiting, port isolé (iptables)
- [x] Déploiement Oracle Cloud Always Free + Caddy + Let's Encrypt
- [x] 33 tests unitaires, lint clean
- [x] GitHub, README, ROADMAP

---

## 🔜 Phase 2 — Plateforme utilisable (v0.2)

**Rendre le projet utilisable par des tiers : SDK, CLI, dashboard.**

- [ ] **SDK Python `scrapower`**
  - `pip install scrapower`
  - `scrapower.submit(wasm_bytes, input_bytes)` → résultat
  - Modules WASM pré-compilés inclus (sha256, matmul, monte_carlo, sort)
  - Mode synchrone (bloque jusqu'au résultat) et async
- [ ] **CLI `scrapower`**
  - `scrapower serve` — lancer un coordinateur
  - `scrapower submit --wasm module.wasm --input data.bin` — soumettre
  - `scrapower status <task_id>` — état d'une tâche
  - `scrapower worker` — lancer un worker natif
  - `scrapower dashboard` — page web locale de monitoring
- [ ] **Dashboard web**
  - Page HTML simple embarquée dans le coordinateur
  - Workers connectés (ID, type, GPU, uptime, charge)
  - Tâches (queued, running, done) avec graphe en temps réel
  - Historique des 100 dernières tâches
- [ ] **Worker keepalive & reconnect**
  - Reconnexion automatique navigateur après perte WebSocket
  - Reconnexion avec backoff exponentiel
  - Conservation des tâches en cours (retry sur nouveau worker)
- [ ] **Multi-runtime WASM**
  - Support WASI preview 1 (accès fichier sandboxé)
  - Caching des modules WASM compilés (évite recompilation)
  - Warm-up : pré-instanciation de modules fréquents
- [ ] **CI/CD GitHub Actions**
  - Tests automatiques à chaque push
  - Lint (ruff) automatique
  - Build du worker navigateur
  - Badge coverage, badge tests

---

## 📋 Phase 3 — Scale (v0.3)

**Passer à l'échelle : plus de workers, plus de sources, plus de fiabilité.**

- [ ] **Harvester multi-providers**
  - Google Colab : provisioning auto de notebooks workers gratuits
  - GitHub Actions : workers via workflow runners gratuits
  - Oracle Cloud : workers additionnels sur instances Always Free
  - Rotation automatique (timeout gratuit 1h-6h selon provider)
  - Queue de remplacement (un worker part, un autre arrive)
- [ ] **Python runtime**
  - Exécution de fonctions Python soumises dynamiquement
  - Sandboxing via `RestrictedPython` ou subprocess isolé
  - Pas de compilation WASM nécessaire côté utilisateur
  - Cache de fonctions fréquentes
- [ ] **Vérification par challenge**
  - Mode `challenge` : 2+ workers exécutent la même tâche
  - Comparaison des résultats (hash identique = validé)
  - Détection de workers malveillants ou défaillants
  - Mode `trust` (actuel) pour les tâches bénignes
- [ ] **Réputation workers**
  - Score basé sur : succès/échecs, latence, uptime
  - Priorité aux workers à haut score
  - Blacklist automatique après N échecs consécutifs
  - Affichage du score dans le widget
- [ ] **Multi-tenant**
  - Isolation par `client_id` avec quotas configurables
  - Limite de tâches simultanées par client
  - Priorités inter-client (premium > standard)
- [ ] **Persistance avancée**
  - Backup automatique de la DB SQLite
  - Migration vers PostgreSQL pour les déploiements lourds
  - Export/import de configuration

---

## 🚀 Phase 4 — Intelligence (v0.4)

**Optimiser automatiquement : routing, prédiction, adaptation.**

- [ ] **Scheduler intelligent**
  - Prédiction de durée des tâches basée sur l'historique
  - Matching worker/tâche optimisé (GPU pour matrices, CPU pour logique)
  - Work stealing : worker libre vole une tâche à un worker lent
  - Priorité dynamique : les tâches âgées montent dans la queue
- [ ] **WebGPU avancé**
  - Shaders configurables par l'utilisateur (upload WGSL + WASM)
  - Support float64 quand disponible
  - Réduction parallèle multi-GPU (si plusieurs workers GPU)
  - Benchmark automatique CPU vs GPU au premier run
- [ ] **Optimisation réseau**
  - Compression des blobs (gzip/brotli)
  - Cache HTTP (ETag) pour les blobs fréquents
  - Streaming des résultats partiels (tâches longues)
  - Batch de tâches : grouper N petites tâches en un seul envoi
- [ ] **Observabilité**
  - Métriques Prometheus : tâches/min, latence p50/p95/p99, workers actifs
  - Logs structurés JSON (compatible ELK/Loki)
  - Alertes : workers < N, latence > seuil, erreurs > seuil
- [ ] **Sécurité renforcée**
  - Chiffrement TLS mutuel (mTLS) workers → coordinateur
  - Signatures Ed25519 pour les résultats
  - Audit log immuable (toutes les transitions de tâches)

---

## 🌐 Phase 5 — Décentralisation (v1.0)

**Sortir du modèle centralisé : fédération, marketplace, communauté.**

- [ ] **Coordinateurs fédérés**
  - Protocole de fédération entre coordinateurs
  - Découverte automatique (DNS-SD, mDNS)
  - Routage inter-coordinateur : tâche → meilleur cluster
  - Failover : si un coordinateur tombe, les autres reprennent
- [ ] **Marketplace de calcul**
  - Crédits de calcul : 1 tâche CPU = X crédits, 1 tâche GPU = Y crédits
  - Gagner des crédits en fournissant du calcul
  - Dépenser des crédits pour soumettre des tâches
  - Prix dynamique basé sur l'offre/demande
- [ ] **Identité & portabilité**
  - Clé Ed25519 par client (identité portable entre coordinateurs)
  - Wallet de crédits transférable
  - Réputation globale (fédérée)
- [ ] **Gouvernance**
  - Spécification ouverte du protocole worker
  - Implémentations de référence (Python, JS, Rust, Go)
  - RFCs pour les évolutions du protocole

---

## 💭 Phase 6 — Horizon (v2.0+)

**Idées exploratoires, long terme.**

- [ ] **Compute-to-earn mobile** — app iOS/Android qui fournit du calcul en arrière-plan
- [ ] **Navigateur headless pool** — ferme de Puppeteer/Playwright sur serveurs pour workers GPU
- [ ] **WASI preview 2** — sandboxing standardisé, composants WASM
- [ ] **SIMD + Threads WASM** — accélération vectorielle et multithreading CPU
- [ ] **Plugin système** — providers de workers : Kubernetes, AWS Lambda free tier, Cloudflare Workers
- [ ] **Streaming tasks** — résultats intermédiaires affichés en temps réel (p ex. rendu 3D progressif)
- [ ] **Dataset distribué** — partage de datasets entre tâches (cache distribué)
- [ ] **ML distribué** — TensorFlow/PyTorch en mode data-parallel sur workers gratuits
- [ ] **Intégration IPFS** — stockage décentralisé des blobs
- [ ] **Token crypto** — ERC-20 pour les crédits, smart contract pour la vérification

---

## 📊 Métriques de succès par phase

| Phase | Workers simultanés | Tâches/jour | Temps moyen/tâche | Uptime coordinateur |
|-------|-------------------|-------------|-------------------|-------------------|
| v0.1  | 5-15              | 100         | < 2s              | 99%               |
| v0.2  | 20-50             | 1 000       | < 1s              | 99.5%             |
| v0.3  | 50-200            | 10 000      | < 500ms           | 99.9%             |
| v0.4  | 200-1 000         | 100 000     | < 200ms           | 99.95%            |
| v1.0  | 1 000+            | 1M+         | < 100ms           | 99.99%            |
