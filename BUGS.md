# Bugs & Technical Debt — Scrapower

Découvert pendant l'audit du harvester (2026-06-23). Non bloquants pour v0.

---

## 🔴 Corrigés (cette session)

| # | Problème | Fichier |
|---|---------|----------|
| ~~C1~~ | `NameError: name 'started' is not defined` dans `remaining_pct()` → crash si `_sandbox_tokens` non vide | `modal.py` L100 |
| ~~B1~~ | `COOLDOWN_SEC` Modal 120→60s + log debug cooldown/max concurrent (plus de "launch failed" fantômes) | `modal.py` |
| ~~B1b~~ | Kaggle `launch_worker` n'avait PAS de cooldown check (`_last_start` jamais utilisé) | `kaggle.py` |
| ~~R1~~ | Rate limit pull partagé (Docker NAT) → 429 abusifs. Fix: dual-mode auth → `worker_id` (30/min) vs IP (6/min survival) | `http_handler.py`, `worker.py`, `sworker.ipynb` |
| ~~W2~~ | `GET /tasks/{id}` ne renvoyait ni `error`, ni `has_logs`, ni `logs_url`. Fix: colonne `error` DB + champs dans la réponse | `db.py`, `task_manager.py`, `domain.py`, `client_api.py` |
| ~~R2~~ | Pas de retry après 5xx. Fix: 3× retry backoff 1s/2s/4s dans les workers | `deploy/modal/worker.py`, `deploy/kaggle/sworker.ipynb` |

## 🟡 Harvester

| # | Problème | Sévérité | Fichier |
|---|---------|----------|---------|
| H1 | `_total_seconds_used` Modal reset au redémarrage coordinator → budget « retrouvé » | Moyen | `modal.py` |
| H2 | Pas de reset mensuel du budget Modal ($30/mois) → `remaining_pct()` diverge après plusieurs mois | Moyen | `modal.py` |
| H3 | `_sandbox_started` peut accumuler des entrées orphelines si `cleanup_stale()` échoue (Modal API down) | Faible | `modal.py` |
| H4 | `_count_queued()` dans EphemeralHarvester utilise `rmod.task_manager` — couplage fragile | Faible | `ephemeral.py` |
| H5 | Pas de log quand `cleanup_stale()` réussit sans rien nettoyer → difficile à debugger | Faible | `modal.py`, `kaggle.py` |
| H6 | Caddy retourne des 502 aléatoires. Hypothèse: race condition keepalive Uvicorn 5s + pool Caddy. R2 masque le symptôme. Root cause à confirmer avec logs Caddy activés. | Moyen | infra |

## 🟡 Worker tracking

| # | Problème | Sévérité | Fichier |
|---|---------|----------|---------|
| W1 | Logs workers en append-only sans rotation → `data/logs/{id}.log` croît indéfiniment | Faible | `http_handler.py` |
| W3 | Mode A (WS) : `zombie_watchdog` et `requeue_stale` sont deux systèmes parallèles qui ne communiquent pas | Faible | `session.py`, `domain.py` |

## 🟢 Améliorations

| # | Idée | Priorité | Statut |
|---|------|----------|--------|
| A1 | `remaining_pct()` Modal utilise `modal.billing` (API dispo) | P1 | ✅ Fait |
| A2 | `remaining_pct()` Kaggle fait un subprocess à chaque tick → lent mais nécessaire | Accepté | — |
| A3 | `COOLDOWN_SEC` Modal 120→60s | P1 | ✅ Fait |
| A4 | Fallback `exit_code=2` (coordinator DL) à retirer une fois WireGuard stable | P2 | — |
