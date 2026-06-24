# Bugs & Technical Debt — Scrapower

---

## 🔴 Corrigés (session 2026-06-24)

19 bugs corrigés. Voir commits.

## 🟡 Audit — Dead code

| # | Statut | Problème | Fichier |
|---|--------|---------|----------|
| D1 | ✅ Fait | Ancien harvester + providers | `harvester/` |
| D2 | ✅ Fait | `worker_standalone.py` jamais importé | `cli/worker_standalone.py` |
| D3 | ✅ Fait | `security_middleware.py` jamais importé | `coordinator/security_middleware.py` |
| D4 | ✅ Fait | `router_mod.task_manager` setté mais plus lu | `main.py` |
| A3 | ⬜ | `pull_rate_limit_per_ip` config morte + `configure_rate_limit()` appel mort | `config.py`, `main.py` |
| A5 | ⬜ | `yt-dlp-ejs` encore dans Dockerfile (retiré de modal.py mais oublié ici) | `Dockerfile` |
| A6 | ⬜ | Deno installé dans Dockerfile, 0 référence dans le code | `Dockerfile` |

## 🟡 Audit — Incohérences

| # | Statut | Problème | Fichier |
|---|--------|---------|----------|
| I1 | ✅ Fait | Deux Provider ABC → résolu par D1 | — |
| I2 | ✅ Fait | `protocol.py` Mode A only, pas Mode B | `protocol.py` |
| I3 | ✅ Fait | Chaîne embedded_worker → conservé (fonctionnel) | — |
| A4 | ⬜ | URL `scrapower.talos-int.com` hardcodée 16 fois | plusieurs |
| A7 | ⬜ | `reputation.py` utilisé seulement par scheduler Mode A (quasi inactif) | `reputation.py` |

## 🟢 Watchlist (pas des bugs, juste à surveiller)

| # | Note | Fichier |
|---|------|---------|
| W1 | `PythonRuntime` dans `python.py` jamais utilisé → gardé comme référence canonique | `worker/runtimes/python.py` |
| W2 | Browser worker (static/) compilé dans Docker mais widget embed peu utilisé | `static/worker.js`, `static/sw.js` |
| W3 | Challenge verification (scheduler) → double exécution, jamais activé en pratique | `scheduler.py` |
