# Scrapower

> Aggregateur de puissance de calcul distribuée exploitant des ressources gratuites :
> navigateurs (WebAssembly + WebGPU), cloud free tiers, et postes personnels.

```
┌──────────┐     ┌──────────────┐     ┌───────────┐
│ Navigateur│     │              │     │  PC/NAS   │
│ WASM+GPU │────►│  Coordinator │◄────│  natif    │
│ (onglet) │     │  (port 8777) │     │  worker   │
└──────────┘     └──────┬───────┘     └───────────┘
                        │
                  ┌─────┴─────┐
                  │ Embedded  │
                  │ worker    │
                  └───────────┘
```

## Quick start

```bash
# 1. Installer les dépendances
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd worker-browser && npm install && cd ..

# 2. Builder le worker navigateur
cd worker-browser && npx esbuild src/index.ts --bundle --format=esm --outfile=../src/scrapower/coordinator/static/worker.js && cd ..

# 3. Lancer le coordinateur
SCRAPOWER_API_KEY=dev-key python -m scrapower.coordinator.main

# 4. Ouvrir http://localhost:8777 dans un navigateur
# 5. Soumettre une tâche :
curl -H "X-API-Key: dev-key" -X POST http://localhost:8777/tasks \
  -H "Content-Type: application/json" \
  -d '{"task_id":"test-1","runtime":"wasm","executable_hash":"...","input_hash":"..."}'
```

## Architecture

```
scrapower/
├── src/scrapower/
│   ├── coordinator/       # Serveur central (FastAPI + SQLite)
│   │   ├── api/           # API client (tasks, blobs, results)
│   │   ├── worker_gateway/# Protocole worker v2.1 (WebSocket + HTTP)
│   │   ├── static/        # Worker navigateur (JS bundle)
│   │   ├── scheduler.py   # Match tâches ↔ workers
│   │   ├── task_manager.py# Cycle de vie des tâches
│   │   ├── blob_store.py  # Stockage des binaires
│   │   ├── db.py          # Schéma SQLite
│   │   ├── config.py      # Configuration
│   │   └── main.py        # Point d'entrée
│   └── worker/
│       ├── client.py      # Worker natif Python (WebSocket)
│       ├── sandbox.py     # Sandbox d'exécution
│       └── runtimes/      # WASM runtime (wasmtime)
├── worker-browser/        # Worker navigateur TypeScript
│   └── src/
│       ├── index.ts       # Point d'entrée, WebSocket, exécution
│       ├── gpu.ts         # WebGPU (multiplication matricielle WGSL)
│       ├── sandbox.ts     # Sandbox Web Worker (WASM CPU)
│       └── ui.ts          # Widget d'interface
├── tests/                 # Tests unitaires + scripts de test
├── examples/              # Modules WASM d'exemple (.wat)
└── docs/                  # Documentation
```

## Fonctionnalités

- [x] Workers navigateur (WebAssembly CPU)
- [x] Workers natifs Python (aiohttp)
- [x] Worker embarqué (fallback intégré au coordinateur)
- [x] WebGPU — multiplication matricielle sur GPU navigateur
- [x] Distribution multi-worker avec load balancing
- [x] Protocole Worker v2.1 (WebSocket persistant + HTTP éphémère)
- [x] Stockage de blobs avec hash SHA-256
- [x] Sécurité : API key, rate limiting, port isolé (iptables)
- [x] Déploiement Oracle Cloud Always Free + Let's Encrypt
- [x] 33 tests unitaires

## Commandes

```bash
# Tests
pytest tests/ -v                          # Tous les tests (33)
pytest tests/test_scheduler.py -v         # Tests du scheduler
python tests/test_distribution.py \       # Test de distribution multi-worker
  --url http://localhost:8777 --api-key KEY --count 20
python tests/test_gpu.py \                # Test WebGPU
  --url http://localhost:8777 --api-key KEY --size 256

# Build
cd worker-browser && npx esbuild src/index.ts --bundle --format=esm --outfile=../src/scrapower/coordinator/static/worker.js

# Lint
ruff check src/ tests/
```

## Licence

MIT
