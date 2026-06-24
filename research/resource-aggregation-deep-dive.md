# Rapport d'approfondissement — Agrégation de ressources hétérogènes

> **Date :** 2026-06-18 — Session fondations  
> **Contexte :** Analyse de ce que Scrapower peut réellement agréger aujourd'hui,
> et de ce qu'il faut implémenter pour chaque source de calcul gratuite documentée
> dans `research/free-cloud-tiers-2026.md`.

---

## 1. État des lieux — Ce qui tourne en production

### Workers déjà opérationnels

| Type | Runtime | Sandbox | Proto | Déploiement | Fiabilité |
|------|---------|---------|-------|-------------|-----------|
| **Navigateur** (WebAssembly + WebGPU) | WASM / Pyodide | wasmtime (navigateur) | Mode A (WS) | `embed.html` → iframe | ⚠️ Onglet fermable |
| **Embedded** (process interne) | WASM | wasmtime (serveur) | WS localhost | `start()` dans main.py | ✅ Toujours là |
| **GitHub Actions** | WASM | wasmtime (runner) | Mode A (WS) | OAuth → repo auto-créé → workflow dispatch | ⚠️ 6h max + ToS risqué |
| **Natif Python** | Python | ❌ Aucun (TRUST) | Mode A (WS) | Manuel (pip install) | 🔴 Non sandboxé |

### Ce que le protocole v2.1 sait déjà exprimer

Le message `capabilities` (envoyé par le worker au hello) supporte déjà :

```json
{
  "runtimes": ["wasm", "python", "node", "native"],
  "resources": {
    "cpu_cores": 4, "ram_mb": 8192, "disk_mb": 51200,
    "gpu": { "supported": false }
  },
  "lifecycle": {
    "mode": "persistent | ephemeral | batch",
    "max_lifetime_sec": 21600,
    "expected_remaining_sec": 18000,
    "availability_profile": "always_on | scheduled | burst | sporadic"
  },
  "network": {
    "connectivity": "outgoing_only | full",
    "max_download_bytes_per_sec": 10485760
  },
  "limits": {
    "max_task_duration_ms": 3600000,
    "max_concurrent_tasks": 2,
    "max_input_size_bytes": 52428800
  }
}
```

**Le protocole est prêt.** Le scheduler (`domain.py`, `scheduler.py`) utilise
déjà `runtimes`, `resources.ram_mb`, `gpu.supported`, et `lifecycle.expected_remaining_sec`
pour filtrer les workers compatibles. Ajouter un nouveau type de worker
se résume à : **créer un processus qui se connecte en WebSocket et envoie
les bons messages.**

---

## 2. Gap analysis — Chaque source gratuit → worker Scrapower

### 2.1 Oracle Cloud ARM (12 GB RAM, 200 GB disk, 10 TB egress)

**Statut :** Le serveur de prod tourne déjà sur Oracle ARM. ✅  
**Gap :** Pas de worker Scrapower dédié sur cette instance.

| Ce qui existe | Ce qui manque | Fichiers à créer/modifier |
|---------------|---------------|--------------------------|
| Serveur ARM avec Python 3.12 | Un process worker natif qui se connecte en WS au coordinateur | `worker/oracle_worker.py` (nouveau) |
| `worker/client.py` (client worker natif) | Un systemd service ou Docker container qui lance le worker au boot | `deploy/oracle-worker.service` |
| Connection WS locale (`ws://127.0.0.1:8777/worker/ws`) | Tâches keepalive pour éviter l'idle reclamation (>20% CPU sur 7j) | Déjà prévu dans le protocole v2.1 (`keepalive` task) |

**Effort estimé :** ~30 min. Un script Python de 30 lignes qui importe
le client worker natif et se connecte au coordinateur local.

**Valeur :** Énorme. C'est la seule ressource « always-on, légitime,
haute capacité » de tout l'écosystème gratuit.

**Risque ToS :** Nul. Oracle Always Free est conçu pour être utilisé.

---

### 2.2 HuggingFace Spaces (16 GB RAM, 50 GB disk, CPU only)

**Statut :** Rien n'existe.  
**Gap :** Déploiement Docker sur HF Spaces + worker natif.

| Ce qui existe | Ce qui manque | Fichiers |
|---------------|---------------|----------|
| Dockerfile du coordinateur (multi-stage) | Un `Dockerfile` HF Spaces avec le worker natif | `deploy/hf-spaces/Dockerfile` |
| `worker/client.py` | Un point d'entrée HF qui lance le worker | `deploy/hf-spaces/app.py` |
| Connection WS vers `wss://your-coordinator.example.com/worker/ws` | Rien — c'est une config | `README.md` HF Spaces |

