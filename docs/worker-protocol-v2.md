# Worker Protocol v2.1

Spécification de l'interface entre le coordinateur Scrapower et tout worker
(backend).

**Changements v2.0 → v2.1 (2026-06-14) :**
- Ajout `assignment_token` (anti race-condition)
- Ajout logs d'erreur dans `execution_metadata`
- Règle de ségrégation `client_id ≠ worker_id`
- Worker local intégré au coordinateur (cold start)
- Message types pour Message Broker, Checkpoint, Aggregator (pré-réservés)
- Considérations de sécurité (blob empoisonné, Sybil par pull vide)

---

## 1. Modèle de communication

Le coordinateur est **le serveur central unique** (pas de décentralisation en
phase 1). Tout worker **initie la connexion** (contrainte NAT/sandbox).

**Le coordinateur héberge également un worker local intégré** (`_embedded`)
qui utilise les ressources de la machine hôte. Ce worker garantit que le
système fonctionne même avec zéro worker externe (cold start).

Deux modes de transport sont supportés :

| Mode | Transport | Session | Heartbeat | Exemples |
|------|-----------|---------|-----------|----------|
| **A — Persistent** | WebSocket (WSS) | Longue durée | Obligatoire | Navigateur, Colab, PC perso, Oracle, Fly.io, Codespaces, _embedded |
| **B — Ephemeral** | HTTP (HTTPS) | 1 cycle requête/réponse | Aucun | Lambda, Cloud Run, CF Workers, Vercer Functions |

Un worker **choisit son mode** à la connexion. Le coordinateur s'adapte.

---

## 2. Mode A — Persistent (WebSocket)

### 2.1 Cycle de vie

```
WORKER                                         COORDINATOR
  │                                                 │
  │  ① CONNECT (WSS /worker/ws)                     │
  │  ──────────────────────────────────────────────►│
  │  { "type": "hello", ... }                        │
  │                                                 │
  │  ② SESSION (session_id, config)                  │
  │ ◄──────────────────────────────────────────────│
  │                                                 │
  │  ③ CAPABILITIES                                  │
  │  ──────────────────────────────────────────────►│
  │  { "type": "capabilities", ... }                 │
  │                                                 │
  │  ④ BOUCLE PRINCIPALE                             │
  │  ┌─────────────────────────────────────────┐    │
  │  │ HEARTBEAT  ──────►  toutes les 10-30s   │    │
  │  │ TASK_ASSIGN ◄─────  push du scheduler   │    │
  │  │ TASK_ACCEPT ──────►                     │    │
  │  │ TASK_RESULT ──────►  résultat soumis     │    │
  │  │ KEEPALIVE   ◄─────  si file vide         │    │
  │  └─────────────────────────────────────────┘    │
  │                                                 │
  │  ⑤ DISCONNECT                                    │
  │     (timeout heartbeat OU bye explicite)         │
  └─────────────────────────────────────────────────┘
```

### 2.2 Messages

#### hello (worker → coordinator)

```json
{
  "type": "hello",
  "version": "2.0",
  "mode": "persistent",
  "worker_id": "auto | ed25519_pubkey | token",
  "auth": {
    "method": "none | token | signed_nonce",
    "value": "..."
  }
}
```

#### session (coordinator → worker)

```json
{
  "type": "session",
  "session_id": "uuid-v4",
  "heartbeat_interval_ms": 10000,
  "coordinator_version": "0.1.0",
  "config": {
    "max_task_queue": 2,
    "keepalive_enabled": true
  }
}
```

#### capabilities (worker → coordinator)

