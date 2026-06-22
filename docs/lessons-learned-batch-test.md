# Lessons Learned — Phase de test batch (Juin 2026)

> Ce document capture les observations, problèmes et décisions architecturales
> issues de la phase de test de transcription batch (5 vidéos, 110 min chaque).

---

## Chronologie du test

```
t=0s    POST /transcribe/batch → 5 tâches créées (QUEUED)
t=15s   Harvester choisit Modal (100% quota)
t=45s   Sandbox Modal #1 boot
t=60s   Worker pull tâche 1 → Mode B (yt-dlp)
t=65s   ❌ yt-dlp échoue : "Sign in to confirm you're not a bot"
t=70s   Fallback : Oracle DL audio via VPN → blob → requeue
t=90s   Worker repull → Mode A (blob) → whisper...

        [Cycle identique répété pour les tâches 2, 3, 4, 5]

t=300s  Sandbox #2 créé (cooldown 120s)
t=600s  Sandbox #3 créé
t=900s  Tâche 1 : 3 retries épuisés → FAILED
t=1200s Tâches 2-5 : en cours / requeuées / FAILED
```

## Problèmes identifiés

### P0 — Cycle fallback systématique

**Observé :** Chaque tâche passait par Mode B (échec) → fallback Oracle DL → Mode A.

**Cause :** Le coordinateur ne sait pas que les workers Modal ont une IP datacenter. Il laisse
chaque worker tenter Mode B, qui échoue systématiquement car YouTube bloque les IPs
Google Cloud ("Sign in to confirm you're not a bot").

**Coût :** 5 × (Mode B doomed + fallback Oracle) = 5 × 85s = ~7 min de gaspillage pur.
Oracle a téléchargé ~500 MB d'audio pour rien.

**Solution retenue :** `network.ip_reputation` dans les capabilities worker. Le coordinateur
décide au moment du prepare si les workers peuvent DL ou s'il faut pré-télécharger.

### P1 — Scale-up trop lent

**Observé :** 8 minutes entre chaque création de Sandbox.

**Cause :** `COOLDOWN_SEC=120` + cleanup lent des sandbox_ids morts + `MAX_CONCURRENT=3`
bloquait les lancements tant que la liste n'était pas nettoyée.

**Solution retenue :** COOLDOWN_SEC → 60s, cleanup plus agressif via `modal.Sandbox.list.aio()`.

### P1 — Workers Kaggle inactifs

**Observé :** Seuls les workers Modal traitaient les tâches. Kaggle (3 comptes) est resté idle.

**Cause probable :** Kernels Kaggle auto-terminés après idle timeout, ou quota épuisé.
À investiguer plus en détail.

### P2 — Erreurs whisper opaques

**Observé :** exit_code=1 après fallback, sans message d'erreur visible. Impossible
de savoir si c'est OOM CUDA, timeout, ou modèle manquant.

**Solution retenue :** Ajouter `last_error` aux tâches, visible via `GET /tasks/{id}`.

### P2 — Cookies insuffisants sur Modal

**Observé :** Les cookies YouTube (export fenêtre privée) sont téléchargés et passés à
yt-dlp, mais YouTube bloque quand même. L'IP datacenter Google Cloud + cookies créés
sur IP résidentielle française = signaux incohérents = rejet.

**Solution retenue :** VPN homelab WireGuard → IP résidentielle partagée → plus de blocage.

---

## Décisions architecturales

### 1. Homelab VPN exit node

Tous les workers (Modal, Kaggle, futurs) routeront leur trafic YouTube via un VPN
WireGuard hébergé sur le homelab. L'IP résidentielle élimine le blocage anti-bot.

**Pourquoi pas CyberGhost ?** Le VPN commercial est limité à Oracle (container Docker).
Impossible de le faire utiliser par les Sandboxes Modal ou les kernels Kaggle.

**Pourquoi pas Modal Proxies ?** Disponible uniquement sur le plan Team/Enterprise
($250+/mois). Inaccessible sur le Starter gratuit.

### 2. Capabilité réseau générique

`network.ip_reputation` remplace toute notion spécifique à YouTube. Valeurs :
- `"residential"` — IP domestique, passe les anti-bots (homelab VPN)
- `"vpn"` — VPN commercial, peut passer selon le service (CyberGhost)
- `"datacenter"` — IP cloud, bloquée par la plupart des anti-bots

Ce critère est transférable à toutes les tâches futures : web scraping,
appels API, téléchargement de datasets, etc.

### 3. Pré-téléchargement comme filet de sécurité

Même avec le VPN homelab, le coordinateur garde la capacité de pré-télécharger l'audio
(fallback). Si le VPN est down ou si tous les workers sont datacenter, le système
continue de fonctionner en Mode A.

---

## Métriques de la phase de test

| Métrique | Valeur | Cible après corrections |
|----------|--------|------------------------|
| Temps total 5 vidéos | >20 min (1 FAILED, 4 en cours) | ~12 min (5 workers //) |
| Gaspillage fallback | 7 min (45% du temps) | 0 min (VPN homelab) |
| Workers actifs max | 1 (Modal seul) | 3-5 (Kaggle + Modal) |
| Taux d'échec | 1/5 (20%) | 0/5 (last_error pour debug) |
| Scale-up 3 workers | >15 min | ~2 min (cooldown 60s) |
