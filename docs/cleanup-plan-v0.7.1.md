# Code Cleanup Plan — v0.7.1

> **Objectif** : réduire la dette technique, éliminer la duplication,
> améliorer la robustesse. Zéro changement fonctionnel.

---

## Passe 1 — Nettoyage mécanique (20 min, zéro risque)

### 1.1 Code mort dans `whisper_runner.py`

| Suppression | Lignes | Raison |
|-------------|--------|--------|
| `_transcribe_transformers()` | ~60 | Fallback jamais déclenché depuis fix `os.environ.copy()` |
| `HF_MODEL_MAP` | ~10 | Mapping inutile |
| `_format_segments()` | ~20 | Fusionné dans `_transcribe_faster_whisper` |
| `import torch`, `from transformers import ...` dans la fonction | — | Plus appelé |
| Installation de `transformers` dans `_ensure_deps()` | ~5 | Dépendance inutile |

**Fichier** : `src/scrapower/worker/runtimes/whisper_runner.py`
**Vérification** : `grep -c '_transcribe_transformers' → 0`

### 1.2 Imports inutilisés

| Fichier | Import | Raison |
|---------|--------|--------|
| `loop.py` | `import sys` | Plus de swap `sys.stderr` |

**Vérification** : `grep -c 'import sys' loop.py → 0`

### 1.3 Uniformiser `log_fn`

| Fichier | Avant | Après |
|---------|-------|-------|
| `python.py:22` | `log_fn: object = None` | `log_fn: Callable[..., None] \| None = None` |
| `loop.py` | implicite | idem |

### 1.4 Constantes magiques

| Constante | Valeur | Fichier |
|-----------|--------|---------|
| `HEARTBEAT_INTERVAL` | 30 | `loop.py` |
| `STDERR_READER_TIMEOUT` | 1800 | `python.py`, `modal/worker.py` |

---

## Passe 2 — Élimination duplication (1h)

### 2.1 Générer `modal/worker.py`

**Problème** : `deploy/modal/worker.py` est une copie manuelle de `loop.py` + `python.py` + `wasm.py` → désynchronisation garantie.

**Solution** : script `scripts/bundle_modal_worker.py` qui lit les sources et génère le bundle.

```python
# scripts/bundle_modal_worker.py
import re
from pathlib import Path

SRC = Path("src/scrapower/worker")
DST = Path("deploy/modal/worker.py")

def bundle():
    parts = []
    for source, section in [
        (SRC / "runtimes" / "python.py", "python"),
        (SRC / "runtimes" / "wasm.py", "wasm"),
        (SRC / "loop.py", "loop"),
        (SRC / "entry.py", "entry"),
    ]:
        code = source.read_text()
        # Remove imports that Modal doesn't need (aiohttp is in image)
        code = re.sub(r'from \.runtimes\.|from \.loop', '# BUNDLED', code)
        parts.append(f"# === BUNDLED: scrapower/worker/{section} ===\n{code}")
    DST.write_text("\n".join(parts))
    print(f"Bundled {len(parts)} modules → {DST}")

if __name__ == "__main__":
    bundle()
```

**Fichiers** : `scripts/bundle_modal_worker.py` (nouveau), `deploy/modal/worker.py` (régénéré)

### 2.2 Dédupliquer quota stats

| Actuel | Cible |
|--------|-------|
| `stats_api._get_kaggle_quota()` | Supprimé, lit `AccountRegistry.quota_detail` |
| `stats_api._get_modal_billing()` | Supprimé, lit `AccountRegistry.quota_detail` |

---

## Passe 3 — Robustesse (30 min)

### 3.1 Remplacer `except Exception: pass`

Dans chaque harvester, remplacer les `except Exception: pass` par `log.exception()` pour ne plus perdre d'erreurs.

| Fichier | Ligne | Remplacement |
|---------|-------|-------------|
| `kaggle.py:328` | `except Exception: pass` | `except Exception: log.exception(...)` |
| `kaggle.py:369` | `except Exception: pass` | idem |
| `modal.py:131` | `except Exception: pass` | `log.warning("modal billing refresh failed")` |
| `modal.py:154` | `except Exception: return 0.0` | `log.warning(...)` |
| `ephemeral.py:72` | `except Exception: pass` | `log.warning(...)` |

### 3.2 Extraire `_build_registry()` de `main.py`

`main.py:lifespan()` fait 90 lignes de construction → extraire dans `_build_registry()`.

---

## 🚫 Ne pas modifier

- `whisper_runner.py:_download_audio()` — logique métier complexe, testée
- `task_manager.py` — stable
- `domain.py` — stable
- `worker_gateway` — stable
- `client_api.py`, `transcribe_api.py` — stables
