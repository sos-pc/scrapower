# Scrapower Architecture v1

Document de référence. Consolide toutes les décisions d'architecture,
la stack technique, les composants, et le plan de construction.

---

## 1. Vision

Agréger de la puissance de calcul hétérogène et gratuite (free tiers cloud,
navigateurs, PC personnels, CI/CD, environnements de dev en ligne…) sous
une API unique. Les workloads sont **embarrassingly parallel** : divisés
en tâches indépendantes, déterministes, sans état partagé ni communication
inter-worker.

**Phase 1 : usage SOLO.** Agréger ses propres comptes gratuits.
**Phase 5+ : usage communautaire.** Workers volontaires, réputation.

---

## 2. Stack technique

| Composant | Langage | Framework | Raison |
|-----------|---------|-----------|--------|
| **Coordinateur** | Python 3.12 | FastAPI + uvicorn | I/O-bound, prototypage rapide, écosystème ML |
| **Worker natif** | Python 3.12 | Même codebase | Partage tout le code client |
| **Worker navigateur** | TypeScript | Vanilla + Web Workers | Incompressible (seul langage navigateur) |
| **Worker Colab** | Python 3.12 | Même codebase | PyTorch/CUDA natif |
| **Harvester** | Python 3.12 | Même codebase | Toutes les APIs cloud sont Python-first |
| **CLI** | Python 3.12 | Même codebase | submit, status, result, capacity |
| **Dashboard** | HTML + SSE | Servi par FastAPI | Pas de framework JS |
| **WASM runtime** | wasmtime-py | Bindings Bytecode Alliance | Officiel, maintenu, instrumentation |
| **Base de données** | SQLite via aiosqlite | Fichier local | Zéro dépendance externe |
| **Déploiement** | Docker ou uv + systemd | Oracle ARM | Simple, reproductible |

**Nombre total de langages : 2** (Python + TypeScript).

---

## 3. Dépendances Python

```toml
[project]
name = "scrapower"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "pydantic>=2.0",
    "aiosqlite>=0.20",
    "wasmtime>=22.0",
    "aiohttp>=3.10",
    "structlog>=24.0",
    "cryptography>=43.0",
    "aiofiles>=24.0",
    "python-multipart>=0.0.12",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "mypy>=1.13",
    "ruff>=0.8",
    "httpx>=0.28",
]
```

---

## 4. Structure du projet

