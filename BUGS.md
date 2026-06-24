# Bugs & Technical Debt — Scrapower

---

## 🔴 Corrigés (session 2026-06-24)

| # | Problème | Fichier |
|---|---------|----------|
| ~~C1~~ | `NameError: started` dans `remaining_pct()` | `modal.py` |
| ~~B1~~ | `COOLDOWN_SEC` 120→60s + log debug cooldown/max | `modal.py`, `kaggle.py` |
| ~~B1b~~ | Kaggle `launch_worker` sans cooldown check | `kaggle.py` |
| ~~R1~~ | Rate limit pull partagé → dual-mode auth (worker_id/IP) | `http_handler.py`, `worker.py`, `sworker.ipynb` |
| ~~W2~~ | `error` + `has_logs` + `logs_url` dans `GET /tasks/{id}` | `db.py`, `task_manager.py`, `domain.py`, `client_api.py` |
| ~~R2~~ | Retry 5xx backoff 1s/2s/4s dans les workers | `worker.py`, `sworker.ipynb` |
| ~~H1~~ | Budget Modal reset reboot → persist `kv_store` DB | `modal.py`, `main.py` |
| ~~H2~~ | Dérive mensuelle budget → tracking local supprimé | `modal.py` |
| ~~H3~~ | `_sandbox_started` orphelin → tracking local supprimé | `modal.py` |
| ~~Refactor~~ | Archi 3 couches: task_type, _match_capabilities, API unifiée | `db.py`, `task_manager.py`, `domain.py`, `http_handler.py`, `client_api.py`, `transcribe_api.py`, `worker.py`, `sworker.ipynb` |

## 🟡 Restant

| # | Sévérité | Problème | Fichier |
|---|----------|---------|----------|
| H4 | Faible | `_count_queued()` couplage fragile | `ephemeral.py` |
| H5 | Faible | Pas de log cleanup vide | `modal.py`, `kaggle.py` |
| H6 | Moyen | Caddy 502 (R2 masque, root cause à confirmer) | infra |
| W1 | Faible | Logs workers sans rotation | `http_handler.py` |
| W3 | Faible | Zombie watchdog + requeue parallèles | `session.py`, `domain.py` |

## 🟢 Nouveaux (session 2026-06-24)

| # | Problème | Fichier |
|---|---------|----------|
| N1 | Harvester log "launch failed" sur cooldown/max (pas une erreur réelle) | `ephemeral.py` |
| N2 | `HF_HUB_ENABLE_HF_TRANSFER` déprécié → `HF_XET_HIGH_PERFORMANCE` | `modal.py` |
| N3 | `yt-dlp-ejs` installé dans sandbox Modal, jamais utilisé (dead dep) | `modal.py` |
