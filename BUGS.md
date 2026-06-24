# Bugs & Technical Debt — Scrapower

---

## 🔴 Corrigés

| # | Problème |
|---|---------|
| ~~C1~~ | `NameError: started` dans `remaining_pct()` |
| ~~B1~~ | `COOLDOWN_SEC` 120→60s + log debug cooldown/max |
| ~~B1b~~ | Kaggle `launch_worker` sans cooldown check |
| ~~R1~~ | Rate limit pull partagé → dual-mode auth |
| ~~W2~~ | `error` + `has_logs` + `logs_url` dans tasks |
| ~~R2~~ | Retry 5xx backoff workers |
| ~~H1~~ | Budget Modal reset reboot → persist DB |
| ~~H2~~ | Dérive mensuelle budget → tracking supprimé |
| ~~H3~~ | `_sandbox_started` orphelin → tracking supprimé |
| ~~Refactor~~ | Archi 3 couches: task_type, matching, API unifiée |
| ~~N1~~ | Harvester "launch failed" fantôme → smart launch |
| ~~H6~~ | Caddy 502 → pas un bug (redémarrages Docker, R2 gère) |

## 🟡 Restant

### Quick fixes (1 ligne, 0 risque)

| # | Problème | Fichier |
|---|---------|----------|
| N2 | `HF_HUB_ENABLE_HF_TRANSFER` déprécié → `HF_XET_HIGH_PERFORMANCE` | `modal.py` |
| N3 | `yt-dlp-ejs` dead dep installé dans chaque sandbox | `modal.py` |
| H5 | Pas de log quand cleanup ne trouve rien | `modal.py`, `kaggle.py` |

### Petit refactor (< 20 lignes)

| # | Problème | Fichier |
|---|---------|----------|
| H4 | `_count_queued()` importe `rmod.task_manager` en douce → injecter dans constructeur | `ephemeral.py` |
| N4 | `modal.billing.workspace_billing_report()` → `workspace.billing.report()` | `modal.py` |

### Refactor moyen

| # | Problème | Fichier |
|---|---------|----------|
| W1 | Logs workers sans rotation ni rétention | `http_handler.py`, `domain.py` |
| W3 | Zombie watchdog + requeue_stale systèmes parallèles | `session.py`, `domain.py` |

### Reste à faire

| # | Problème | Fichier |
|---|---------|----------|
| N5 | `python.py` doc: "trusted workers only" — mais Kaggle/Modal l'exécutent | `python.py` |
| N6 | `whisper_runner.py` debug log: "WG_PROXY set: ...a2e07833e67d4724..." (password en clair) | `whisper_runner.py` |