```json
{
  "type": "capabilities",
  "session_id": "...",
  "payload": {
    "runtimes": ["wasm", "python", "node", "native"],
    "resources": {
      "cpu_cores": 4,
      "ram_mb": 8192,
      "disk_mb": 51200,
      "gpu": {
        "supported": false
      }
    },
    "lifecycle": {
      "mode": "persistent",
      "max_lifetime_sec": null,
      "expected_remaining_sec": null,
      "idle_timeout_sec": null
    },
    "verification": {
      "can_challenge": true,
      "challenge_timeout_max_sec": 300
    },
    "network": {
      "connectivity": "outgoing_only | full",
      "max_download_bytes_per_sec": 10485760,
      "max_upload_bytes_per_sec": 5242880
    },
    "limits": {
      "max_task_duration_ms": 3600000,
      "max_concurrent_tasks": 2,
      "max_input_size_bytes": 52428800,
      "max_output_size_bytes": 104857600
    }
  }
}
```

#### heartbeat (worker → coordinator)

```json
{
  "type": "heartbeat",
  "session_id": "...",
  "current_load_pct": 45.0,
  "tasks_in_progress": 1,
  "uptime_sec": 3600,
  "expected_remaining_sec": 7200
}
```

#### heartbeat_ack (coordinator → worker)

```json
{
  "type": "heartbeat_ack",
  "lease_renewed_until": "2026-06-14T12:05:00Z"
}
```

#### task_assign (coordinator → worker)

```json
{
  "type": "task_assign",
  "task": {
    "id": "uuid-v4",
    "definition_hash": "sha256 hex",
    "runtime": "wasm",
    "client_id": "submitter's client_id",
    "assignment_token": "uuid-v4 unique pour cette assignation",
    "resources_required": {
      "cpu_cores_min": 2,
      "ram_mb_min": 512,
      "gpu_required": false
    },
    "deadline_ms": 60000,
    "payload": {
      "executable_hash": "sha256 hex du blob WASM",
      "input_hash": "sha256 hex du blob d'entrée"
    },
    "verification": {
      "mode": "trust | redundancy | game",
      "redundancy_count": 3,
      "challenge_duration_ms": 60000,
      "dispute_script_hash": "sha256 hex | null"
    },
    "reward": {
      "base_credit": 100,
      "bonus_fast_ms": 30000,
      "bonus_credit": 50
    }
  }
}
```

**Règle de ségrégation :** le scheduler ne DOIT PAS assigner une tâche
dont `client_id` est égal au `worker_id` (ou `client_id`) du worker.
Un worker ne peut pas exécuter ses propres tâches.

Le worker DOIT renvoyer `assignment_token` dans `task_accept` et `task_result`.
Le scheduler vérifie que le token correspond à l'assignation en cours.
Si un `task_accept` arrive sans `assignment_token` valide ou après expiration
(5 secondes), la tâche est réassignée.

#### task_accept (worker → coordinator)

```json
{
  "type": "task_accept",
  "session_id": "...",
  "task_id": "...",
  "assignment_token": "uuid from task_assign"
}
```

Le scheduler doit recevoir ce message dans les 5 secondes suivant `task_assign`.
Sinon → réassignation.

#### task_reject (worker → coordinator)

```json
{
  "type": "task_reject",
  "session_id": "...",
  "task_id": "...",
  "reason": "resource_unavailable | runtime_unsupported | timeout_too_short"
}
```

#### task_result (worker → coordinator)

```json
{
  "type": "task_result",
  "session_id": "...",
  "task_id": "...",
  "assignment_token": "uuid from task_assign",
  "status": "success | error | timeout",
  "result": {
    "output_hash": "sha256 hex du blob résultat",
    "execution_metadata": {
      "duration_ms": 1234,
      "instructions_executed": 50000000,
      "memory_peak_mb": 256,
      "exit_code": 0,
      "stderr": "captured stderr (truncated to 4096 bytes)",
      "oom_detected": false
    }
  },
  "verification_data": null
}
```

