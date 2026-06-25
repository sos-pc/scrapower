# Bugs & Technical Debt — Scrapower

---

## 🔴 Corrigés (session 2026-06-24/25)

| # | Bug | Fichiers |
|---|-----|----------|
| C1 | HF Space `CONFIG_ERROR` : collision variables/secrets | `hf_spaces.py` |
| C2 | Caddy `502` : `reverse_proxy 172.17.0.1` → mauvaise gateway Docker | `/opt/ghost/Caddyfile` |
| C3 | Harvester bloqué sur HF pour tâches GPU — `launch_worker()` retournait `True` pour Space RUNNING | `hf_spaces.py` |
| C4 | Redeploy HF Space inutile à chaque restart du coordinator | `hf_spaces.py` |
| C5 | `output_hash` mismatch worker↔blob store → `BLOB_NOT_FOUND` | `worker.py`, `app.py`, `sworker.ipynb` |
| C6 | Worker HF `completed: 0` — conséquence de C5 | `app.py` |
| S1 | **P0** — Coordinator n'a pas vérifié que le blob existe avant d'accepter un submit → tâche fantôme. Fix : `rowcount` check sur l'UPDATE blobs existant. | `task_manager.py` |
| B7 | `_wake_space()` URL dérivée manuellement → utilise `space_info().host` avec cache + fallback | `hf_spaces.py` |
| B5 | Fichiers inutiles bundlés dans le HF Space → retiré `COPY worker/` du Dockerfile + `copytree` de `_first_deploy()` | `Dockerfile`, `hf_spaces.py` |
| B4 | Caddy fix hors repo → snippet `deploy/caddy/scrapower.conf` + doc README | `deploy/caddy/`, `README.md` |
| B2 | `_ensure_secrets()` appelé à chaque restart → retiré de Path A, appelé uniquement au premier déploiement | `hf_spaces.py` |
| B3 | `workers_active` HF basé sur le stage → tracking réel via `SessionManager.touch_mode_b()` + `mode_b_active_count("hf-")` + `_touch_starting()` au lancement + `_ping_worker()` fallback | `session.py`, `http_handler.py`, `router.py`, `hf_spaces.py`, `main.py` |

---

## 🟢 P2 — À faire

| # | Problème | Fichier |
|---|---------|----------|
| **B8** | `_RATE_WINDOW` (rate limit pull) : dict sans TTL par entrée, purge seulement quand > 5000 entrées. Attaque par IPs random → dict gonfle jusqu'à 4999 indéfiniment. | `http_handler.py` |
| **B9** | Pull endpoint accepte les requêtes anonymes à 6/min (backward compat). 500 IPs × 6/min = 3000 req/min, vecteur DoS. | `http_handler.py` |
| **B10** | `workers_active` surcompté temporairement (90s) après un restart : 1 promesse + 1 pull réel coexistent. Cosmétique, pas de sur-lancement. | `hf_spaces.py` |

---

## ⚠️ Compromis assumés (watchlist)

| # | Compromis | Pourquoi | Risque |
|---|-----------|----------|--------|
| **W4** | Space HF public — wake + health check utilisent HTTP GET non authentifié. Le health endpoint expose l'URL du coordinator. | Plus rapide que `restart_space()`. Le coordinator est déjà public. | Faible. |
| **W6** | `launch_worker()` retourne `False` pour Space RUNNING — sémantique : « j'ai fait quelque chose » vs « un worker est dispo ». | Empêche le harvester de boucler sur HF pour les tâches GPU. | Accepté comme design intent. |
| **W7** | ~~Pas de vérification blob au submit~~ → corrigé par S1. | | |

---

## 🔮 Hors scope

### H1 — Le worker ne retente pas le submit après `accepted: False`

Quand `complete()` échoue (token invalide, blob manquant, etc.), le worker ne retente pas. Le task reste `ASSIGNED` jusqu'à `requeue_stale` (10-15 min). Préexistant à S1 — non aggravé.

**Piste** : transition `ASSIGNED → QUEUED` dans `TaskService.complete()` après échec.

### H2 — Cycle de vie worker standardisé dans `WorkerProvider`

`ProviderStatus.workers_starting` + harvester-aware lifecycle. Bénéficierait à tous les providers. Refactor d'architecture.

---

## 🔒 Sécurité (corrigé)

| # | Problème | Fichier |
|---|---------|----------|
| ~~A9~~ | Tokens Modal en clair | `scripts/modal_proxy_diag.py` |
| ~~A10~~ | Password WG en clair | `deploy/modal/proxy_test.py` |
| ~~N6~~ | Password WG dans logs worker | `whisper_runner.py` |
