# Bugs & Technical Debt — Scrapower

---

## 🔥 Session 2026-06-26 — Diagnostic batch + heartbeat + refactor Mode A

### Objectif initial
Débugger le cycle de transcription batch (playlist Hegel, vidéos 1-2h).
Les tâches bouclent : transcription OK → submit rejeté → retour en queued →
re-transcription depuis zéro → boucle infinie.

### Chronologie complète

| Étape | Action | Commit | Résultat |
|-------|--------|--------|----------|
| 1 | Ajout logs diagnostic dans `complete()` et `submit()` | `e09b5c3` | ✅ Déployé |
| 2 | Test batch 2 vidéos | — | ❌ Worker Modal tué à 300s (KeyboardInterrupt) |
| 3 | Analyse logs Modal (sandbox piot.jeremie) | — | Transcription 209 segments puis kill externe |
| 4 | Recherche doc Modal | — | GPU Sandboxes = préemptibles ; timeout peut être ignoré |
| 5 | Découverte : logs diagnostic révèlent "token mismatch" | — | `complete rejected: token mismatch db=none` — le submit est rejeté car le token a été invalidé par `requeue_stale()` |
| 6 | P1a+P1b — `remaining_pct()` et `_first_deploy()` gèrent PAUSED/SLEEPING/STOPPED | `9d29370` | ✅ Harvester ne fait plus de redeploy inutile |
| 7 | P1c — `total_active` exclut CPU-only pour tâches GPU | `9e0d6fc` | ✅ Harvester ne bloque plus sur HF |
| 8 | Découverte P3 — les 2 comptes Modal ont spend limit à 0$ | — | L'utilisateur avait mis les limites à 0 par peur d'être débité |
| 9 | L'utilisateur relève la limite du compte piot.jeremie | — | ✅ Modal fonctionne à nouveau |
| 10 | Ajout logs erreur heartbeat (coordinator + worker) | `50b1872` | ✅ `except:pass` remplacé par `log.exception` |
| 11 | Heartbeat : send immédiat (pas de sleep avant 1er envoi) | `db7ae34` | Toujours 0 heartbeat reçu |
| 12 | Heartbeat : session aiohttp dédiée (pas la session partagée) | `dcbaafb` | Toujours 0 heartbeat reçu |
| 13 | Heartbeat : `global _LOG_TASK_ID` manquant → crash Python | `4032c26` | ✅ 1er heartbeat reçu ! |
| 14 | Heartbeat : thread synchrone urllib (bypass event loop) | `f3660cb` | ✅ 3 heartbeats reçus ! Fonctionne ! |
| 15 | Découverte : `task_valid=false` au 1er heartbeat — token déjà invalide | — | Le scheduler Mode A appelle `requeue_stale()` toutes les 5s et invalide le token |
| 16 | Audit complet des dépendances Mode A | — | 8 fichiers à supprimer, 11 à modifier |
| 17 | Plan de suppression Mode A + boucle maintenance unifiée | — | En cours |

### 🎯 Résultat clé : La heartbeat fonctionne

Après 4 itérations (commits `50b1872` → `f3660cb`), la heartbeat Mode B envoie
enfin des requêtes HTTP. Le fix final :
- `global _LOG_TASK_ID` dans `_heartbeat_sync()` (le bug racine)
- `urllib.request` dans un thread dédié (pas aiohttp, pas l'event loop)
- Premier envoi immédiat (pas de sleep 30s avant)
- Annulation heartbeat APRÈS upload+submit (pas dans le finally)

### 🔴 Problèmes découverts et leur résolution

| # | Problème | Root cause | Fix | État |
|---|---------|-----------|-----|------|
| P0 | Modal tue GPU sandboxes à 300s | GPU Sandboxes préemptibles | Checkpoints whisper_runner (planifié) | ⚠️ En attente |
| P1 | Harvester choisit HF (CPU) pour GPU | `remaining_pct()` ment, `_first_deploy()` inutile, `total_active` pas filtré | 3 sous-bugs corrigés | ✅ |
| P3 | Comptes Modal hors budget | Spend limit à 0$ | Relevé sur piot.jeremie | ✅ |
| P4 | Heartbeat 0 requête envoyée | `global _LOG_TASK_ID` manquant + event loop bloqué | urllib + thread + global | ✅ |
| P5 | `requeue_stale()` invalide le token Mode B | Scheduler Mode A appelle `requeue_stale()` toutes les 5s | Supprimer Mode A (en cours) | 🔴 En cours |

### 🔮 Refactor — Suppression Mode A (à faire)

Raison : le scheduler Mode A appelle `requeue_stale()` toutes les 5s,
invalidant les tokens des workers Mode B. Mode A n'a plus aucun worker
actif. Le supprimer élimine le conflit et ~1000 lignes de code mort.

Plan : voir ci-dessous.

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
