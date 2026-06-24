# Bugs & Technical Debt — Scrapower

---

## 🔴 Corrigés (session 2026-06-24)

| # | Problème |
|---|---------|
| ~~C1~~ | `NameError: started` |
| ~~B1~~ | COOLDOWN 120→60s + logs |
| ~~B1b~~ | Kaggle cooldown manquant |
| ~~R1~~ | Rate limit pull → dual auth |
| ~~W2~~ | error/has_logs/logs_url |
| ~~R2~~ | Retry 5xx workers |
| ~~H1-H3~~ | Budget Modal (persist + simplify) |
| ~~Refactor~~ | Archi 3 couches |
| ~~N1~~ | Launch failed fantôme |
| ~~H6~~ | Caddy 502 (pas un bug) |
| ~~N6~~ | Password WG en clair |
| ~~N2/N3/N5~~ | Quick wins + sandbox |
| ~~H5~~ | Logs cleanup vide |
| ~~H4~~ | Injection task_service |
| ~~N4~~ | Billing API migrate |
| ~~W3~~ | Zombie → requeue bridge |
| ~~W1~~ | Rotation logs 30j |

## 🟡 Audit — Dead code

| # | Problème | Fichier | Lignes |
|---|---------|----------|--------|
| D1 | Ancien harvester + providers (seul `cli harvest` l'appelle) | `harvester/` entier | ~500 |
| D2 | `worker_standalone.py` jamais importé | `cli/worker_standalone.py` | ~100 |
| D3 | `security_middleware.py` jamais importé | `coordinator/security_middleware.py` | ~60 |
| D4 | `router_mod.task_manager` setté mais plus lu | `main.py` | 1 |

## 🟡 Audit — Incohérences

| # | Problème | Fichier |
|---|---------|----------|
| I1 | Deux `WorkerProvider` / `Provider` ABC coexistent | `harvester/providers/base.py` vs `coordinator/harvester/base.py` |
| I2 | `protocol.py` définit messages Mode A, mais Mode B (primaire) ne les utilise pas | `protocol.py` |
| I3 | Chaîne `embedded_worker → worker/client → worker/sandbox → runtimes` pour un worker WASM quasi inactif | `embedded_worker.py`, `worker/client.py`, `worker/sandbox.py` |
