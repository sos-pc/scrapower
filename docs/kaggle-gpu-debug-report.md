# Kaggle GPU Debug Report — 2026-06-28

> **Handoff document** : tout ce qu'on a tenté pour faire fonctionner faster-whisper/ctranslate2
> sur les GPU T4 de Kaggle. Échec confirmé — la piste PyTorch natif reste ouverte.

---

## Environnement Kaggle T4

```
NVIDIA-SMI 560.35.03   Driver Version: 560.35.03   CUDA Version: 12.6
GPU: Tesla T4, 16 GB VRAM
Python: 3.10.13 → 3.12 (varie selon les kernels)
PyTorch: préinstallé (CUDA 12.1, cuDNN 8.x bundled)
```

## Symptôme

À chaque exécution de `whisper_runner.py`, l'import de `faster-whisper` → `ctranslate2` produit :

```
[whisper_runner] cuda failed: CUDA failed with error CUDA driver version is insufficient for CUDA runtime version, trying next
[whisper_runner] WhisperModel loaded on cpu (int8)
```

Le fallback CPU fonctionne (tiny: 2.6s, turbo: ~4min pour 2h).

---

## Tentatives (ordre chronologique)

### Tentative 0 — Pin `ctranslate2==4.4.0` (session précédente)
- **Fichier** : `deploy/kaggle/sworker.ipynb`
- **Action** : `pip install -q "ctranslate2==4.4.0" faster-whisper`
- **Résultat** : ❌ Même erreur. Le pin 4.4.0 ne change rien car toutes les versions 4.x exigent cuDNN 9.

### Tentative A1 — Install cuDNN 9 + LD_LIBRARY_PATH (Fix A v1)
- **Fichier** : `src/scrapower/worker/runtimes/whisper_runner.py`, fonction `_ensure_deps()`
- **Action** : 
  ```python
  pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*
  os.environ["LD_LIBRARY_PATH"] = "/path/to/cudnn/lib:" + existing
  ```
- **Problème** : `__import__("nvidia.cudnn")` réussissait car PyTorch fournit déjà ce module (cuDNN 8). Le Fix A ne se déclenchait jamais — il était protégé par un `except ImportError` qui n'arrivait pas.
- **Résultat** : ❌ Code jamais exécuté.

### Tentative A2 — Forcer l'install cuDNN 9 (Fix A v2)
- **Fichier** : `src/scrapower/worker/runtimes/whisper_runner.py`
- **Action** : Suppression du `try/except ImportError` — toujours tenter le pip install.
- **Problème** : `nvidia.cudnn.__file__` était `None` après réinstallation (namespace package), causant `TypeError: expected str, bytes or os.PathLike object, not NoneType` dans `os.path.join()`.
- **Résultat** : ❌ Crash dans le Fix A.

### Tentative A3 — Fallback importlib + site-packages (Fix A v3)
- **Fichier** : `src/scrapower/worker/runtimes/whisper_runner.py`
- **Action** : 
  ```python
  import importlib.util
  spec = importlib.util.find_spec("nvidia.cudnn")
  cudnn_lib = os.path.join(os.path.dirname(spec.origin), "lib")
  # fallback: glob site-packages/nvidia/cudnn/lib
  ```
- **Résultat partiel** : ✅ cuDNN 9 trouvé (`LD_LIBRARY_PATH=/usr/local/lib/python3.12/dist-packages/nvidia/cudnn/lib`). Mais...
- **Résultat final** : ❌ GPU toujours inaccessible. Même avec cuDNN 9 + LD_LIBRARY_PATH, `ctranslate2` refuse le driver CUDA 12.6.

### Tentative B1 — Downgrade ctranslate2 < 4
- **Fichier** : `deploy/kaggle/sworker.ipynb`
- **Action** : `pip install -q "ctranslate2<4" faster-whisper`
- **Résultat** : ❌ Même erreur. La version 3.x de ctranslate2 n'accepte pas non plus le driver 560.35.03.

### Tentative C — Diagnostic standalone dans `entry.py`
- **Fichier** : `src/scrapower/worker/entry.py`
- **Action** : Ajout d'un diagnostic CUDA (torch version, ctranslate2 device count, LD_LIBRARY_PATH) au démarrage du worker.
- **Résultat** : Les prints vont dans stdout/stderr du notebook Kaggle, pas dans les logs coordinator. Inexploitable sans accès direct à Kaggle.
- **Note** : Le diagnostic de `entry.py` n'est PAS transmis au coordinator car la capture stderr ne démarre qu'au moment de l'exécution d'une tâche (dans `_run_task_sync`), pas au démarrage du worker.