**Particularité HF Spaces :** HF fournit une URL publique. On peut exposer
un worker WebSocket MAIS HF tue les processus inactifs. Il faut soit un
heartbeat régulier, soit utiliser le Mode B (HTTP pull).

**Effort estimé :** ~1h. Un Dockerfile + un app.py de 50 lignes.

**Valeur :** Élevée. 16 GB RAM, toujours dispo, ToS tolérants pour le ML.
Parfait pour les tâches Python lourdes (pandas, numpy).

**Risque ToS :** Faible. HF tolère le compute distribué dans le cadre ML.

---

### 2.3 Modal ($30/mois crédits, GPU A100)

**Statut :** Rien n'existe.  
**Gap :** Intégration complète (SDK Modal + worker Scrapower).

| Ce qui existe | Ce qui manque | Fichiers |
|---------------|---------------|----------|
| `worker/client.py` | Un worker Modal qui utilise le SDK `modal` pour provisionner un conteneur GPU | `worker/modal_worker.py` |
| Protocole WS Mode A | Adaptation Mode B (HTTP pull) car Modal facture au temps de conteneur | `worker/modal_worker.py` |
| Blob store SHA-256 | Le worker télécharge exécutable + input, exécute, upload output | ✅ Déjà standard |

**Architecture recommandée :** Mode B (ephemeral HTTP). Le worker Modal :
1. POST `/worker/pull` → reçoit une tâche
2. Télécharge exécutable + input depuis le blob store
3. Exécute dans le conteneur GPU (PyTorch, CUDA)
4. Upload output → POST `/worker/submit`
5. Le conteneur s'arrête (pas de coût idle)

**Particularité GPU :** Les tâches GPU doivent être marquées `runtime: "python"`
et `gpu_required: true`. Le scheduler doit router vers Modal seulement si
la tâche nécessite CUDA (pas WebGPU).

**Effort estimé :** ~2h. Intégration du SDK Modal + adaptation Mode B.

**Valeur :** Très élevée. C'est le seul GPU gratuit légitime et performant.

**Risque ToS :** Nul. Les $30/mois sont explicitement offerts pour utilisation.

---

### 2.4 Kaggle (P100/T4, 30 GB RAM, 30h GPU/semaine)

**Statut :** Rien n'existe.  
**Gap :** Automatisation de notebook Kaggle.

| Ce qui existe | Ce qui manque | Fichiers |
|---------------|---------------|----------|
| Rien | Un notebook Kaggle qui agit comme worker Scrapower | `worker/kaggle_worker.ipynb` |
| Protocole Mode A (WS) | Connexion WebSocket depuis un notebook Kaggle | ✅ Kaggle supporte les WS |
| `worker/client.py` | Version « notebook » qui tourne en boucle dans une cellule | `worker/kaggle_worker.ipynb` |

**Contrainte clé :** Kaggle interdit l'automatisation non-interactive. Le
notebook doit être **lancé manuellement** par l'utilisateur. Pas de harvester
automatique. Le worker doit afficher une barre de progression et des logs
visibles pour prouver l'interaction humaine.

**Effort estimé :** ~1h pour le notebook. Le vrai coût est la documentation
utilisateur (« comment lancer le worker Kaggle »).

**Valeur :** Élevée pour le GPU. 30h/semaine de T4 gratuit, c'est imbattable.

**Risque ToS :** Modéré si lancé manuellement, élevé si automatisé.
**Stratégie :** Mode opt-in manuel uniquement.

---

### 2.5 Google Cloud Run (2M req/mois, 60 min timeout)

**Statut :** Rien n'existe.  
**Gap :** Déploiement Cloud Run + worker Mode B.

| Ce qui existe | Ce qui manque | Fichiers |
|---------------|---------------|----------|
| Dockerfile existant (coordinateur) | Une image Docker légère avec juste le worker | `deploy/cloud-run/Dockerfile.worker` |
| Mode B HTTP pull/spull | Rien — le worker Cloud Run est parfait pour le Mode B | `worker/cloudrun_worker.py` |

**Architecture :** Mode B exclusivement. Cloud Run facture au temps de
requête. Le worker :
1. Cloud Run démarre le conteneur (cold start ~1s)
2. POST `/worker/pull` → reçoit une tâche
3. Exécute (max 60 min)
4. POST `/worker/submit` → rend le résultat
5. Cloud Run arrête le conteneur (0 coût)

**Effort estimé :** ~1h. Un Dockerfile + 30 lignes de Python.

**Valeur :** Bonne pour le burst (pic de tâches). 2M requêtes/mois gratuites.

**Risque ToS :** Faible. Google Cloud Run est conçu pour ça.

---

### 2.6 Google Colab (T4 GPU, ~12h max)

**Statut :** Un fichier `colab.py` existe dans le harvester.  
**Gap :** Le harvester automatique est risqué (anti-bot Colab).

