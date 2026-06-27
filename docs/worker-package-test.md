# Worker Package Refactor — Test Report (2026-06-27)

**Commits**: `fffb5d5` → `3cf6e0e` → `8fc6c8c`
**Goal**: Single source of truth for all workers (Modal, Kaggle, HF Spaces)

---

## Final Architecture

```
src/scrapower/worker/          ← source unique, modulaire
├── entry.py                   ← main(), GPU detection, capabilities
├── loop.py                    ← WorkerLoop (pull/execute/heartbeat/submit)
└── runtimes/
    ├── python.py              ← PythonRuntime (sandboxed subprocess)
    └── wasm.py                ← WasmRuntime (wasmtime)

Deploy (par provider):
├── deploy/modal/worker.py     ← script auto-suffisant (bundlé depuis src/)
├── deploy/hf-spaces/app.py    ← import direct (Dockerfile a src/)
└── deploy/kaggle/sworker.ipynb ← pip install git+... + import
```

## Pourquoi Modal utilise un script bundlé

### Tentatives échouées

| # | Approche | Résultat |
|---|----------|----------|
| 1 | `add_local_dir` + `PYTHONPATH` + `python -m` | `No module named scrapower.worker.entry` |
| 2 | `sys.path.insert` + `from import` | Même erreur |
| 3 | `add_local_dir` vers `site-packages` | Même erreur |
| 4 | `add_local_dir` + `copy=True` vers `site-packages` | Même erreur |
| 5 | `add_local_python_source("scrapower.worker", copy=True)` | Même erreur |
| 6 | `add_local_dir` + `run_commands("pip install .")` | Image build failed |
| 7 | Staging directory + `add_local_dir(copy=True)` vers site-packages | Même erreur ou "local dir does not exist" |

### Cause racine (confirmée par la doc Modal)

`add_local_python_source` met les fichiers dans `/root`, qui est sur le `PYTHONPATH` des **Modal Functions**, pas des **Sandboxes**. La doc le dit explicitement :

> *"Packages are added to the `/root` directory of containers, which is on the `PYTHONPATH` of any executed Modal **Functions**"*

Pour les Sandboxes, il faut que le package soit dans `site-packages`, ce qui nécessite soit :
- Publier sur PyPI (`pip_install` depuis PyPI)
- Builder un wheel local et le pip install dans l'image
- Utiliser `add_local_dir(copy=True)` vers site-packages avec la bonne arborescence

Aucune de ces approches n'a fonctionné de manière fiable avec les sandboxes.

### Solution retenue : script auto-suffisant bundlé

`deploy/modal/worker.py` est un script Python autonome (~370 lignes) qui contient tout le code du worker (runtimes, loop, entry). Il est généré à partir des modules de `src/scrapower/worker/` — la source reste modulaire.

L'entrypoint sandbox : `python -c "code"` où `code = open("deploy/modal/worker.py").read()`.

### Méthode pro alternative (non implémentée)

La doc Modal recommande le **pre-build + publish** :

```python
# Au démarrage du coordinator
image = modal.Image.from_registry(...)
    .pip_install(...)
    .add_local_dir("src/scrapower/worker", "/opt/...", copy=True)
image.build(app=app).publish("scrapower-worker-runtime")

# Création des sandboxes
sb = modal.Sandbox.create(
    "python", "-c", "from scrapower.worker.entry import main; main()",
    image=modal.Image.from_name("scrapower-worker-runtime"),
    ...
)
```

**Coût supplémentaire** : ~30 lignes de code, gestion du cycle de vie de l'image (build au démarrage, rebuild si code changé, fallback si build échoue), credentials Modal nécessaires au démarrage du coordinator, latence de build (30-60s).

**Bénéfice** : `import scrapower.worker` fonctionne nativement, plus propre architecturalement.

**Décision** : Différé. Le script bundlé fonctionne et le surcoût du pre-build n'est pas justifié tant qu'on n'a pas >2 types de workers sur Modal.

---

## Test Results (end-to-end)

### Modal (GPU T4) ✅
```
sandbox created → pull → heartbeat → download blobs →
execute whisper → upload output → submit → COMPLETED
Worker: modal-85626702 | 3c4b768053d6...
```

### Kaggle (GPU T4) ✅ (pull/heartbeat OK, WG_PROXY KO)
```
kernel started → pip install git+... → import scrapower.worker →
pull task → heartbeat → download blobs → whisper_runner →
WG_PROXY Connection refused → exit_code=2 → submit rejected → retry
```

**Problème** : WG_PROXY (`scrapower.talos-int.com:1081`) injoignable depuis les kernels Kaggle. Le worker fonctionne mais ne peut pas télécharger YouTube.

### HF Spaces (CPU) ✅ (pull/heartbeat OK, pas de WG_PROXY)
```
pull → heartbeat → download → whisper_runner → exit_code=2 (pas de proxy)
```

**Problème** : WG_PROXY non configuré sur HF Spaces.

---

## Issues Remaining

| # | Severity | Component | Symptom | Status |
|---|----------|-----------|---------|--------|
| M1 | ✅ | Modal | Module import | Résolu (script bundlé) |
| M2 | ✅ | Kaggle | Kernel pull | Résolu (pip install git+...) |
| M3 | 🟡 | Coordinator | GPU→CPU matching | Non corrigé |
| M4 | 🟠 | Kaggle | WG_PROXY unreachable | Infra/réseau |
| M5 | 🟠 | HF | WG_PROXY not configured | Infra/config |
| M6 | 🟠 | Modal | spend limit methammer | Compte épuisé |