```
scrapower/
├── docs/
│   ├── worker-protocol-v2.md      ← Référence du protocole worker
│   └── architecture-v1.md         ← Ce document
├── pyproject.toml                 ← Racine workspace
├── src/scrapower/
│   ├── __init__.py
│   │
│   ├── coordinator/               ← Le serveur central
│   │   ├── __init__.py
│   │   ├── main.py                ← uvicorn + FastAPI, point d'entrée
│   │   ├── config.py              ← Settings (TOML + env vars)
│   │   ├── db.py                  ← SQLite models + migrations
│   │   ├── blob_store.py          ← PUT/GET/HEAD /blobs/{hash}, GC
│   │   ├── worker_gateway/
│   │   │   ├── __init__.py
│   │   │   ├── router.py          ← Routes WS + HTTP
│   │   │   ├── ws_handler.py      ← Mode A : WebSocket messages
│   │   │   ├── http_handler.py    ← Mode B : /worker/pull, /worker/submit
│   │   │   └── session.py         ← Session management, heartbeat watchdog
│   │   ├── task_manager.py        ← États, transitions, files d'attente
│   │   ├── scheduler.py           ← Match tâche ↔ worker, lifecycle-aware
│   │   ├── verification.py        ← Engine : trust, redundancy, game
│   │   ├── reputation.py          ← Score worker, historique
│   │   ├── keepalive.py           ← Gestion des keepalive tasks
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── client_api.py      ← POST /tasks, GET /tasks/:id, etc.
│   │   ├── dashboard.py           ← HTML + SSE endpoint
│   │   ├── health.py              ← /health, /metrics (Prometheus)
│   │   └── embedded_worker.py     ← Worker local intégré
│   │
│   ├── worker/                    ← Client worker (utilisé partout)
│   │   ├── __init__.py
│   │   ├── client.py              ← WS + HTTP client (Worker Protocol)
│   │   ├── sandbox.py             ← wasmtime wrapper + firejail
│   │   ├── runtimes/
│   │   │   ├── __init__.py
│   │   │   ├── wasm.py            ← WASM sandbox (wasmtime)
│   │   │   ├── python.py          ← Python subprocess sandbox
│   │   │   └── native.py          ← Native (TRUST only)
│   │   └── profiles/              ← Profils de contraintes simulées
│   │       ├── __init__.py
│   │       ├── oracle_free.toml
│   │       ├── colab_gpu.toml
│   │       ├── colab_cpu.toml
│   │       ├── github_actions.toml
│   │       ├── codespaces.toml
│   │       ├── lambda.toml
│   │       ├── cf_worker.toml
│   │       └── browser.toml
│   │
│   ├── harvester/                 ← Provisionneur de workers
│   │   ├── __init__.py
│   │   ├── core.py                ← Boucle de contrôle
│   │   ├── quota.py               ← Suivi des quotas
│   │   └── providers/
│   │       ├── __init__.py
│   │       ├── google_colab.py
│   │       ├── github_actions.py
│   │       ├── oracle.py
│   │       ├── aws_lambda.py
│   │       └── cloudflare.py
│   │
│   ├── cli/                       ← Client CLI
│   │   ├── __init__.py
│   │   └── main.py                ← submit, status, result, capacity
│   │
│   └── sdk/                       ← SDK utilisateur
│       ├── __init__.py
│       └── client.py              ← sdk.submit(python_fn, data)
│                                   ←   → compile WASM → upload → submit
│
├── worker-browser/                ← Projet TypeScript séparé
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── index.ts               ← WebSocket client + DOM UI
│       ├── sandbox.ts             ← WASM execution (Web Worker)
│       ├── gpu.ts                 ← WebGPU compute
│       └── ui.ts                  ← Stats, toggle, dashboard
│
├── tests/
│   ├── conftest.py                ← Fixtures (coordinator in memory)
│   ├── test_blob_store.py
│   ├── test_worker_gateway_ws.py
│   ├── test_worker_gateway_http.py
│   ├── test_task_manager.py
│   ├── test_scheduler.py
│   ├── test_scheduler_lifecycle.py
│   ├── test_verification.py
│   ├── test_reputation.py
│   ├── test_chaos.py              ← Worker meurt, timeout, etc.
│   ├── test_profiles.py           ← Chaque profil de contrainte
│   ├── test_security.py           ← Rate-limit, Sybil, ségrégation
│   └── test_e2e.py                ← Flux complet submit→result
│
├── data/                           ← .gitignored
│   ├── blobs/                      ← Content-addressed (sha256 prefix)
│   └── scrapower.db                ← SQLite
│
├── config/
│   ├── coordinator.toml            ← Configuration par défaut
│   └── profiles/                   ← Profils de workers
│       └── *.toml
│
├── scripts/
│   ├── chaos_test.sh               ← Scénarios de chaos
│   ├── benchmark.sh
│   └── deploy_oracle.sh
│
├── .gitignore
└── README.md
```

---

## 5. Composants du coordinateur (détaillé)

### 5.1 Payload Store (`blob_store.py`)

```
POST   /blobs              → 200 { "hash": "sha256hex" }
GET    /blobs/{hash}       → 200 raw bytes | 404
HEAD   /blobs/{hash}       → 200 | 404

Stockage : data/blobs/XX/XXXXXX... (préfixe de 2 char du hash)
GC : supprimer les blobs sans référence après 7 jours (30 jours pour checkpoints)
Limite : 50 Mo par blob
```

### 5.2 Worker Gateway (`worker_gateway/`)

**Mode A (WebSocket) :**
- Endpoint : `WS /worker/ws`
- Messages : hello, session, capabilities, heartbeat, task_assign/accept/reject/
  result, keepalive, bye
- Heartbeat watchdog : zombie après `3 × heartbeat_interval_ms`
- Task accept timeout : 5 secondes après assign

**Mode B (HTTP) :**
- `POST /worker/pull` : soumet capabilities + lifecycle → recoit task | no_task
- `POST /worker/submit` : soumet résultat → recoit ack
- Lease : tâche réassignée si résultat non reçu avant `lease_ms`

### 5.3 Task Manager (`task_manager.py`)

États : `PENDING → QUEUED → ASSIGNED → EXECUTING → SUBMITTED → CHALLENGING → VALIDATED | DISPUTED → RESOLVED | FAILED | TIMEOUT → REQUEUED | CANCELLED`

