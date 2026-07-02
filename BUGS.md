# Bugs & Dette technique — Audit 2026-06-29

> Fichier local, exclu de Git. Examné un par un avant correction.

---

## 🔴 P0 — Critique

### 1. `wasm.py` — WASM jamais exécuté (stub)
- **Fichier** : `src/scrapower/worker/runtimes/wasm.py`
- **Problème** : `execute_wasm()` charge le module via wasmtime (Engine, Store, Module, Memory) mais n'appelle **aucune fonction exportée**. Le « résultat » est un double SHA256 de l'input. Aucun code WASM n'est jamais exécuté.
- **Impact** : Toute tâche `runtime=wasm` produit un résultat factice.
- **Action** : Soit implémenter l'appel à `compute()`, soit supprimer le runtime WASM s'il n'est pas utilisé en production.

### 2. `config.py:153` — KeyError si sections TOML mal agencées
- **Fichier** : `src/scrapower/coordinator/config.py`
- **Ligne** : 153
- **Problème** : `sec = data["security"]` est à l'intérieur du bloc `if "worker_gateway" in data:`. Si `[worker_gateway]` présent mais `[security]` absent → `KeyError`. Si `[security]` présent mais `[worker_gateway]` absent → config silencieusement ignorée.
- **Impact** : Crash au démarrage ou config partielle.
- **Action** : Sortir la lecture de `security` hors du bloc `worker_gateway`.

### 3. `main.py:377` — `_cleanup_loop()` code mort
- **Fichier** : `src/scrapower/coordinator/main.py`
- **Lignes** : 377-386
- **Problème** : `_cleanup_loop()` est définie mais jamais appelée. `_maintenance_loop()` fait déjà le cleanup.
- **Impact** : Aucun (code mort inoffensif), mais confusion.
- **Action** : Supprimer `_cleanup_loop()`.

### 4. `main.py:37-38` — Variables globales `config` et `db`
- **Fichier** : `src/scrapower/coordinator/main.py`
- **Lignes** : 37-38
- **Problème** : `config` et `db` déclarés comme variables globales, utilisés par les endpoints `/blobs`. Devraient être sur `app.state`.
- **Impact** : Anti-pattern, risque en contexte multi-thread.
- **Action** : Déplacer sur `app.state` (déjà fait pour `task_service`, `registry`, `providers`).

### 5. `ephemeral.py` — `workers_active` toujours à 0
- **Fichier** : `src/scrapower/coordinator/harvester/ephemeral.py` + `accounts.py`
- **Problème** : `Account.update_workers()` n'est **jamais appelé**. La condition `total_active >= queued` est toujours `0 >= N` → jamais vraie. La régulation de capacité est inopérante.
- **Impact** : Le harvester tente toujours de lancer des workers même s'il y en a déjà assez. Atténué par les cooldowns des providers, mais gaspillage potentiel de quota.
- **Action** : Appeler `update_workers()` dans le harvester après chaque lancement/cleanup.

### 6. `kaggle.py:315` — `return` dans la boucle `for`
- **Fichier** : `src/scrapower/coordinator/harvester/kaggle.py`
- **Ligne** : 315
- **Problème** : Dans `_count_active_kernels()`, le `return active` est à l'intérieur de la boucle `for account in accounts`. La fonction retourne après avoir traité uniquement le premier compte.
- **Impact** : Les kernels des autres comptes sont ignorés. Le harvester sous-estime le nombre de workers actifs.
- **Action** : Déplacer `return active` après la boucle.

### 7. `kaggle.py:259` — `datetime.UTC` incompatible Python < 3.11
- **Fichier** : `src/scrapower/coordinator/harvester/kaggle.py`
- **Ligne** : 259
- **Problème** : `from datetime import UTC` n'existe qu'à partir de Python 3.11. Crash sur versions antérieures.
- **Impact** : Incompatibilité si le worker tourne sur Python 3.10 (peu probable en pratique, le Dockerfile utilise 3.12).
- **Action** : Remplacer par `datetime.timezone.utc` (compatible 3.9+).

### 8. `python.py` — `PythonRuntime.execute` perd `log_fn`
- **Fichier** : `src/scrapower/worker/runtimes/python.py`
- **Lignes** : 120-121
- **Problème** : `PythonRuntime.execute()` appelle `execute_python()` **sans** passer `log_fn`. Quand le `WorkerLoop` utilise la classe au lieu de la fonction directe, le streaming stderr est perdu.
- **Impact** : Pas de logs temps réel pour les tâches Python exécutées via la classe.
- **Action** : Ajouter `log_fn` en paramètre de `PythonRuntime.execute()`.

---

## 🟡 P1 — Important

### 9. `accounts.py` — `update_workers()` jamais appelé
- Voir P0 #5.

