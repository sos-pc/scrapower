# Session du 2026-06-29 — Cleanup & Bundle Modal

## Résumé

Session dédiée au **nettoyage de la codebase** et à l'**élimination de la
 duplication worker**.  Code mort supprimé, constantes extraites, bundle Modal
 auto-généré, documentation mise à jour.

---

## 1. Nettoyage codebase (v0.7.1)

### Code mort supprimé
- `whisper_runner.py` : `_transcribe_transformers()` + `HF_MODEL_MAP` (112 lignes)
- `loop.py` : `import sys` inutilisé, `Callable` inutilisé
- `python.py` : `import subprocess` inutilisé

### Constantes magiques extraites
- `HEARTBEAT_INTERVAL_SEC = 30` dans `loop.py` et `deploy/modal/worker.py`
- `STDERR_READER_TIMEOUT_SEC = 1800` dans `python.py` et `deploy/modal/worker.py`
- Les hardcodages `1800` et `30` remplacés par les constantes

### Type annotations
- `log_fn: object = None` → `log_fn: Callable[[str], None] | None = None`

### Gestion d'erreurs
- `except Exception: pass` → `log.debug()` dans `kaggle.py` (3 occurences)
- `except Exception: pass` → `log.debug()` dans `modal.py` (2 occurences)
- Commentaire explicatif dans `ephemeral.py`

---

## 2. Bundle Modal auto-généré

### Problème
`deploy/modal/worker.py` était maintenu manuellement : chaque modification
de `loop.py`, `python.py`, `wasm.py`, `entry.py` devait être répercutée à
la main → risque de désynchronisation.

### Solution
`scripts/bundle_modal_worker.py` lit les 4 sources canoniques et génère
un script autonome pour Modal Sandbox. Transformations :
- Strip des docstrings et `from __future__` (HEADER les fournit)
- Suppression des imports relatifs (tout est dans le même fichier)
- Footer Modal avec installation des dépendances et retry

### Workflow
```bash
# Après toute modification des sources worker :
python scripts/bundle_modal_worker.py
git add deploy/modal/worker.py && git commit
```

### Impact
- **Kaggle** : continue avec `pip install git+https://...` (package installé)
- **Modal** : bundle généré depuis les mêmes sources (script autonome)
- **Plus de duplication manuelle** — les deux providers exécutent le même code

---

## 3. Documentation mise à jour

| Fichier | Changement |
|---------|-----------|
| `ARCHITECTURE.md` | ModalHarvester : bundle auto-généré documenté |
| `docs/audit-2026-06.md` | A7 marqué résolu |
| `docs/cleanup-plan-v0.7.1.md` | Statut des passes mis à jour |
| `ROADMAP.md` | Bundle et nettoyage ajoutés à v0.7 |

---

## 4. Prochaines étapes

| # | Tâche |
|---|-------|
| 1 | Tester le bundle généré sur Modal (sandbox réel) | ✅ Fait — transcription tiny OK, circuit complet validé |
| 2 | Extraire `_build_registry()` de `main.py:lifespan()` |
| 3 | Dédupliquer quota stats |

---

# Session du 2026-06-18 — Fondations Scrapower

## Résumé

Session dédiée à **finaliser les fondations de sécurité et de confiance**
avant d'ajouter de nouvelles fonctionnalités.  Trois bugs critiques corrigés,
système de réputation implémenté, roadmap mise à jour.

---

## 1. Corrections de sécurité (3 bugs)

### 1.1 Bypass d'isolation client (`anonymous`)

**Fichier :** `src/scrapower/coordinator/api/client_api.py`

**Problème :** `_check_owner()` ignorait le contrôle de propriété quand le
`X-Client-ID` était absent (défaut = `"anonymous"`).  N'importe qui pouvait
lire/annuler une tâche en omettant le header.

```python
# AVANT (bug)
if task and task.client_id != client_id and client_id != "anonymous":
    raise HTTPException(403)

# APRÈS (corrigé)
if task and task.client_id != client_id:
    raise HTTPException(403)
```

**Impact :** `"anonymous"` est maintenant traité comme un client normal —
il ne peut voir que ses propres tâches (soumises sans `client_id` explicite).

### 1.2 Auth worker : comparaison non constant-time

**Fichier :** `src/scrapower/coordinator/worker_gateway/ws_handler.py`

**Problème :** `_auth_level()` comparait les hash SHA-256 avec `==` au lieu
de `hmac.compare_digest()`, exposant une fuite temporelle théorique.

**Correction :** Aligné sur `security.py` qui utilise déjà `hmac.compare_digest`.

### 1.3 `total_changes` → `cursor.rowcount` (race condition)

**Fichier :** `src/scrapower/coordinator/task_manager.py`

**Problème :** `self._db.total_changes` est cumulatif sur toute la connexion,
pas par statement.  Après la première écriture, la détection de race condition
retournait toujours `True`.

**Correction :** Remplacé par `cursor.rowcount > 0` (lignes 237, 250).
Déjà fait en production avant cette session.

---