Transitions atomiques, vérification du `assignment_token`.

### 5.4 Scheduler (`scheduler.py`)

Boucle toutes les 5 secondes :
1. Récupérer tâches QUEUED
2. Filtrer workers compatibles (runtime, resources, lifecycle)
3. Appliquer règle de ségrégation `client_id ≠ worker_id`
4. Trier par score (réputation × fiabilité lifecycle)
5. Assigner avec `assignment_token`
6. Expiration : si pas de `task_accept` en 5s → réassigner
7. Max 3 retries, puis FAILED

### 5.5 Verification Engine (`verification.py`)

Stratégies :
- `trust` : résultat accepté sans vérification
- `redundancy` : N workers, consensus à M
- `game` : 1 worker exécute, période de challenge, bisection si contesté

### 5.6 Reputation Ledger (`reputation.py`)

Score ∈ [0, 1]. Fonction de :
- `tasks_completed` / `tasks_accepted`
- `disputes_won` / `disputes_lost`
- `uptime_ratio`
- `age_days`

### 5.7 Embedded Worker (`embedded_worker.py`)

Worker local qui tourne dans le processus du coordinateur.
- Priorité basse : ne prend les tâches que si aucun worker externe
- Rôle cold start : garantit que le système tourne toujours
- Rôle keepalive : maintient >20% CPU sur Oracle Free

### 5.8 Client API (`api/client_api.py`)

```
POST   /tasks              → { "task_id": "uuid" }
GET    /tasks/{id}         → { "status", "progress", "result_hash" }
GET    /results/{task_id}  → raw bytes
POST   /tasks/{id}/cancel  → { "ok": true }
GET    /capacity           → { "cpu_cores": 12, "gpu_count": 2, ... }
```

### 5.9 Dashboard (`dashboard.py`)

Page HTML unique servie par le coordinateur.
- Workers connectés (tableau temps réel via SSE)
- File d'attente des tâches
- Capacité estimée
- Logs récents

### 5.10 Observability (`health.py`)

```
GET /health   → { "status": "ok", "uptime_sec": 3600, "workers": 5 }
GET /metrics  → Prometheus format (workers_connected, tasks_*, …)
```

---

## 6. Profils de workers (contraintes simulées)

Chaque profil est un fichier TOML qui définit les capacités et le lifecycle
d'un type de worker. Le worker natif peut charger n'importe quel profil
pour simuler les contraintes avant déploiement réel.

### Exemple : `colab_gpu.toml`

```toml
[worker]
worker_id = "colab-gpu-01"
auth_level = 1
mode = "persistent"

[capabilities]
runtimes = ["wasm", "python"]
cpu_cores = 2
ram_mb = 12288
disk_mb = 78000

[capabilities.gpu]
supported = true
types = ["cuda"]
vram_mb = 15360

[lifecycle]
mode = "persistent"
max_lifetime_sec = 43200          # 12 heures
idle_timeout_sec = 1800           # 30 minutes → kill
availability_profile = "scheduled"

[network]
connectivity = "outgoing_only"
max_download_kbps = 5000
max_upload_kbps = 2000

[limits]
max_task_duration_ms = 3600000    # 1 heure
max_concurrent_tasks = 1
max_input_size_bytes = 52428800
max_output_size_bytes = 104857600

[verification]
can_challenge = false
challenge_timeout_max_sec = 0
```

### Exemple : `browser.toml`

```toml
[worker]
worker_id = "browser-01"
auth_level = 0
mode = "persistent"

[capabilities]
runtimes = ["wasm"]
cpu_cores = 4
ram_mb = 4096

[capabilities.gpu]
supported = true
types = ["webgpu"]

[lifecycle]
mode = "persistent"
max_lifetime_sec = null
idle_timeout_sec = 300
availability_profile = "sporadic"

[network]
connectivity = "outgoing_only"

[limits]
max_task_duration_ms = 120000      # 2 minutes
max_concurrent_tasks = 1
```

---

## 7. Sécurité (design choices)

