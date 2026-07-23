# Remédiation & déploiement — juillet 2026

> Journal de ce qui a été **corrigé, testé et déployé** en prod le 2026-07-23,
> en réponse à [`audit-2026-07.md`](audit-2026-07.md) (audit détaillé) et à
> `BUGS.md`. Déployé : `9a1eba6` → `507dcb5` (sur `origin/master`, image
> reconstruite depuis un build context nettoyé). Vérifié en production.

## Corrigé et déployé

| Domaine | Détail | Réf. audit |
|---|---|---|
| **Sécurité — auth worker** | `/worker/submit` et `/worker/heartbeat` n'exigeaient **aucune** clé API (n'importe qui pouvait soumettre un résultat, spammer les logs, ou forcer le TIMEOUT de la tâche d'un autre). Path-traversal via `task_id`/`worker_id` dans le nom du fichier de log. → clé exigée sur les 3 endpoints worker (coord + worker + bundle Modal) + validation d'ID + vérif du token sur le chemin d'erreur. | *(hors périmètre de l'audit — `security.py` non fourni)* |
| **Fuite de blobs (disque)** | `ref_count` ne retombait jamais à 0 (store=1 + refs tâche, cleanup ne décrémentait que 1) → le GC ne collectait **jamais**. Modèle refondu : refs possédées par les tâches, `store_blob` à 0, pins pour whisper+cookies, `reconcile_ref_counts()` au démarrage. **Prod : 746 Mo → 2,2 Mo** après reconcile+GC. | §10 (race store_blob) ; B12/juin |
| **workers_active cross-comptes** | comptage du total tous comptes affecté à chaque compte → harvester sous-lance. Corrigé : comptage par compte (Kaggle par préfixe username, Modal par token). | **§2 (P0 #1)** ✓ |
| **`requeue_for_worker()`** | code mort **et** buggé (état `timeout` brut irrécupérable). Supprimé. | **§8 (P1 #11)** ✓ |
| **`complete()` sans rollback** | prétendait rollback sans le faire → `output_hash` fantôme sur tâche ASSIGNED. Vrai `rollback()` ajouté. | — |
| **Message dans `output_hash`** | nettoyage PENDING écrivait dans `output_hash` au lieu de `error`. Corrigé. | — |
| **`asyncio.create_task` fire-and-forget** | tâches de préparation GC-ables en plein vol → référence forte conservée. | — |
| **Doc obsolète** | `ARCHITECTURE.md` : Mode A/WS supprimé, fallback exit_code=2 inexistant, interface `WorkerProvider`, TTL 30j, auth worker. | **§7** ✓ (partiel) |
| **Encodage** | mojibake dans `main.py`/`kaggle.py` + octet cp1252 invalide dans `security.py` réparés ; `.editorconfig` ajouté. | — |
| **Lint** | `ruff check src/` **vert** ; `import os` mort retiré, `cursor` inutile retiré. | §10 (partiel) |

## Vérifié en production (2026-07-23)

- `submit` sans clé → **401** (était 400) — trou d'auth fermé.
- reconcile : **196 blobs orphelins → ref_count 0** ; GC : **746 Mo → 2,2 Mo**.
- whisper + cookies actifs **épinglés** (survivent au GC).
- `/health` et `https://scrapower.talos-int.com/health` → 200. Ghost intact.

## Reste ouvert (de l'audit — non traité)

- **§3.1 (P0 #2)** — `PUT /blobs` lit tout le corps avant auth/taille (DoS mémoire).
- **§9 (P0 #3)** — `httpx` en dépendance `dev` alors que le CLI l'importe ; Dockerfile non pinné ; patch `kagglesdk` silencieux.
- **§1 (P1 #8)** — `get()`/`get_queued()` ne relisent pas `deadline_ms`/`max_retries`/`task_type`.
- **§5 (P1 #4)** — heartbeat `task_valid:false` n'annule pas le subprocess (GPU gaspillé).
- **§4 (P1 #5)** — worker ne vérifie pas les statuts HTTP (blob 404 exécuté, 401 = file vide).
- **§6 (P1 #6/7)** — config TOML jamais montée en prod ; constantes de staleness éparpillées.
- **§3.2–3.7** — rate-limit contournable, cookies/secrets en clair (Kaggle notebook, Makefile).
- **§10** — I/O sync bloquant l'event loop, violations de couche, `cursor=cursor=` restants.
- **mypy** : 9 erreurs préexistantes (Optional streams, backoff int/float, hf_spaces str|None).
- **Tests** : toujours aucun `pytest` (seulement ruff+mypy en CI) — le meilleur investissement.
