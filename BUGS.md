# Bugs & Technical Debt — Scrapower

---

## 🔴 Corrigés (session 2026-06-24)

19 bugs corrigés + 4 dead code modules + 3 incohérences. Voir commits.

## 🟡 À faire

| # | Priorité | Problème | Fichier |
|---|----------|---------|----------|
| A5 | Basse | `yt-dlp-ejs` encore dans Dockerfile | `Dockerfile` L27 |
| A6 | Basse | Deno installé pour rien dans Dockerfile | `Dockerfile` L20-21 |
| A7 | Basse | `reputation.py` 100 lignes utilisées seulement par scheduler Mode A | `reputation.py` |

## 🟢 Watchlist

| # | Note | Fichier |
|---|------|---------|
| W1 | `PythonRuntime` jamais utilisé → gardé comme référence | `worker/runtimes/python.py` |
| W2 | Browser worker compilé, widget embed peu utilisé | `static/` |
| W3 | Challenge verification scheduler jamais activé | `scheduler.py` |

## 🔒 Sécurité (corrigé)

| # | Problème | Fichier |
|---|---------|----------|
| ~~A9~~ | Tokens Modal en clair | `scripts/modal_proxy_diag.py` |
| ~~A10~~ | Password WG en clair | `deploy/modal/proxy_test.py`, `proxy_test_cookies.py` |
| ~~N6~~ | Password WG dans logs worker | `whisper_runner.py` (corrigé + Kaggle notebook) |