**Recommandation :** **Ne pas implémenter de harvester automatique.**
Colab détecte les patterns non-interactifs et bannit les comptes.
Utilisable uniquement en mode manuel (l'utilisateur ouvre le notebook
et clique « Run »). Même architecture que Kaggle.

---

### 2.7 Cloudflare Workers (128 MB RAM)

**Statut :** Rien n'existe.  
**Gap :** 128 MB est trop peu pour des tâches de calcul. Utilisable
uniquement comme **relais WebSocket** (proxy entre le coordinateur
et les workers distants) ou pour des tâches de coordination ultra-légères.

**Recommandation :** Pas prioritaire. La valeur ajoutée est marginale.

---

## 3. Ce qui manque dans le scheduler pour router intelligemment

Le scheduler actuel (`domain.py:90-143`) matche sur :
- `runtimes` (le worker supporte-t-il `wasm`, `python` ?)
- `resources.ram_mb` (≥ 128 MB)
- `gpu.supported` (booléen)
- `lifecycle.expected_remaining_sec` (≥ deadline_ms)

**Ce qui manque pour le routage multi-source :**

### 3.1 Distinction GPU Web vs GPU CUDA

Actuellement `gpu.supported: true/false` ne distingue pas WebGPU (navigateur)
de CUDA (Modal/Kaggle). Une tâche `gpu_required: true` pourrait atterrir
sur un navigateur WebGPU alors qu'elle nécessite CUDA.

**Solution :** Ajouter un champ `gpu_type` dans les capabilities.

```python
# Dans le protocole capabilities.resources.gpu :
"gpu": {
    "supported": true,
    "type": "webgpu | cuda | rocm | none",
    "vram_mb": 4096,
    "model": "T4 | A100 | P100 | integrated"
}
```

Et côté tâche (`task_assign`) :
```python
"resources_required": {
    "gpu_required": true,
    "gpu_type": "cuda",       # ← nouveau
    "min_vram_mb": 8000       # ← nouveau
}
```

**Fichiers à modifier :** `domain.py` (SchedulingPolicy.match), `protocol.py` (types).

### 3.2 Préférence de source (always-on > burst > sporadic)

Le scheduler actuel trie par `tasks_in_progress` (idle first) mais ne
distingue pas un worker Oracle (toujours là) d'un navigateur (sporadique).

**Solution :** Pondérer par `availability_profile` déclaré.

```python
# Dans SchedulingPolicy.match() :
AVAILABILITY_WEIGHT = {
    "always_on": 0,
    "scheduled": 1,
    "burst": 2,
    "sporadic": 3,
}
# Plus le poids est bas, plus le worker est préféré
```

**Fichier à modifier :** `domain.py`.

### 3.3 Coût par tâche (crédits gratuits limités)

Modal donne $30/mois. Si on brûle tout en 1 jour, le worker devient
inutilisable pour le reste du mois.

**Solution :** Ajouter un quota par source dans le scheduler.

```python
# Dans config.py :
worker_quotas = {
    "modal": {"max_tasks_per_day": 100, "max_gpu_minutes_per_day": 60},
    "kaggle": {"max_gpu_hours_per_week": 30},
}
```

**Fichiers à créer/modifier :** `config.py`, `scheduler.py`.

---

## 4. Plan d'implémentation — Du plus rentable au plus complexe

### 🔴 P0 — Oracle Cloud Worker (30 min, énorme valeur)

```
Fichier à créer : worker/oracle_worker.py
Contenu : script Python 30 lignes qui importe worker.client,
         se connecte en WS, déclare 12 GB RAM + CPU + mode persistent
Déploiement : systemd service ou docker compose aux côtés du coordinateur
```

C'est le fruit le plus bas. L'infrastructure est déjà là, le worker natif
existe déjà (`worker/client.py`). Juste un script de lancement.

### 🟠 P1 — HuggingFace Spaces Worker (1h)

```
Fichiers : deploy/hf-spaces/Dockerfile, deploy/hf-spaces/app.py
Nécessite : compte HF, création d'un Space Docker
```

16 GB RAM gratuits, légitimes, persistants. Complément parfait à Oracle
(pas de risque idle reclamation sur HF).

### 🟠 P1 — Enrichissement capabilities GPU (30 min)

```
Fichiers : protocol.py, domain.py, scheduler.py
Ajouter : gpu.type, gpu.vram_mb dans capabilities
         gpu_type, min_vram_mb dans task_assign
         routage GPU Web vs CUDA dans le scheduler
```

Prérequis pour Modal et Kaggle. Sans ça, une tâche CUDA peut atterrir
sur un navigateur WebGPU → échec.

### 🟡 P2 — Modal GPU Worker (2h)