### 10. `client_api.py` — Paramètre `require_auth` mort
- **Fichier** : `src/scrapower/coordinator/api/client_api.py`
- **Problème** : `create_client_router(require_auth: Callable | None)` — le paramètre n'est jamais utilisé dans le corps. La méthode `_check_auth` interne est toujours appelée.
- **Action** : Supprimer le paramètre.

### 11. `client_api.py` + 3 autres — `"data/logs"` dupliqué ×4
- **Fichiers** : `client_api.py` (×2), `domain.py:240`, `http_handler.py:280`
- **Problème** : Le chemin `"data/logs"` est hardcodé dans 4 fichiers.
- **Action** : Centraliser dans `config.py` (`config.log_dir`).

### 12. `stats_api.py` — Couplage au singleton `router.py`
- **Fichier** : `src/scrapower/coordinator/api/stats_api.py`
- **Problème** : `getattr(router_mod, "session_manager", None)` — repose sur le singleton module-level injecté par `main.py`.
- **Action** : Passer `session_manager` via `app.state` ou en paramètre.

### 13. `transcribe_api.py` — Fallback hash silencieux
- **Fichier** : `src/scrapower/coordinator/api/transcribe_api.py`
- **Ligne** : 34
- **Problème** : Si `whisper_runner.py` n'existe pas, `hashlib.sha256(b"").hexdigest()` est utilisé → hash invalide, échec silencieux au runtime.
- **Action** : Lever une exception explicite si le fichier est introuvable.

### 14. `transcribe_api.py` — Mutation `os.environ`
- **Fichier** : `src/scrapower/coordinator/api/transcribe_api.py`
- **Ligne** : 158
- **Problème** : `os.environ["SCRAPOWER_YT_COOKIES_HASH"] = new_hash` — mutation d'état global, risque en concurrence.
- **Action** : Passer la valeur via la tâche (input_data) plutôt que via l'environnement.

### 15. `transcribe_api.py` — Chemin fragile vers `whisper_runner.py`
- **Fichier** : `src/scrapower/coordinator/api/transcribe_api.py`
- **Lignes** : 22-23
- **Problème** : `Path(__file__).parent.parent.parent.parent / "worker" / "runtimes"` — 4 niveaux de `parent`, cassera si l'arborescence change.
- **Action** : Mettre dans `config.py`.

### 16. `blob_store.py` — `db` inutilisé dans `get_blob` et `blob_exists`
- **Fichier** : `src/scrapower/coordinator/blob_store.py`
- **Lignes** : 76, 88
- **Problème** : Les fonctions acceptent `db` en paramètre mais ne l'utilisent pas. Signature trompeuse.
- **Action** : Supprimer le paramètre ou l'utiliser.

### 17. `blob_store.py` — `run_gc` dupliqué
- **Fichier** : `src/scrapower/coordinator/blob_store.py`
- **Problème** : Deux passes quasi identiques (checkpoint vs regular).
- **Action** : Factoriser.

### 18. `db.py` — Migrations fragiles
- **Fichier** : `src/scrapower/coordinator/db.py`
- **Problème** : Les migrations sont ré-exécutées à chaque démarrage avec `try/except: pass`. Pas de table `schema_version`. Erreurs silencieusement avalées.
- **Action** : Ajouter une table `schema_version`, ne jouer que les migrations non appliquées.

### 19. `domain.py` — Violation de couche (×6)
- **Fichier** : `src/scrapower/coordinator/domain.py`
- **Problème** : `TaskService` contourne `TaskManager` et accède directement à `self._tm._db` pour des écritures SQL dans 6 méthodes (`set_queued`, `mark_failed`, `count_queued`, `requeue_stale`, `requeue_for_worker`, `cleanup_expired`).
- **Action** : Déplacer ces opérations dans `TaskManager`.

### 20. `domain.py` — Imports `import time` lazy ×6
- **Fichier** : `src/scrapower/coordinator/domain.py`
- **Problème** : `import time` répété dans 5 méthodes + `import asyncio` dans `run_prepare`.
- **Action** : Mettre au niveau module.

### 21. `task_manager.py` — `cursor = cursor =` (triple affectation)
- **Fichier** : `src/scrapower/coordinator/task_manager.py`
- **Lignes** : 164, 191, 280
- **Problème** : `cursor = cursor = await...` — double affectation sans effet.
- **Action** : Nettoyer.

### 22. `task_manager.py` — Champs omis dans `get_queued`
- **Fichier** : `src/scrapower/coordinator/task_manager.py`
- **Lignes** : 190-220
- **Problème** : Omet `definition_json`, `current_assignment_token`, `assigned_worker_id`, `assigned_at`, `output_hash`, `deadline_ms`, `max_retries`.
- **Action** : Aligner avec `get()`.

### 23. `task_manager.py` — Timestamps en string
- **Fichier** : `src/scrapower/coordinator/task_manager.py`
- **Problème** : `str(time.time())` stocké en TEXT. Comparaisons SQL non numériques.
- **Action** : Utiliser `REAL` ou `INTEGER`.

