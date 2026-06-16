# ADR-009 : Python runtime

## Contexte

Les tâches sont actuellement limitées au format WASM. L'utilisateur doit
écrire du WAT, le compiler en WASM, gérer manuellement la mémoire linéaire.
C'est inutilisable pour l'essentiel des workloads réels : ML, data processing,
web scraping, calcul scientifique.

Ajouter un runtime Python permettrait de soumettre une fonction Python
sérialisée (dill/cloudpickle) qui serait exécutée sur les workers compatibles.

## Avantages

1. **UX massive** : `scrapower.submit(fn, data)` au lieu de WAT+WASM+blobs
2. **ML natif** : PyTorch, NumPy, Pandas, JAX directement sur Colab GPU
3. **Infrastructure existante** : le scheduler supporte déjà le matching runtime
4. **Workers existants** : Colab, Oracle, PC perso ont déjà Python
5. **Zéro friction** : même API, même file d'attente, même protocole

## Risques

| Risque | Impact | Mitigation |
|--------|--------|------------|
| **Sécurité** — Python non sandboxé peut compromettre le worker | 🔴 Critique | Phase 1 solo (trust), Phase 4+ (firejail/Docker) |
| **Dépendances** — pip install par tâche = lent, risqué | 🟠 Majeur | Workers pré-installent les packages communs (numpy, torch) |
| **Portabilité** — pas de Python dans le navigateur | 🟡 Moyen | WASM reste le runtime par défaut ; Python = workers dédiés |
| **Déterminisme** — pas bit-à-bit reproductible | 🟡 Moyen | Acceptable. Le verification game reste pour WASM uniquement |
| **Complexité** — subprocess, timeout, capture stdout | 🟢 Faible | Code existant pour le worker natif |

## Décision

**ON IMPLÉMENTE** avec les contraintes suivantes :

1. **Phase 1 (solo)** : subprocess simple avec timeout de 60s.
   Pas de sandbox fort. Usage par l'utilisateur lui-même uniquement.
   Workers marqués `trusted: true` dans leurs capabilities.

2. **Phase 4+ (communauté)** : firejail ou conteneur Docker éphémère
   par tâche. Réseau bloqué, FS read-only, CPU/memory limits.

3. Le navigateur ne supportera JAMAIS Python. WASM reste le runtime
   universel. Python est un ajout pour les workers serveur/Colab.

## Plan d'implémentation

```
A. worker/runtimes/python.py     — PythonRuntime (subprocess + timeout)
B. WorkerClient modifié          — détection runtime "python" → PythonRuntime
C. trusted flag                  — capabilities.verification.trusted = true
D. Tests                         — soumettre fonction Python, vérifier résultat
E. Documentation                 — exemple submit_python.py
```

## Conséquences

- **Positif** : le système passe de "calcul numérique WASM" à "calcul général"
- **Négatif** : surface d'attaque élargie ; mitigé par usage solo en Phase 1
- **Neutre** : le protocole et le scheduler ne changent pas