| Menace | Mitigation |
|--------|-----------|
| Worker exécute sa propre tâche | Règle `client_id ≠ worker_id` dans le scheduler |
| Course condition assignation | `assignment_token` + timeout 5s |
| Blob empoisonné exécuté | Sandbox TOUS les runtimes (wasmtime, firejail, vm2) |
| Sybil pull vide | Rate-limit IP + backoff exponentiel |
| Worker usurpe identité | Auth niveaux 1-2 (token + Ed25519) |
| Disque saturé | GC blobs 7 jours + alerte seuil 80% |
| SQLite contention | WAL mode + file d'attente d'écriture |
| Pas de backup | Litestream vers S3 gratuit (phase 2) |
| MITM | HTTPS partout (obligatoire) |
| Stderr explosion | Tronqué à 4096 octets |

---

## 8. Plan de construction

### Phase 1 — Fondation (objectif : flux complet submit→result en local)

| Étape | Composants | Durée estimée | Test de validation |
|-------|-----------|---------------|-------------------|
| 1.1 | `main.py`, `config.py`, `db.py`, `blob_store.py` | 1 jour | `curl -X PUT --data-binary @file http://localhost:8777/blob` → hash |
| 1.2 | `worker_gateway/` (Mode A + B) | 1-2 jours | Worker factice se connecte en WS et HTTP |
| 1.3 | `worker/client.py`, `worker/sandbox.py` (WASM) + profils | 1-2 jours | Worker natif connecte, déclare capabilities, exécute WASM |
| 1.4 | `task_manager.py`, `scheduler.py` | 1-2 jours | submit → task créée → assignée → exécutée |
| 1.5 | `verification.py` (trust only) | ½ jour | Résultat accepté sans vérification, `verification_data: null` |
| 1.6 | `api/client_api.py`, `cli/main.py`, `sdk/client.py` | 1 jour | `scrapower submit --wasm test.wasm` → résultat |
| 1.7 | `embedded_worker.py` | ½ jour | 0 worker externe → l'embedded prend la tâche |
| 1.8 | `dashboard.py`, `health.py` | ½ jour | `curl /health` → OK, dashboard HTML visible |
| 1.9 | Tests de chaos | 1 jour | Worker meurt → réassignation, timeout → retry |
| **TOTAL** | | **7-9 jours** | Flux complet validé |

### Phase 2 — Diversité & résilience

| Étape | Composants |
|-------|-----------|
| 2.1 | `verification.py` (mode `redundancy` et `game` avec bisection WASM) |
| 2.2 | `worker-browser/` (TypeScript, Web Worker, WebGPU) |
| 2.3 | `reputation.py` (score fonctionnel) |
| 2.4 | `keepalive.py` (tâches bidon configurées par profil) |
| 2.5 | Backup Litestream + alertes (UptimeRobot ou cron externe) |
| 2.6 | `message_broker.py` (Pub/Sub pour distributed training) |
| 2.7 | `checkpoint.py` (sauvegarde/restauration état de tâche) |

### Phase 3 — Scale

| Étape | Composants |
|-------|-----------|
| 3.1 | `harvester/` + providers (Colab, GH Actions, Oracle) |
| 3.2 | Déploiement sur Oracle Free (coordinateur + embedded worker) |
| 3.3 | Optimisation SQLite (> 1000 workers → config WAL, index) |
| 3.4 | `aggregator.py` (MapReduce, FedAvg) |

### Phase 4+ — Communauté

| Étape | Composants |
|-------|-----------|
| 4.1 | Page web publique (worker navigateur volontaire) |
| 4.2 | Docker Hub image (déploiement worker en 1 commande) |
| 4.3 | Support multi-utilisateurs (API keys, quotas) |
| 4.4 | Documentation publique, site web |

---

## 9. Décisions architecturales (ADR)

### ADR-001 : Coordinateur central unique

**Décision :** Un seul processus coordinateur, pas de distribution.
**Raison :** Tous les projets qui marchent sont centralisés (BOINC, F@H).
La décentralisation est un problème futur.
**Conséquence :** Si le coordinateur meurt, le système est down.
Mitigations : backup Litestream, plan de redémarrage rapide.

### ADR-002 : Python pour tout le backend

**Décision :** Python (FastAPI) pour coordinateur, worker natif, harvester, CLI.
**Raison :** 2 langages au lieu de 3, prototypage rapide, écosystème ML.
**Risque accepté :** Perfs Python si > 10 000 workers. À ce stade, on
réécrira les hot paths en Rust (PyO3) si nécessaire.

### ADR-003 : Content addressing pour tous les blobs