**Phase 1 :** `verification_data` est toujours `null`. Les `state_roots`
ne sont pas collectés (pas d'overhead). La vérification est en mode
`trust` uniquement. Les modes `redundancy` et `game` → Phase 2.

#### keepalive (coordinator → worker)

Envoyée quand la file de tâches est vide et que le worker a besoin d'activité
pour éviter un timeout (Colab, Oracle < 20% CPU, Replit…).

```json
{
  "type": "keepalive",
  "session_id": "...",
  "task": {
    "id": "keepalive-uuid",
    "runtime": "wasm",
    "deadline_ms": 5000,
    "payload": {
      "executable_hash": "sha256 du module keepalive standard",
      "input_hash": "sha256 de l'entrée keepalive (seed aléatoire)"
    }
  }
}
```

Le worker exécute la tâche keepalive (mini-calcul : hash, PRNG) et soumet
le résultat comme une tâche normale. Le coordinateur ne valide pas le résultat
(c'est du bruit).

#### bye (worker → coordinator)

```json
{
  "type": "bye",
  "session_id": "...",
  "reason": "user_disconnect | quota_exhausted | shutdown"
}
```

### 2.3 Timeouts

- **Heartbeat** : si aucun heartbeat reçu pendant `3 × heartbeat_interval_ms`,
  le worker est déclaré **ZOMBIE**.
- **Zombie** : les tâches en cours sont réassignées immédiatement.
- **Task deadline** : si `task_result` non reçu avant `deadline_ms`, la tâche
  est réassignée. Le worker est notifié via `task_cancel`.

---

## 3. Mode B — Ephemeral (HTTP)

### 3.1 Cycle de vie

```
WORKER                                        COORDINATOR
  │                                                │
  │  ① PULL (POST /worker/pull)                     │
  │  ─────────────────────────────────────────────►│
  │  { capabilities + lifecycle }                    │
  │                                                │
  │  ② RESPONSE                                     │
  │ ◄─────────────────────────────────────────────│
  │  { task | no_task }                             │
  │                                                │
  │  ③ (si task) EXECUTE                            │
  │  ┌─────────────────────────────┐               │
  │  │ Télécharger payload          │               │
  │  │ Exécuter dans la sandbox     │               │
  │  │ Uploader résultat            │               │
  │  └─────────────────────────────┘               │
  │                                                │
  │  ④ SUBMIT (POST /worker/submit)                 │
  │  ─────────────────────────────────────────────►│
  │  { task_id, result }                            │
  │                                                │
  │  ⑤ ACK                                          │
  │ ◄─────────────────────────────────────────────│
  │                                                │
  │  FIN (le worker meurt ici)                      │
  └────────────────────────────────────────────────┘
```

### 3.2 Messages

#### pull (worker → coordinator)

```json
{
  "type": "pull",
  "version": "2.0",
  "mode": "ephemeral",
  "worker_id": "auto | token",
  "capabilities": {
    "runtimes": ["wasm"],
    "resources": {
      "cpu_cores": 2,
      "ram_mb": 3008,
      "disk_mb": 512,
      "gpu": { "supported": false }
    },
    "lifecycle": {
      "mode": "ephemeral",
      "max_lifetime_sec": 900,
      "expected_remaining_sec": 850,
      "idle_timeout_sec": null
    }
  }
}
```

#### pull_response (coordinator → worker)

```json
{
  "type": "pull_response",
  "task": {
    "id": "uuid-v4",
    "runtime": "wasm",
    "deadline_ms": 30000,
    "lease_ms": 30000,
    "payload": {
      "executable_url": "https://coord/blob/sha256hex",
      "input_url": "https://coord/blob/sha256hex"
    },
    "verification": {
      "mode": "trust",
      "challenge_duration_ms": 0
    }
  }
}
```

Si pas de tâche :

```json
{
  "type": "pull_response",
  "task": null
}
```

#### submit (worker → coordinator)

```json
{
  "type": "submit",
  "task_id": "uuid-v4",
  "worker_id": "...",
  "status": "success | error | timeout",
  "result": {
    "output_hash": "sha256 hex",
    "execution_metadata": {
      "duration_ms": 2345,
      "exit_code": 0
    }
  }
}
```

#### submit_ack (coordinator → worker)

```json
{
  "type": "submit_ack",
  "task_id": "...",
  "accepted": true,
  "credit_earned": 100
}
```

### 3.3 Leases

- Le `lease_ms` est inférieur ou égal au `max_lifetime_sec` du worker.
- Si le résultat n'arrive pas avant `lease_ms` → tâche réassignée.
- Le worker **ne doit pas** accepter une tâche dont le `deadline_ms` dépasse
  son `expected_remaining_sec`.

---

## 4. Lifecycle — Champ commun aux deux modes

Chaque worker déclare son cycle de vie. Le scheduler l'utilise pour choisir
les tâches compatibles.

```json
{
  "lifecycle": {
    "mode": "persistent | ephemeral | batch",
    "max_lifetime_sec": 21600,
    "expected_remaining_sec": 18000,
    "idle_timeout_sec": 300,
    "availability_profile": "always_on | scheduled | burst | sporadic"
  }
}
```

| Champ | Description | Exemples |
|-------|-------------|----------|
| `mode` | `persistent` = WebSocket longue durée, `ephemeral` = 1 cycle HTTP, `batch` = WS mais durée bornée (CI/CD) | |
| `max_lifetime_sec` | Durée de vie max. `null` = infini | GH Actions: 21600 |
| `expected_remaining_sec` | Temps restant estimé avant arrêt | Décroît dans le temps |
| `idle_timeout_sec` | Inactivité max avant kill | Colab: 1800, Codespaces: 1800 |
| `availability_profile` | Profil de disponibilité | Oracle: `always_on`, Navigateur: `sporadic`, GH Actions: `batch` |

### Règles du scheduler

```
┌─────────────────────────────────────────────────────────────┐
│  RÈGLES D'ASSIGNATION                                       │
│                                                             │
│  1. Tâche deadline_ms > expected_remaining_sec ?             │
│     → NE PAS assigner.                                        │
│                                                             │
│  2. Tâche deadline_ms > idle_timeout_sec × 2 ?              │
│     → Assigner seulement si le worker a des tâches actives  │
│       (sinon il va timeout idle avant de finir).            │
│                                                             │
│  3. keepalive nécessaire ?                                   │
│     → idle_timeout_sec approche ET file vide                │
│     → Envoyer keepalive task.                               │
│                                                             │
│  4. Mode batch approche de sa fin ?                         │
│     → max_lifetime_sec - expected_remaining_sec < 600 ?     │
│     → Ne plus assigner de nouvelles tâches.                │
│     → Attendre les résultats en cours.                     │
│                                                             │
│  5. Verification game :                                     │
│     → Assigner à worker avec `can_challenge: true`          │
│       ET `lifecycle.mode: persistent`                       │
│       ET `challenge_timeout_max_sec ≥ challenge_duration`.  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Authentication & Identity

```
┌────────────────────────────────────────────────────────────┐
│                                                            │
│  NIVEAU 0 — ANONYMOUS                                      │
│  • worker_id = UUID aléatoire généré par le worker          │
│  • auth.method = "none"                                    │
│  • Réputation : 0 (fraîche)                                │
│  • Limité aux tâches courtes (< 30s), pas de challenge     │
│  • Utilisé par : navigateur visiteur, Lambda               │
│                                                            │
│  NIVEAU 1 — TOKEN                                          │
│  • worker_id = hash du token                               │
│  • auth.method = "token"                                   │
│  • auth.value = token pré-partagé                          │
│  • Réputation : liée au token, persiste entre sessions     │
│  • Utilisé par : Harvester (Colab, GH Actions…)            │
│                                                            │
│  NIVEAU 2 — SIGNED KEY (Ed25519)                           │
│  • worker_id = base58(public_key)                          │
│  • auth.method = "signed_nonce"                            │
│  • auth.value = signature(nonce_fourni_par_coordo)         │
│  • Le worker prouve qu'il possède la clé privée            │
│  • Réputation : fortement liée à la clé                   │
│  • Utilisé par : PC perso, Oracle (identité stable)        │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

---

## 6. Gestion des erreurs

### 6.1 Codes d'erreur (commun aux deux modes)

```json
{
  "type": "error",
  "code": "INVALID_MESSAGE | AUTH_FAILED | RATE_LIMITED | 
           TASK_NOT_FOUND | SESSION_EXPIRED | WORKER_BANNED |
           TASK_ALREADY_ASSIGNED | LEASE_EXPIRED |
           PAYLOAD_TOO_LARGE | RUNTIME_UNSUPPORTED |
           COORDINATOR_OVERLOAD | INTERNAL_ERROR",
  "message": "Human-readable explanation",
  "details": {}
}
```

### 6.2 Scénarios de reprise

| Événement | Action coordinateur |
|-----------|-------------------|
| Worker déconnecté (mode A) | Tâches en cours → réassignées après 30s |
| Task timeout (deadline dépassée) | Réassigner. Max 3 retries. |
| Worker soumet résultat après deadline | Refuser (`LEASE_EXPIRED`). Résultat ignoré. |
| Worker rejette une tâche | Réassigner immédiatement. |
| Worker en erreur 3 fois de suite | Score réputation −0.1. Réduire priorité. |
| Worker submit `status: error` | Considérer comme échec. Réassigner. |
| Résultat corrompu (hash mismatch) | Considérer comme triche. Score −0.3. |

---

## 7. Payloads & Content Addressing

```
┌────────────────────────────────────────────────────────────┐
│                                                            │
│  Tous les blobs (code, données, résultats) sont            │
│  content-addressed : content_hash = SHA256(blob)           │
│                                                            │
│  ENDPOINTS (communs aux deux modes) :                      │
│                                                            │
│  PUT  /blobs                → 200 { "hash": "..." }       │
│       Body: raw bytes                                     │
│       Headers: Content-Type, Content-Length               │
│                                                            │
│  GET  /blobs/{hash}         → 200 raw bytes               │
│                              404 NOT_FOUND                │
│                                                            │
│  HEAD /blobs/{hash}         → 200 / 404                   │
│                                                            │
│  LIMITES :                                                 │
│  • Max blob size : 50 Mo (configurable)                    │
│  • Rate limit : 10 PUT/min, 100 GET/min par IP             │
│  • Expiration : GC après 7 jours sans référence            │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

---

## 8. Keepalive tasks

Le coordinateur fournit une tâche WASM standard pour le keepalive :

```wat
;; keepalive.wasm — fait tourner un PRNG pendant ~1-5 secondes
;; pour simuler de l'activité et éviter les timeouts.
(module
  (import "env" "now_ms" (func $now_ms (result i64)))
  (memory (export "memory") 1)
  
  (func (export "compute") (param $input_ptr i32) (param $input_len i32)
                            (param $output_ptr i32) (param $output_len i32)
                            (result i32)
    (local $seed i64)
    ;; Lire le seed depuis l'input
    (local.set $seed (i64.load (local.get $input_ptr)))
    ;; Boucle PRNG jusqu'à ce que ~2 secondes soient écoulées
    (loop $spin
      (local.set $seed (i64.add (i64.mul (local.get $seed) (i64.const 6364136223846793005))
                                 (i64.const 1442695040888963407)))
      (i64.lt_u (call $now_ms) (i64.add (i64.load (local.get $input_ptr))
                                          (i64.const 2000)))
      (br_if $spin))
    ;; Écrire le seed final en sortie (ignoré)
    (i64.store (local.get $output_ptr) (local.get $seed))
    (i32.const 0))  ;; exit code 0
)
```

---

## 9. Résumé des endpoints

```
┌────────────────────────────────────────────────────────────┐
│                                                            │
│  COORDINATEUR ENDPOINTS                                    │
│                                                            │
│  MODE A (WebSocket) :                                      │
│  WSS /worker/ws                                            │
│                                                            │
│  MODE B (HTTP) :                                           │
│  POST /worker/pull        → { task | no_task }            │
│  POST /worker/submit      → { ack }                       │
│                                                            │
│  STOCKAGE :                                                │
│  PUT  /blobs               → { hash }                     │
│  GET  /blobs/{hash}        → raw bytes                    │
│  HEAD /blobs/{hash}       → 200/404                      │
│                                                            │
│  CLIENT (utilisateur) :                                    │
│  POST /tasks                → { task_id }                 │
│  GET  /tasks/{id}           → { status, ... }             │
│  GET  /results/{task_id}    → { result_hash, ... }        │
│  POST /tasks/{id}/cancel    → { ok }                      │
│                                                            │
│  MÉTRIQUES :                                               │
│  GET  /capacity             → { cpu_cores, gpu_count, ... }│
│  GET  /health               → { status, uptime, ... }     │
│  GET  /metrics              → Prometheus format           │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

---

## 10. Exemple de flux complet (Mode A)

```
1. Navigateur ouvre wss://coordinator.example.com/worker/ws
   → envoie hello
   ← reçoit session (heartbeat 10s)

2. Déclare capabilities :
   → runtimes: [wasm], cpu: 4, ram: 8192, gpu: {webgpu: true}
   → lifecycle: persistent, can_challenge: true

3. Boucle heartbeat :
   → toutes les 10s, envoie heartbeat
   ← reçoit heartbeat_ack

4. Le scheduler a une tâche wasm compatible :
   ← reçoit task_assign { task_id: "abc", runtime: "wasm", ... }
   → envoie task_accept

5. Télécharge le payload :
   GET /blobs/{executable_hash}   → module WASM
   GET /blobs/{input_hash}        → données d'entrée

6. Exécute dans un Web Worker (WASM sandbox) :
   → compute(input_ptr, input_len, output_ptr, output_len)
   → exit_code = 0

7. Upload le résultat :
   PUT /blobs                  → output_hash

8. Soumet le résultat :
   → envoie task_result { status: "success", output_hash, verification_data: null }

9. Résultat validé immédiatement (mode trust, Phase 1)
   (notification via event "task_validated")

10. Si file vide + risque idle timeout :
    ← reçoit keepalive
    → exécute keepalive.wasm (~2s PRNG)
    → envoie task_result (ignoré)
```

---

## 11. Exemple de flux complet (Mode B)

```
1. AWS Lambda est invoquée :

   POST /worker/pull
   { capabilities: { runtimes: [wasm], lifecycle: { mode: ephemeral,
     max_lifetime_sec: 900, expected_remaining_sec: 850 } } }

   ← { task: { id: "def", lease_ms: 30000, runtime: "wasm", ... } }

2. Télécharge le payload :
   GET executable_url → module WASM
   GET input_url → données

3. Exécute dans wasmtime :
   → compute(input, output)
   → exit_code = 0

4. Upload le résultat :
   PUT /blobs → output_hash

5. Soumet :
   POST /worker/submit
   { task_id: "def", status: "success", output_hash }

   ← { accepted: true, credit_earned: 100 }

6. Fin. Le Lambda meurt.
```

---

## 12. Worker local intégré (_embedded)

Le coordinateur héberge un worker local qui utilise les ressources de la
machine hôte (CPU, RAM, disque). Ce worker :

- Se connecte en local (`ws://localhost:{port}`) au démarrage
- Utilise le mode A (persistent)
- Niveau d'auth 2 (Ed25519, clé générée au premier démarrage)
- `worker_id = "_embedded"`
- Priorité de scheduling : **basse** (dernier recours)
- Rôle principal : éviter le cold start, maintenir >20% CPU Oracle

```python
# Comportement au démarrage du coordinateur
async def start_embedded_worker():
    if not any_external_worker_connected():
        # Cold start : l'embedded worker prend toutes les tâches
        pass
    else:
        # Mode veille : ne prend que les tâches urgentes (deadline proche)
        pass
```

---

## 13. Message Broker (Pub/Sub) — pré-réservé, phase 2

Permet la communication indirecte entre workers via le coordinateur.
Nécessaire pour le distributed training (FedAvg) et les pipelines.

```json
{ "type": "publish",   "topic": "gradients/round_42", "payload_hash": "sha256" }
{ "type": "subscribe", "topics": ["gradients/*"] }
{ "type": "unsubscribe", "topics": ["gradients/*"] }
{ "type": "deliver",   "topic": "gradients/round_42", "payload_hash": "sha256", "from": "worker-xxx" }
```

---

## 14. Checkpoint Manager — pré-réservé, phase 2

Permet de sauvegarder et restaurer l'état d'une tâche longue.

```json
{ "type": "checkpoint",       "task_id": "...", "checkpoint_data_hash": "sha256", "step": 12500 }
{ "type": "checkpoint_ack",   "checkpoint_id": "uuid", "stored": true }
{ "type": "checkpoint_restore", "task_id": "...", "checkpoint_id": "uuid" }
```

Les checkpoints sont stockés comme des blobs normaux, avec un TTL étendu
(30 jours au lieu de 7).

---

## 15. Aggregator — pré-réservé, phase 3

Fonctions d'agrégation côté coordinateur pour MapReduce et FedAvg.

```json
{ "type": "aggregate_request", "task_group_id": "...", "method": "fedavg | reduce | concat", "parts": ["hash1", "hash2"] }
{ "type": "aggregate_result",  "task_group_id": "...", "result_hash": "sha256", "ready": true }
```

---

## 16. Considérations de sécurité

### 16.1 Blob empoisonné

Les workers doivent sandboxer TOUS les runtimes, pas seulement WASM :
- **WASM** : sandbox natif (wasmtime sans WASI)
- **Python** : exécuter dans un sous-processus avec `firejail` ou conteneur
  Docker minimal (réseau désactivé, FS read-only sauf /tmp)
- **Node.js** : idem, sandbox via `vm2` ou `isolated-vm`
- **Natif** : TRUST uniquement — réservé aux workers de réputation ≥ 0.8

Le coordinateur doit vérifier qu'un module WASM est syntaxiquement valide
avant de l'envoyer aux workers.

### 16.2 Sybil par pull vide

Un attaquant inonde `POST /worker/pull` avec des workers anonymes.
Mitigations :
- Rate-limit : 1 requête/5s par IP
- Si `no_task` × 10 consécutifs → backoff exponentiel (5s → 10s → 20s...)
- File d'attente des workers anonymes : max 100 connexions
- Si dépassement → refuser avec `RATE_LIMITED`

### 16.3 Séparation client/worker

Règle stricte : `task.client_id ≠ worker.client_id`.
Implémentée dans le scheduler, pas de contournement possible.

### 16.4 Injection de stderr dans execution_metadata

Le `stderr` capturé est tronqué à 4096 octets pour éviter l'explosion
des métadonnées (un worker malveillant pourrait générer des Go de logs).

---

## 17. Versions

| Version | Date | Changements |
|---------|------|-------------|
| 1.0 | — | Protocole initial (jamais formalisé) |
| 2.0 | 2026-06-14 | Ajout Mode B (HTTP ephemeral), lifecycle-aware scheduling, keepalive, two-tier verification, content addressing standardisé, auth multi-niveaux |
| **2.1** | 2026-06-14 | `assignment_token`, logs stderr, ségrégation client/worker, worker _embedded, Message Broker + Checkpoint + Aggregator réservés, considérations sécurité |

---

## 18. Implémentations de référence prévues

| Backend | Mode | Langage | Statut |
|---------|------|---------|--------|
| `native` (Oracle, PC perso) | A | Rust (même binaire que coordo) | À faire |
| `browser` (navigateur) | A | TypeScript + WASM | À faire |
| `colab` (Google Colab) | A | Python + ngrok | À faire |
| `github-actions` (GH Actions) | A | Python (dans le job CI) | À faire |
| `lambda` (AWS Lambda) | B | Python / Rust | À faire |
| `cloud-run` (GCP Cloud Run) | B | Rust | À faire |
| `cf-worker` (Cloudflare Workers) | B | TypeScript | À faire |