## 2. Système de réputation workers (nouveau)

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `reputation.py` | **NOUVEAU** | `ReputationService` : score, blacklist, taux de challenge adaptatif |
| `ws_handler.py` | Modifié | UPSERT worker au `hello`, mise à jour réputation au `task_result` |
| `router.py` | Modifié | Passe `reputation_service` à `handle_ws` |
| `domain.py` | Modifié | `SchedulingPolicy.match()` filtre les blacklistés, trie par réputation |
| `scheduler.py` | Modifié | Précharge les scores, taux de challenge adaptatif |
| `main.py` | Modifié | Instantie `ReputationService`, wiring |

### Formule de scoring

```
Départ      : 0.50 (neutre)
Matched     : score += 0.10 × (1.0 - score)    → asymptotique vers 1.0
Mismatched  : score *= 0.5                       → décroissance rapide
Blacklist   : ≥ 3 mismatches en 1 heure         → exclu du scheduling
```

### Taux de challenge adaptatif

```
challenge_rate = max(0.01, 1.0 - reputation)

score 0.0 → 1.00  (toujours challengé)
score 0.5 → 0.50  (nouveau worker)
score 0.9 → 0.10  (confiance)
score 1.0 → 0.01  (jamais 100% confiance)
```

### Intégration avec le scheduler

- À chaque tick, les scores de réputation sont préchargés pour tous les
  workers actifs
- `SchedulingPolicy.match()` exclut les workers blacklistés (score ≤ 0)
  et trie par réputation décroissante (préfère les workers de confiance)
- Le taux de challenge est calculé par worker, pas globalement

---

## 3. Étude de faisabilité — Serveur unifié

**Fichier :** `research/feasibility-study-unified-server.md`

### Conclusions principales

- **Modèle FaaS distribué** — Scrapower est déjà un « AWS Lambda gratuit »
  avec workers hétérogènes. C'est la bonne direction.
- **WASM = runtime CPU universel** — validé par Dfinity ICP et Fluence.
  Fonctionne déjà sur navigateur, GHA, serveurs.
- **GPU hétérogène** — WebGPU (navigateur) et CUDA (Colab/Kaggle) sont
  deux capacités distinctes, pas unifiables. Le scheduler doit router
  selon le type.
- **Pas de « pool RAM »** — impossible avec des workers éphémères.
  Le théorème CAP l'interdit. Rester sur FaaS + blob store.
- **Pas de « serveur unifié » avec filesystem** — illusion dangereuse.
  Séparer stockage persistant et compute éphémère.

### Recommandation

Positionner Scrapower comme un **bus de calcul distribué** (Lambda + S3
gratuit), pas comme un « serveur unifié ».  C'est honnête, compréhensible,
et techniquement exact.

---

## 4. Roadmap mise à jour

### Phase 4 (v0.4) — 5 items marqués FAIT

- [x] Réputation workers
- [x] Challenge adaptatif
- [x] Isolation client (bypass corrigé)
- [x] Auth worker (vérification stricte)
- [x] Correction `total_changes` → `rowcount`

### Ajouts Phase 4

- [ ] Mode `redundant` — double-exécution 100% (optionnel, documenté)

### Phase 5 renommée — « Scale & Compute Unifié »

- [ ] Runtime LLM (llama.cpp / WebLLM)
- [ ] Runtime Docker (tâches `{"runtime": "docker"}`)
- [ ] Mode FaaS (endpoint `/faas/{func_hash}`)
- [ ] Modal (crédits $30/mois, A100)

### Retiré de la roadmap

- Golem Network → distraction avant v1.0
- Token ERC-20 → friction inutile, contraire à la valeur du projet

---

## 5. Prochaines étapes (priorité)

| # | Tâche | Fichiers |
|---|-------|----------|
| 1 | Déployer sur Oracle | `scp` + `docker compose up -d --build` |
| 2 | Ajouter `busy_timeout` pragma SQLite | `db.py` |
| 3 | Remplacer `secrets.token_urlsafe` → `uuid4` pour session IDs | `session.py` |
| 4 | Documenter risque ToS GitHub Actions dans README | `README.md` |
| 5 | Implémenter Web Crypto / Ed25519 (ADR) | `crypto_utils.py` |
| 6 | Ajouter endpoint `/stats/reputation` | `stats_api.py` |

---

## 6. Commandes de déploiement

```bash
# Copier les fichiers modifiés
scp -i ~/.ssh/clouscard-ghost.key \
  src/scrapower/coordinator/reputation.py \
  src/scrapower/coordinator/api/client_api.py \
  src/scrapower/coordinator/worker_gateway/ws_handler.py \
  src/scrapower/coordinator/worker_gateway/router.py \
  src/scrapower/coordinator/domain.py \
  src/scrapower/coordinator/scheduler.py \
  src/scrapower/coordinator/main.py \
  ubuntu@130.110.242.56:~/scrapower/src/scrapower/coordinator/

# Redémarrer
ssh -i ~/.ssh/clouscard-ghost.key ubuntu@130.110.242.56 \
  "cd ~/scrapower && docker compose up -d --build"
```