**Décision :** `blob_hash = SHA256(content)`. Immuable, dédupliqué.
**Raison :** Vérification d'intégrité triviale, cache-friendly,
compatible IPFS/S3 futur.

### ADR-004 : Pas de TLS en dev local

**Décision :** HTTP/WS en localhost, TLS en prod.
**Raison :** Le navigateur acceptera `ws://localhost:8777` sans TLS
(uniquement en dev). Prod → certificat Let's Encrypt.

### ADR-005 : assignment_token anti race-condition

**Décision :** Chaque `task_assign` contient un token unique.
Le worker doit le renvoyer dans `task_accept` et `task_result`.
Timeout de 5 secondes pour `task_accept`.
**Raison :** Évite que 2 workers exécutent la même tâche.

### ADR-005b : Ségrégation client/worker configurable

**Décision :** La règle `client_id ≠ worker_id` est contrôlée par
un flag `enforce_segregation` (défaut: `false`).
**Raison :** En Phase 1 (usage solo), le propriétaire est à la fois
client et opérateur des workers. La ségrégation rendrait le système
inutilisable. Activée en Phase 4+ (communautaire).
**Conséquence :** Un worker peut exécuter ses propres tâches en solo.
Ce n'est pas un problème car il n'y a pas d'enjeu de triche.

### ADR-006 : Worker _embedded obligatoire
**Décision :** Le coordinateur héberge un worker local avec priorité basse.
**Raison :** Cold start (0 worker externe), keepalive Oracle (>20% CPU).
**Conséquence :** Le coordinateur consomme ses propres ressources pour
exécuter des tâches. Acceptable car il est sinon idle.

### ADR-007 : SDK utilisateur dans le scope phase 1

**Décision :** Le SDK (`sdk.submit(code, data)`) sélectionne automatiquement
le runtime approprié et package la soumission. Il ne compile PAS
Python en WASM.
**Raison :** Compiler du Python arbitraire en WASM n'est pas réaliste
(Pyodide = 25 Mo, trop lourd). Le SDK choisit le runtime :
- Si `code` est un fichier `.wasm` → runtime `wasm`
- Si `code` est une fonction Python → runtime `python`, sérialisée (dill)
- Si `code` est un script shell → runtime `native` (TRUST only)
Le scheduler assigne au worker qui supporte le runtime demandé.
**Conséquence :** Les tâches Python ne tournent que sur les workers
avec runtime Python (Colab, PC perso, Oracle). Les tâches WASM
tournent partout.

### ADR-008 : Verification — trust only en Phase 1

**Décision :** La Phase 1 utilise uniquement le mode de vérification
`trust`. Pas de redondance, pas de verification game, pas de
`state_roots` obligatoires.
**Raison :** En usage solo, l'utilisateur fait confiance à ses propres
workers. Le verification game (Arbitrum-style) et la redondance sont
complexes et inutiles sans workers externes.
**Conséquence :** `verification_data` dans `task_result` est `null`.
Le champ `state_roots` n'est pas collecté (pas d'overhead).
Les modes `redundancy` et `game` → Phase 2.

---

## 10. Glossaire

| Terme | Définition |
|-------|-----------|
| **Coordinateur** | Serveur central : schedule, stocke, vérifie, route |
| **Worker** | Processus qui exécute des tâches (tout environnement) |
| **Backend** | Implémentation concrète du Worker Protocol pour une source |
| **Tâche (Task)** | Unité de travail : code + input → output |
| **Job** | Groupe de tâches liées (ex: 1000 runs Monte Carlo) |
| **Blob** | Donnée binaire content-addressed (SHA256) |
| **Profil** | Fichier TOML de contraintes simulant un environnement cloud |
| **Harvester** | Service qui provisionne/gère les workers automatisés |
| **Verification Game** | Protocole interactif de bisection pour détecter la triche |
| **Keepalive** | Tâche bidon pour éviter les timeouts d'inactivité |
| **Embedded Worker** | Worker local dans le processus du coordinateur |

---

## 11. Références

- [Worker Protocol v2.1](./worker-protocol-v2.md) — Interface coordinateur↔worker
- [Free Cloud Tiers Research](./research/free-cloud-tiers-2026.md) — Sources de calcul gratuites
- [Unconventional Compute Sources](./research/unconventional-sources-2026.md) — Sources non conventionnelles