### 24. `hf_spaces.py` — `_ping_worker` code mort
- **Fichier** : `src/scrapower/coordinator/harvester/hf_spaces.py`
- **Lignes** : 272-292
- **Action** : Supprimer.

### 25. `hf_spaces.py` — Crash si dossier de déploiement absent
- **Fichier** : `src/scrapower/coordinator/harvester/hf_spaces.py`
- **Problème** : `_find_deploy_dir` lève `FileNotFoundError` → crash au démarrage.
- **Action** : Gérer l'absence gracieusement.

### 26. `modal.py` — I/O synchrone dans l'event loop
- **Fichier** : `src/scrapower/coordinator/harvester/modal.py`
- **Problème** : `sqlite3` (bloquant) dans `_load_state`/`_save_state`, `open()` dans `_create_sandbox`.
- **Action** : Utiliser `aiosqlite` ou `asyncio.to_thread`.

### 27. `http_handler.py` + `domain.py` — Accès direct `_tm._db`
- Voir P1 #19.

### 28. `router.py` — `task_manager` code mort
- **Fichier** : `src/scrapower/coordinator/worker_gateway/router.py`
- **Ligne** : 15
- **Problème** : `task_manager = None` — défini mais jamais référencé.
- **Action** : Supprimer.

### 29. `router.py` — Singletons module-level
- **Fichier** : `src/scrapower/coordinator/worker_gateway/router.py`
- **Lignes** : 14-16
- **Problème** : `session_manager`, `task_service` injectés depuis `main.py` comme singletons globaux.
- **Action** : Utiliser `app.state` ou l'injection de dépendances FastAPI.

### 30. `session.py` — Paramètres inutilisés
- **Fichier** : `src/scrapower/coordinator/worker_gateway/session.py`
- **Ligne** : 15
- **Problème** : `heartbeat_interval_sec` et `heartbeat_miss_threshold` acceptés mais jamais stockés ni utilisés. `max_age_sec=90` hardcodé.
- **Action** : Dériver `max_age_sec` de `heartbeat_interval_sec * heartbeat_miss_threshold`.

### 31. `entry.py` — Code mort + import dupliqué + valeurs magiques
- **Fichier** : `src/scrapower/worker/entry.py`
- **Problèmes** :
  - Lignes 82-83 : double assignation de `max_lifetime_sec` (no-op)
  - Ligne 114 : `import os as _os_diag` alors que `os` déjà importé
  - Valeurs magiques : 16384, 21600, 900000, etc.
- **Action** : Nettoyer + extraire des constantes.

### 32. `loop.py` — Import mort + constante inutilisée + `print()` nu
- **Fichier** : `src/scrapower/worker/loop.py`
- **Problèmes** :
  - `import json as _json` — jamais utilisé (aiohttp fait le parsing)
  - `STDERR_READER_TIMEOUT_SEC` défini ici mais seul `python.py` l'utilise
  - Ligne 195 : `print(..., flush=True)` au lieu de `self._log()` — casse l'uniformité
- **Action** : Nettoyer.

### 33. `whisper_runner.py` — Fallback transformers absent + DRY violé
- **Fichier** : `src/scrapower/worker/runtimes/whisper_runner.py`
- **Problèmes** :
  - Docstring annonce un fallback `transformers` pour Kaggle mais aucun code ne l'implémente
  - Triplon de sérialisation d'erreur (lignes 224-241)
  - Filtrage fragile des arguments yt-dlp (lignes 71-78)
- **Action** : Supprimer la docstring trompeuse, factoriser la sérialisation.

---

## 🔵 P2 — Cosmétique

### 34. `cors_middleware.py` — `ALLOWED_ORIGINS` mort
- Défini ligne 13, jamais utilisé (le middleware hardcode `b"*"`).

### 35. `security.py` — `import hmac` lazy + docstrings corrompus
- `import hmac` dans `verify_api_key` au lieu du niveau module.
- Caractères `ā€"` au lieu de `—` dans les docstrings.

### 36. `harvester/base.py` — Types manquants
- `registry` non typé dans `refresh_quota`, `cleanup_stale`, `status`.

### 37. `domain.py:149` — Docstring en français
- `"""Nombre de tâches en attente..."""` — incohérent avec le reste.

### 38. `main.py` — Versions inconsistantes
- `"0.7.1"` (homepage) vs `"0.1.0"` (health).

### 39. `worker/__init__.py` — Docstring trompeuse
- Parle de `from scrapower.worker.entry import main` mais exporte `WorkerLoop`.

### 40. `worker/runtimes/__init__.py` — Docstring incorrecte
- Dit que les runtimes retournent un dict, mais ils retournent un tuple.

---

## 📊 Stats

| Priorité | Nombre |
|----------|--------|
| P0 | 8 |
| P1 | 25 |
| P2 | 9 |
| **Total** | **42** |
