# Bugs & Technical Debt — Scrapower

Découvert pendant l'audit du harvester (2026-06-23). Non bloquants pour v0.

---

## 🔴 Corrigés (cette session)

| # | Problème | Fichier |
|---|---------|----------|
| ~~C1~~ | `NameError: name 'started' is not defined` dans `remaining_pct()` | `modal.py` |
| ~~B1~~ | `COOLDOWN_SEC` Modal 120→60s + log debug cooldown/max concurrent | `modal.py` |
| ~~B1b~~ | Kaggle `launch_worker` n'avait PAS de cooldown check | `kaggle.py` |
| ~~R1~~ | Rate limit pull partagé (Docker NAT) → 429 abusifs. Fix: dual-mode auth | `http_handler.py`, `worker.py`, `sworker.ipynb` |
| ~~W2~~ | `GET /tasks/{id}` ne renvoyait ni `error`, ni `has_logs`, ni `logs_url` | `db.py`, `task_manager.py`, `domain.py`, `client_api.py` |
| ~~R2~~ | Pas de retry après 5xx. Fix: 3x retry backoff 1s/2s/4s dans les workers | `deploy/modal/worker.py`, `deploy/kaggle/sworker.ipynb` |
| ~~H1~~ | Budget Modal reset au reboot. Fix: persistance dans `kv_store` DB | `modal.py`, `main.py` |

## 🟡 Harvester

| # | Problème | Sévérité | Fichier |
|---|---------|----------|---------|
| H2 | Pas de reset mensuel du budget Modal ($30/mois) | Moyen | `modal.py` |
| H3 | `_sandbox_started` peut accumuler des entrées orphelines | Faible | `modal.py` |
| H4 | `_count_queued()` couplage fragile avec `rmod.task_manager` | Faible | `ephemeral.py` |
| H5 | Pas de log quand `cleanup_stale()` réussit sans rien nettoyer | Faible | `modal.py`, `kaggle.py` |
| H6 | Caddy 502 aléatoires. R2 masque le symptôme. Root cause à confirmer. | Moyen | infra |

## 🟡 Worker tracking

| # | Problème | Sévérité | Fichier |
|---|---------|----------|---------|
| W1 | Logs workers en append-only sans rotation | Faible | `http_handler.py` |
| W3 | Mode A (WS) : `zombie_watchdog` et `requeue_stale` parallèles | Faible | `session.py`, `domain.py` |

## 🟢 Améliorations

| # | Idée | Priorité | Statut |
|---|------|----------|--------|
| A1 | `remaining_pct()` Modal utilise `modal.billing` (API dispo) | P1 | ✅ Fait |
| A2 | `remaining_pct()` Kaggle fait un subprocess à chaque tick | Accepté | — |
| A3 | `COOLDOWN_SEC` Modal 120→60s | P1 | ✅ Fait |
| A4 | Fallback `exit_code=2` à retirer une fois WireGuard stable | P2 | — |