```
Fichiers : worker/modal_worker.py
Nécessite : compte Modal, crédits $30/mois
```

Premier GPU gratuit légitime. Ouvre la porte à l'inférence LLM distribuée.

### 🟡 P2 — Kaggle Notebook Worker (1h + doc)

```
Fichiers : worker/kaggle_worker.ipynb
Nécessite : doc utilisateur « lancer ce notebook »
```

30h/semaine de T4 gratuit. Puissant mais manuel uniquement.

### 🟢 P3 — Google Cloud Run Worker (1h)

```
Fichiers : deploy/cloud-run/Dockerfile.worker, worker/cloudrun_worker.py
```

Burst worker pour les pics de charge. 2M requêtes/mois gratuites.

### 🟢 P3 — Quotas par source (1h)

```
Fichiers : config.py, scheduler.py
```

Évite de brûler les crédits gratuits en 1 jour.

---

## 5. Architecture cible — Le « bus de calcul distribué »

```
                        ┌─────────────────────────┐
                        │    Scrapower API REST    │
                        │    /tasks  /blobs  /faas │
                        └───────────┬─────────────┘
                                    │
                        ┌───────────┴─────────────┐
                        │     Coordinator :8777    │
                        │  ┌───────────────────┐   │
                        │  │ Scheduler          │   │
                        │  │  • GPU type routing│   │
                        │  │  • Availability weight│ │
                        │  │  • Quota management │   │
                        │  │  • Reputation score │   │
                        │  └───────────────────┘   │
                        │  ┌───────────────────┐   │
                        │  │ Blob Store (SHA-256)│   │
                        │  │ PostgreSQL / SQLite │   │
                        │  └───────────────────┘   │
                        └──────┬────────┬──────────┘
                               │        │
              ┌────────────────┼────────┼────────────────────┐
              │                │        │                    │
              ▼                ▼        ▼                    ▼
        ┌──────────┐   ┌──────────┐ ┌──────────┐    ┌──────────┐
        │ Toujours │   │  GPU     │ │  Burst   │    │ Sporadique│
        │ available│   │  workers │ │  workers │    │  workers │
        ├──────────┤   ├──────────┤ ├──────────┤    ├──────────┤
        │ Oracle   │   │ Modal    │ │ Cloud Run│    │ Navigateur│
        │ 12 GB    │   │ A100 GPU │ │ 60 min   │    │ 4 GB RAM │
        │          │   │          │ │          │    │ WebGPU   │
        │ HF Spaces│   │ Kaggle   │ │ AWS Lambda│   │ Colab    │
        │ 16 GB    │   │ T4 GPU   │ │ 15 min   │    │ T4 GPU   │
        │          │   │          │ │          │    │ (manuel) │
        │ Embedded │   │          │ │          │    │          │
        │ localhost│   │          │ │          │    │          │
        └──────────┘   └──────────┘ └──────────┘    └──────────┘
              │                │        │                    │
              └────────────────┴────────┴────────────────────┘
                               │
                     Tous parlent WebSocket
                     Mode A (persistent) ou Mode B (HTTP pull)
```

Le scheduler route selon :
1. **Runtime** (`wasm` → navigateur/embedded, `python` → Oracle/Modal/HF)
2. **GPU type** (`cuda` → Modal/Kaggle, `webgpu` → navigateur, `none` → CPU)
3. **Disponibilité** (`always_on` préféré à `sporadic`)
4. **Réputation** (score > 0 préféré, blacklistés exclus)
5. **Quota restant** (Modal: max N tâches/jour, Kaggle: max 30h/semaine)

---

## 6. Réponse à la question « peut-on agréger en un seul serveur ? »

**Non, pas au sens d'un VPS avec RAM unifiée et filesystem cohérent.**
C'est impossible avec des workers éphémères à 50ms de latence.

**Oui, au sens d'un bus de calcul unifié.** On peut présenter une API
unique (`/tasks`, `/blobs`, `/faas`) qui route automatiquement vers
le meilleur worker disponible selon le type de tâche, sans que
l'utilisateur sache ou se soucie de quelle ressource physique exécute
son calcul.

C'est le modèle **AWS Lambda + S3 gratuit et distribué** — pas
un « serveur », mais une **plateforme de calcul élastique**.

---

## 7. Prochaine action recommandée

Créer le **Oracle Cloud worker** (`worker/oracle_worker.py`) et l'ajouter
au `docker-compose.yml` comme service aux côtés du coordinateur.

**Pourquoi en premier :**
- Infrastructure déjà en place (le serveur tourne)
- Zéro risque ToS
- 12 GB RAM + 10 TB egress = capacité énorme
- 30 minutes de travail
- Débloque immédiatement des tâches Python lourdes (pandas, numpy)
  sans dépendre des navigateurs éphémères
