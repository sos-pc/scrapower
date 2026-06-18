# Scrapower

[![CI](https://github.com/sos-pc/scrapower/actions/workflows/ci.yml/badge.svg)](https://github.com/sos-pc/scrapower/actions)
[![Tests](https://img.shields.io/badge/tests-44%2F44-brightgreen)](https://github.com/sos-pc/scrapower)
[![Security](https://img.shields.io/badge/security-97%2F100-brightgreen)](https://github.com/sos-pc/scrapower)

> **Agrégateur de calcul distribué gratuit** — exécute des tâches WASM et Python sur
> des navigateurs, GitHub Actions runners, et workers embarqués. Friction zéro.

```
┌──────────────┐     ┌───────────────┐     ┌──────────────────┐
│  Navigateur  │     │               │     │  GitHub Actions  │
│  WASM + GPU  │────►│  Coordinator  │◄────│  ubuntu-latest   │
│  (onglet)    │     │  FastAPI 8777 │     │  2 CPU, 7 GB RAM │
└──────────────┘     └───────┬───────┘     └──────────────────┘
                             │
                      ┌──────┴──────┐
                      │  Python     │
                      │  worker     │
                      └─────────────┘
```

## Démarrage rapide

```bash
# 1. Installer
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Builder le worker navigateur
cd worker-browser && npm ci && npx esbuild src/index.ts --bundle --format=esm \
  --outfile=../src/scrapower/coordinator/static/worker.js && cd ..

# 3. Lancer (mode dev)
SCRAPOWER_API_KEY=dev-key python -m scrapower.coordinator.main

# 4. Ouvrir http://localhost:8777 → le widget s'affiche en bas à droite
# 5. Soumettre une tâche :
curl -H "X-API-Key: dev-key" -X POST http://localhost:8777/tasks \
  -H "Content-Type: application/json" \
  -d '{"runtime":"wasm","executable_hash":"...","input_hash":"..."}'
```

## Production

```bash
# Déploiement Docker (recommandé)
cp .env.example .env  # éditer les secrets
docker compose up -d --build

# Ou déploiement distant
make deploy  # → Oracle Cloud via SSH
```

## Architecture

```
src/scrapower/
├── coordinator/         # Serveur central FastAPI + SQLite
│   ├── api/             # Endpoints client (tasks, blobs, results, stats)
│   ├── worker_gateway/  # Protocole Worker v2.1 (WebSocket)
│   ├── static/          # Worker navigateur (HTML + JS)
│   ├── scheduler.py     # Distribution des tâches
│   ├── task_manager.py  # Cycle de vie (QUEUED → ASSIGNED → VALIDATED)
│   ├── blob_store.py    # Stockage content-adressé (SHA-256)
│   ├── security.py      # Auth, rate limiting, audit log
│   ├── crypto_utils.py  # Chiffrement Fernet (per-deployment salt)
│   ├── db.py            # Schéma SQLite (5 tables)
│   └── main.py          # Point d'entrée
├── worker/              # Worker Python natif
│   ├── client.py        # Client WebSocket
│   ├── sandbox.py       # Abstraction d'exécution
│   └── runtimes/wasm.py # WASM via wasmtime (timeout 30s, fuel 100M instr)
├── harvester/           # Provisioning automatique de workers
│   └── providers/       # GitHub Actions, local, Colab
└── cli/                 # CLI : scrapower serve, submit, worker
```

## Types de workers

| Type | CPU | RAM | GPU | Durée | Friction |
|------|-----|-----|-----|-------|----------|
| **Navigateur** (onglet) | Variable | ~4 GB | WebGPU | Persistant | Zéro — juste ouvrir un onglet |
| **GitHub Actions** | 2 cœurs | 7 GB | ❌ | 6h max | 1 clic OAuth |
| **Embedded** (serveur) | 4 cœurs | 8 GB | ❌ | Persistant | Aucune (automatique) |
| **Python natif** | Machine hôte | Machine hôte | ❌ | Persistant | `pip install` |

## Modèle de sécurité

- **Content-addressing** : tous les blobs identifiés par SHA-256 → immuables
- **Assignment token** : token unique par tâche, vérifié avant complétion
- **Vérification challenge** : 10% des tâches double-exécutées, résultats comparés (mode configurable)
- **Inter-client isolation** : un client ne peut pas lire les tâches d'un autre
- **API key** : obligatoire pour `/tasks`, `/results`, `/stats`
- **Rate limiting** : 30 req/min/IP, max 5 workers/IP
- **WASM sandbox** : timeout 30s, fuel 100M instructions, mémoire max 16 MB
- **Headers sécurité** : HSTS, CSP, X-Frame-Options, X-Content-Type-Options via Caddy
- **Docker non-root** : conteneur en read-only, capabilities minimales
- **Audit complet** : 25 vulnérabilités auditées, 22 corrigées, 3 non-exploitables

## Commandes

```bash
# Tests (44)
pytest tests/ -q

# Lint + typecheck
ruff check src/ tests/
mypy src/scrapower --ignore-missing-imports

# Build
make build              # Worker JS
make docker-build       # Image Docker
make docker-up          # Lancer en local

# Déploiement
make deploy             # → Oracle Cloud
```

## Endpoints

| Méthode | Chemin | Auth | Description |
|---------|--------|------|-------------|
| GET | `/` | Non | Page worker navigateur |
| GET | `/health` | Non | Health check |
| GET | `/stats` | Non | Capacité infrastructure |
| POST | `/tasks` | API Key | Soumettre une tâche |
| GET | `/tasks/{id}` | API Key + Client | Statut d'une tâche |
| DELETE | `/tasks/{id}` | API Key + Client | Annuler une tâche |
| GET | `/results/{id}` | API Key + Client | Résultat d'une tâche |
| PUT | `/blobs` | API Key ou Token | Uploader un binaire |
| GET | `/blobs/{hash}` | Non (hash = auth) | Télécharger un binaire |
| WS | `/worker/ws` | Non | Connexion worker |
| GET | `/auth/github/login` | Non | OAuth GitHub |
| GET | `/auth/github/callback` | Non | Callback OAuth |

## Licence

MIT
