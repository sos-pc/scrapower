# Roadmap

## ✅ Terminé

- [x] Coordinateur central (FastAPI + SQLite + WebSocket)
- [x] Worker navigateur (TypeScript, WebAssembly CPU, Web Worker sandbox)
- [x] Worker natif Python (aiohttp, WebSocket persistant)
- [x] Worker embarqué (fallback intégré au coordinateur)
- [x] Protocole Worker v2.1 (2 modes : persistant WebSocket + éphémère HTTP)
- [x] WebGPU — multiplication matricielle sur GPU navigateur (shader WGSL)
- [x] Distribution multi-worker avec load balancing
- [x] Sécurité : API key, rate limiting, port isolé via iptables
- [x] Déploiement Oracle Cloud Always Free + Let's Encrypt (Caddy)
- [x] 33 tests unitaires (pytest + pytest-asyncio)
- [x] CI manuel : build esbuild, recréation DB au démarrage

## 🔜 Prochaine release (v0.2)

- [ ] **Qualité de code** — ruff strict, typage partout, docstrings
- [ ] **Politique de test** — 1 test par endpoint/feature, coverage ≥ 80%
- [ ] **GitHub** — repo public, CI via GitHub Actions, badge coverage
- [ ] **SDK WASM** — modules pré-compilés (sha256, matmul, Monte Carlo) pour utilisateurs
- [ ] **CLI `scrapower`** — `scrapower submit`, `scrapower status`, `scrapower worker`
- [ ] **Dashboard admin** — page HTML avec stats workers, tâches, santé
- [ ] **Worker keepalive** — reconnexion automatique navigateur après perte WebSocket

## 📋 Backlog (v0.3+)

- [ ] **Python runtime** — exécution directe de fonctions Python (sandboxées)
- [ ] **Vérification** — mode vérification par challenge (recompute et compare)
- [ ] **Réputation workers** — score basé sur succès/échecs, priorité aux meilleurs
- [ ] **Multi-tenant** — isolation par client_id, quotas
- [ ] **Colab/GitHub Actions harvester** — provisioning automatique de workers gratuits
- [ ] **Observabilité** — Prometheus metrics, logs structurés
- [ ] **WebGPU avancé** — shaders configurables, support float64, réduction parallèle

## 💭 Idées futures

- [ ] P2P / décentralisé — coordinateurs fédérés
- [ ] Marketplace — crédits, priorité payante
- [ ] WASI preview 2 — sandboxing standardisé
- [ ] SIMD WASM — accélération vectorielle CPU
