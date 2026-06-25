# Bugs & Technical Debt — Scrapower

---

## 🔴 Corrigés (session 2026-06-24/25)

| # | Bug | Fichiers |
|---|-----|----------|
| C1 | HF Space `CONFIG_ERROR` : collision variables/secrets | `hf_spaces.py` |
| C2 | Caddy `502` : `reverse_proxy 172.17.0.1` → mauvaise gateway Docker | `/opt/ghost/Caddyfile` |
| C3 | Harvester bloqué sur HF pour tâches GPU | `hf_spaces.py` |
| C4 | Redeploy HF Space inutile à chaque restart | `hf_spaces.py` |
| C5 | `output_hash` mismatch worker↔blob store → `BLOB_NOT_FOUND` | `worker.py`, `app.py`, `sworker.ipynb` |
| C6 | Worker HF `completed: 0` — conséquence de C5 | `app.py` |
| S1 | P0 — Coordinator n'a pas vérifié blob avant submit → `rowcount` check | `task_manager.py` |
| B2 | `_ensure_secrets()` appelé à chaque restart → retiré de Path A | `hf_spaces.py` |
| B3 | `workers_active` HF via `SessionManager` + `_touch_starting` + `_ping_worker` | `session.py`, `http_handler.py`, `router.py`, `hf_spaces.py`, `main.py` |
| B4 | Caddy fix hors repo → `deploy/caddy/scrapower.conf` + README | `deploy/caddy/`, `README.md` |
| B5 | Fichiers inutiles dans le bundle HF Space → retiré `COPY worker/` | `Dockerfile`, `hf_spaces.py` |
| B7 | `_wake_space()` URL dérivée manuellement → `space_info().host` | `hf_spaces.py` |
| H1 | Worker ne retentait pas le submit → retry ×3 upload+submit | `app.py`, `worker.py`, `sworker.ipynb` |
| B8/B9 | Rate limit pull : fuite mémoire + anonyme → cleanup auto + 401 | `http_handler.py` |
| B11 | `requeue_stale()` bypass `transition()` → utilise `transition(TIMEOUT)` | `domain.py` |
| B13 | 6 tables DB mortes → DROP migration | `db.py` |
| B14 | Kaggle dead code `_get_quota` → supprimé | `kaggle.py` |
| B15 | Modal `os.environ` race condition → `Client.from_credentials` | `modal.py` |

**18 bugs corrigés.**

---

## 🟢 Restant (non bloquant)

| # | Problème | Fichier |
|---|---------|----------|
| B10 | `workers_active` surcompté 90s après restart (1 promesse + 1 pull). Cosmétique. | `hf_spaces.py` |
| B12 | Blob `ref_count` toujours ≥1 → GC lent (6h, TTL 7j). 742 MB. Acceptable. | `blob_store.py`, `domain.py` |

---

## ⚠️ Compromis assumés

| # | Compromis | Risque |
|---|-----------|--------|
| W4 | Space HF public — health endpoint expose l'URL coordinator | Faible |
| W6 | `launch_worker()` → `False` pour Space RUNNING | Accepté |

---

## 🔮 Hors scope

| # | Idée |
|---|------|
| H2 | `ProviderStatus.workers_starting` + lifecycle standardisé |
| H3 | Monitoring worker persistant (table `workers`) |

---

## 🔒 Sécurité (corrigé)

| # | Problème | Fichier |
|---|---------|----------|
| ~~A9~~ | Tokens Modal en clair | `scripts/modal_proxy_diag.py` |
| ~~A10~~ | Password WG en clair | `deploy/modal/proxy_test.py` |
| ~~N6~~ | Password WG dans logs worker | `whisper_runner.py` |
