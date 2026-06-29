# Code Cleanup Plan — v0.7.1

> **Objectif** : réduire la dette technique, éliminer la duplication,
> améliorer la robustesse. Zéro changement fonctionnel.

**Statut global** : ✅ Toutes les passes terminées.

---

## Passe 1 — Nettoyage mécanique ✅ Terminé (commits `239d9af`, `4de13cb`)

### 1.1 Code mort dans `whisper_runner.py` ✅

| Suppression | Lignes | Raison |
|-------------|--------|--------|
| `_transcribe_transformers()` | ~60 | Fallback jamais déclenché depuis fix `os.environ.copy()` |
| `HF_MODEL_MAP` | ~10 | Mapping inutile |
| `_format_segments()` | ~20 | Fusionné dans `_transcribe_faster_whisper` |
| `import torch`, `from transformers import ...` | — | Plus appelé |
| Installation de `transformers` dans `_ensure_deps()` | ~5 | Dépendance inutile |

**Fichier** : `src/scrapower/worker/runtimes/whisper_runner.py` (-112 lignes)

### 1.2 Imports inutilisés ✅

| Fichier | Import retiré | Raison |
|---------|--------------|--------|
| `loop.py` | `import sys` | Plus de swap `sys.stderr` |
| `loop.py` | `from collections.abc import Callable` | Jamais utilisé dans ce fichier |
| `python.py` | `import subprocess` | Remplacé par `asyncio.create_subprocess_exec` |

### 1.3 Uniformiser `log_fn` ✅

| Fichier | Avant | Après |
|---------|-------|-------|
| `python.py` | `log_fn: object = None` | `log_fn: Callable[[str], None] \| None = None` |

### 1.4 Constantes magiques ✅

| Constante | Valeur | Définie dans | Utilisée dans |
|-----------|--------|-------------|---------------|
| `HEARTBEAT_INTERVAL_SEC` | 30 | `loop.py`, `deploy/modal/worker.py` | `asyncio.sleep()`, `WorkerLoop.__init__` |
| `STDERR_READER_TIMEOUT_SEC` | 1800 | `python.py`, `deploy/modal/worker.py` | `asyncio.wait_for()`, messages timeout |

Les hardcodages `1800` et `30` ont été remplacés partout par les constantes.

---

## Passe 2 — Élimination duplication ✅ Terminé

### 2.1 Générer `modal/worker.py` ✅

**Problème** : `deploy/modal/worker.py` était une copie manuelle de `loop.py` +
`python.py` + `wasm.py` + `entry.py`. Chaque modification des sources canoniques
devait être répercutée à la main → désynchronisation garantie.

**Solution** : `scripts/bundle_modal_worker.py` lit les 4 sources canoniques et
génère un bundle autonome. Transformations :
- Strip des docstrings et `from __future__` (fournis par le HEADER)
- Suppression des imports relatifs (`from .runtimes.xxx`, `from .loop`)
- Suppression du `__main__` d'`entry.py` (remplacé par le footer Modal)
- Footer Modal : `pip install` dépendances + `main()` avec retry

**Workflow** :
```bash
python scripts/bundle_modal_worker.py
git add deploy/modal/worker.py && git commit
```

**Fichiers** : `scripts/bundle_modal_worker.py` (nouveau), `deploy/modal/worker.py` (régénéré)

### 2.2 Dédupliquer quota stats ✅

| Actuel | Statut |
|--------|--------|
| `stats_api._get_kaggle_quota()` | Déjà supprimé — lit `AccountRegistry.quota_detail` |
| `stats_api._get_modal_billing()` | Déjà supprimé — lit `AccountRegistry.quota_detail` |

Ces fonctions ont été retirées lors du refactor AccountRegistry (v0.7). `stats_api.py` lit directement `registry.all`.

---

## Passe 3 — Robustesse (partiellement terminé)

### 3.1 Remplacer `except Exception: pass` ✅

Tous les `except Exception: pass` des harvesters remplacés par `log.debug()` :

| Fichier | Changement |
|---------|-----------|
| `kaggle.py` | 3x `except Exception: pass` → `log.debug()` |
| `modal.py` | 2x `except Exception: pass` → `log.debug()` (billing, cleanup) |
| `ephemeral.py` | Commentaire explicatif ajouté |

Restent intacts (légitimes) : `modal.py` `_load_state`/`_save_state` (DB peut ne pas
exister), `main.py` `CancelledError` (pattern asyncio standard).

### 3.2 Extraire `_build_registry()` de `main.py` ✅

`main.py:lifespan()` faisait ~140 lignes de construction → extrait dans `_build_registry()`.

---

## 🚫 Ne pas modifier

- `whisper_runner.py:_download_audio()` — logique métier complexe, testée
- `task_manager.py` — stable
- `domain.py` — stable
- `worker_gateway` — stable
- `client_api.py`, `transcribe_api.py` — stables