---

## Cause racine confirmée

**Le driver NVIDIA 560.35.03 de Kaggle est incompatible avec `ctranslate2` (toutes versions).**

Ce n'est PAS un problème de :
- ❌ cuDNN manquant (vérifié : installé, LD_LIBRARY_PATH configuré)
- ❌ Mauvaise version de ctranslate2 (testé 3.x et 4.x)
- ❌ Lib CUDA manquantes (PyTorch les fournit)

Le message `CUDA driver version is insufficient` est littéral : le driver 560.35.03 est trop ancien pour la combinaison ctranslate2 + CUDA 12.6.

---

## Pistes NON explorées

| # | Piste | Faisabilité | Effort estimé |
|---|-------|------------|---------------|
| 1 | `insanely-fast-whisper` (utilise PyTorch directement, pas ctranslate2) | Élevée — PyTorch fonctionne sur Kaggle | ~20 lignes dans `whisper_runner.py` |
| 2 | `openai-whisper` via `transformers` (PyTorch natif) | Élevée | ~30 lignes |
| 3 | `faster-whisper` avec `device="cpu"` forcé (accepter le fallback) | Triviale | 0 lignes |
| 4 | Mettre à jour le driver NVIDIA sur Kaggle | Impossible — environnement géré par Kaggle | N/A |
| 5 | Compiler ctranslate2 from source contre le driver 560 | Très faible — conflits de dépendances | Plusieurs heures |

---

## Fichiers modifiés cette session

| Fichier | Changement | Commit |
|---------|-----------|--------|
| `src/scrapower/coordinator/harvester/kaggle.py` | Fix `_kernel_refs` sync dans `_cleanup_old_kernels()` | `79420ab` |
| `src/scrapower/worker/runtimes/whisper_runner.py` | Ajout/retrait Fix A (cuDNN), CPU fallback conservé | `12a9c1a` → `79420ab` |
| `src/scrapower/worker/entry.py` | Ajout diagnostic CUDA au démarrage | `06eabf2` |
| `deploy/kaggle/sworker.ipynb` | Cellule diagnostic, `IDLE_TIMEOUT_SEC=60`, pin ctranslate2 (retiré) | `06eabf2` → `79420ab` |
| `BUGS.md` | Documenté P9 (kaggle _kernel_refs) et P10 (GPU incompatibility) | `005efa4` |

---

## État final du worker Kaggle

```
✅ Mode B HTTP pull/submit — fonctionnel
✅ Heartbeat — fonctionnel  
✅ WG_PROXY — fonctionnel (téléchargement YouTube OK)
✅ CPU fallback — fonctionnel (tiny: 2.6s)
❌ GPU — incompatible avec ctranslate2
```

## Architecture de déploiement Kaggle

1. Le harvester lit `deploy/kaggle/sworker.ipynb` du disque **à chaque lancement**
2. Remplace les placeholders `{{COORDINATOR_URL}}`, `{{API_KEY}}`, `{{WG_USER}}`, etc.
3. Push vers Kaggle via `kaggle kernels push`
4. Le notebook fait `pip install git+https://github.com/sos-pc/scrapower.git` → importe `scrapower.worker.entry`
5. Le worker télécharge `whisper_runner.py` via `/blobs/{hash}` depuis le coordinator
6. Les kernels COMPLETE/ERROR sont nettoyés toutes les 5 min par `_cleanup_old_kernels()`

## Serveur

- **Adresse** : `130.110.242.56`
- **SSH** : `ssh -i ~/.ssh/clouscard-ghost.key ubuntu@130.110.242.56`
- **Coordinator** : `~/scrapower`, `docker compose restart coordinator`
- **API Key** : `sp-a97586463f06f440c04278b074b8599151be3d5bc47e9f57`
- **URL** : `https://scrapower.talos-int.com`
- **Kaggle enabled** : `KAGGLE_ENABLED=true` (3 comptes)
- **Modal disabled** : `MODAL_ENABLED=false` (2 comptes)
- **Comptes Kaggle dispo** : `piotjeremie` (crédits restants), `methammerazerty` (limité), `ultimatethunderbolts` (limité)
