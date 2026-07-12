# Audit d'architecture Scrapower — juillet 2026

> Review approfondie de l'état réel du projet (v0.7.1, ~4 800 lignes Python,
> 50 commits) et proposition de refonte « au mieux de ce que le projet peut
> réellement permettre ». Ce document remplace la vision par la réalité, puis
> reconstruit une trajectoire cohérente.

---

## 0. Verdict en une page

**Ce que dit la documentation** : un « agrégateur de calcul distribué », un
« bus de calcul » façon AWS Lambda + S3 gratuit, exécutant des tâches
WASM et Python sur des workers hétérogènes (Kaggle, Modal, HF Spaces,
navigateurs), comparé dans une étude de 500 lignes à BOINC, Golem, Fluence,
Dfinity ICP.

**Ce que fait réellement le code** : transcrire des vidéos YouTube avec
Whisper sur des GPU T4 gratuits (Kaggle + Modal), en contournant le blocage
d'IP datacenter de YouTube via un proxy résidentiel WireGuard.

Tout le reste a été **supprimé par sélection naturelle** au fil des commits :
WASM (« 0/70 tâches »), workers navigateur (Mode A / WebSocket), système de
réputation, verification game, WebRTC P2P. Ce qui reste de « générique »
(les champs `task_type`, `runtime`, `requirements_json`, la fonction
`_match_capabilities` à 6 règles) ne sert qu'**un seul workload concret**.

**La bonne nouvelle** : le projet a déjà, sans le formuler, convergé vers son
vrai produit. **La refonte optimale n'est pas d'ajouter — c'est d'assumer.**
Un service de transcription/sous-titrage auto-hébergé qui carbure sur du GPU
gratuit est un produit réel, différencié et utile (un Deepgram/AssemblyAI du
pauvre). Le cœur technique le plus précieux — le *harvester quota-aware* qui
maximise l'usage des tiers gratuits — mérite d'être gardé et durci ; le reste
de la généricité coûte cher pour rien.

**Les 3 décisions à fort levier** :
1. **Choisir le produit** (transcription/sous-titres) et supprimer la
   généricité spéculative → −30 à 40 % de code, cohérence totale.
2. **Réintroduire des tests** sur la machine à états file/lease/retry →
   c'est là que vivent les bugs d'un système distribué, et il y en a
   actuellement **zéro**.
3. **Aligner la documentation sur le code** → l'écart actuel est le plus
   grand risque de maintenabilité.

---

## 1. Ce que le projet est vraiment

| Dimension | Documentation / vision | Réalité du code (v0.7.1) |
|---|---|---|
| Workloads | WASM + Python générique + whisper + fetch + translate + LLM | **Whisper uniquement** (`whisper_runner.py` est le seul exécutable réel) |
| Runtimes | wasm + python + docker (roadmap) | `python` seul ; `execute_python` seul câblé |
| Workers | Kaggle, Modal, HF Spaces, navigateurs (WebGPU) | **Kaggle + Modal** (GPU). HF = CPU, donc **incapable** de faire tourner le seul workload GPU → vestigial |
| Protocole | Mode A (WS push) + Mode B (HTTP pull) | **Mode B seul** (Mode A supprimé, à juste titre) |
| Confiance | réputation adaptative + challenge 10 % + redondance | **rien** (tout supprimé ; workers = comptes que l'auteur contrôle) |
| Multi-tenant | isolation `client_id`, quotas par clé | **une seule clé API partagée** ; toutes les tâches sont `client_id="anonymous"` |
| Positionnement | « bus de calcul distribué » | **file de jobs FaaS mono-workload** |

Le décalage n'est pas anodin : il fait porter à l'API, au schéma SQL et au
scheduler le **coût d'une abstraction générale** qui n'a qu'une seule
implémentation. C'est la cause racine de la majorité de la complexité
résiduelle.

---

## 2. Points forts réels (à conserver)

Il faut créditer ce qui est bien pensé — et il y en a.

1. **Le blob store content-addressed** (`blob_store.py`) est propre :
   SHA-256, immuable, écriture atomique (`tmp` + `os.replace`), validation
   anti-path-traversal (64 hex). C'est la bonne abstraction de stockage.
2. **La sécurité anti-race sur l'assignation** (`assignment_token` unique par
   tentative, vérifié au submit via `cursor.rowcount`) est correcte et bien
   faite. Le raisonnement sur `rowcount` vs `total_changes` (voir SESSION_DOC)
   montre une vraie compréhension de SQLite.
3. **Le harvester quota-aware** est l'idée forte du projet : interroger le
   quota restant de chaque compte (Modal billing API, `kaggle quota --csv`),
   trier par `remaining_pct` décroissant, lancer en parallèle. C'est
   l'ingrédient différenciant — maximiser un « marché spot gratuit ».
4. **Le contournement YouTube par IP résidentielle** (WireGuard homelab →
   SOCKS5 Oracle → worker) est une intuition pragmatique et réelle : YouTube
   bloque les IP datacenter, pas les IP résidentielles.
5. **Le choix de Mode B (HTTP pull stateless)** pour des workers éphémères est
   architecturalement juste ; tuer Mode A était la bonne décision.
6. **La discipline de suppression de code mort** : le projet a supprimé
   agressivement ce qui ne servait pas. C'est sain — il faut juste finir le
   travail (voir §1).
7. **Le durcissement Docker** du coordinateur (`read_only`, `cap_drop: ALL`,
   `no-new-privileges`, `tmpfs noexec`, user non-root) est solide.

---

## 3. Constats & problèmes

### 3.1 🔴 Correctness (confirmés par lecture du code)

**C1 — Le GC de blobs ne collecte jamais → fuite disque.**
`store_blob` insère `ref_count=1`. `TaskManager.create()` fait `+1` sur
`executable_hash`/`input_hash`. `set_queued` refait `+1` sur l'input.
`complete()` fait `+1` sur l'output. Mais `cleanup_expired` ne décrémente que
`−1` par hash. Résultat : `ref_count` se stabilise à ≥ 1 et **n'atteint
jamais 0** ; `run_gc` (qui ne supprime que `ref_count=0`) ne libère jamais
rien, quel que soit le TTL. Le blob `whisper_runner` seedé au démarrage voit
son compteur croître de +1 **à chaque tâche, sans borne**. Déjà identifié en
interne (audit-2026-06 B12/A8) mais non corrigé.

**C2 — `get_result` passe `db=None` à `get_blob`.**
`client_api.py:134` : `get_blob(None, config.blob_dir, ...)`. Ça ne casse que
parce que `get_blob`/`blob_exists` **n'utilisent pas** leur paramètre `db`
(signatures trompeuses, BUGS.md #16). Toute future implémentation qui lirait
la DB planterait.

**C3 — Migrations DB silencieuses, sans versioning.**
`db.py:_migrate` réexécute toutes les migrations à chaque boot dans un
`try/except: pass`. Pas de table `schema_version`. Une migration qui échoue
pour une *vraie* raison est avalée. Incohérence : la migration ajoute
`task_type DEFAULT 'wasm'` alors que le code par défaut est `'whisper'`.

**C4 — Timestamps hétérogènes (TEXT vs REAL).**
`created_at`/`updated_at` sont des `TEXT` contenant `str(time.time())` ;
`assigned_at` est `REAL`. Les comparaisons (`requeue_stale`,
`cleanup_expired`) mêlent `str(now - delta)` et colonnes numériques. Ça
« marche » aujourd'hui par coïncidence (les timestamps Unix font 10 chiffres
jusqu'en 2286, donc l'ordre lexicographique coïncide avec l'ordre numérique)
mais c'est fragile et un piège pour la prochaine personne.

**C5 — Mutation d'`os.environ` pour les cookies YouTube.**
`transcribe_api.py:158` : `os.environ["SCRAPOWER_YT_COOKIES_HASH"] = new_hash`.
État mutable global, non thread-safe, perdu au redémarrage, partagé entre tous
les appelants. Devrait être porté par la tâche (dans l'input blob) ou en DB.

**C6 — Corruption d'encodage (mojibake) dans les sources.**
`main.py` (lignes 1, 368, 400, 456, 471…) et `security.py` contiennent des
séquences `ÃÂ¢ÃÂÃÂ` (UTF-8 double/triple-encodé) dans les docstrings et les
bannières de commentaires. Cosmétique, mais révèle un problème de pipeline
d'édition et donne une mauvaise impression immédiate.

**C7 — Concurrence Modal comptée globalement.**
`ModalHarvester.launch_worker` teste `len(self._sandbox_ids) >= MAX_CONCURRENT`
(global, tous comptes confondus) au lieu de par compte, ce qui contredit le
design « décision par compte » de `AccountRegistry`.

### 3.2 🟠 Fiabilité & exploitation

**R1 — Zéro test.** La suite de tests a été supprimée (« dead code, no ROI »).
Pour un système distribué avec races, retries, leases et une machine à états,
c'est la décision la plus coûteuse à long terme. **C'est précisément là que
vivent C1, C4, C7.**

**R2 — I/O bloquantes dans l'event loop.** `modal.py` fait du `sqlite3`
synchrone (`_load_state`/`_save_state`) et `open("deploy/modal/worker.py")` en
plein `async` (BUGS.md #26). Sur une boucle harvester à 15 s, ça bloque tout.

**R3 — SQLite mono-writer sans `busy_timeout`.** API + harvester +
`_maintenance_loop` écrivent tous. WAL est activé (bien), mais l'absence de
`PRAGMA busy_timeout` (noté comme TODO dans SESSION_DOC) expose à des
`database is locked` sous charge.

**R4 — Observabilité minimale.** Pas de métriques, pas d'historique
structuré, pas de coût-par-transcription. Le debug se fait via des fichiers
`data/logs/{task_id}.log` sur disque. `/stats` existe mais reste ponctuel.

**R5 — État volatil au redémarrage.** Rate-limits (`_RATE_WINDOW`),
`SessionManager`, hash cookies vivent en mémoire. Le `_purge_orphaned_assignments`
au boot est un bon réflexe, mais l'ensemble reste un SPOF.

### 3.3 🟡 Design & dette

**D1 — Généralité spéculative.** `task_type` / `runtime` / `requirements_json`
/ `_match_capabilities` (6 règles) pour un unique workload GPU-whisper.
Les valeurs par défaut trahissent l'histoire (`task_type='wasm'` en migration,
`'whisper'` en code).

**D2 — Violations de couche.** `TaskService` (censé être « pure business
logic, no I/O ») accède directement à `self._tm._db` pour du SQL brut dans 6
méthodes (BUGS.md #19). L'abstraction `TaskManager` fuit.

**D3 — Dette documentaire.** `ARCHITECTURE.md` décrit Mode A, navigateurs,
WASM, HF GPU — inexistants. L'étude de faisabilité disserte sur BOINC/Golem
pour un transcripteur YouTube. La ROADMAP planifie « v0.9 LLM distribué »
alors que les fondations v0.7 (tests, versioning de schéma) manquent.

**D4 — Versions incohérentes** : `pyproject 0.1.0`, homepage `0.7.1`, health
`0.1.0`.

### 3.4 🔒 Sécurité & conformité

**S1 — Pas de vrai multi-tenant.** Une clé API unique ; le `_check_owner`
compare à `client_id="anonymous"` pour toutes les transcriptions → le contrôle
de propriété est vide de sens pour le workload principal. À assumer (mono-user)
ou à corriger (clés par client, hashées, quotas par clé).

**S2 — Risque ToS existentiel.** L'automatisation headless de kernels Kaggle
et de sandboxes Modal pour du calcul non-interactif est une zone grise (voire
une violation) que **l'étude de faisabilité du projet elle-même classe
🔴 « risque élevé » pour Kaggle**. Or *toute* la proposition de valeur repose
là-dessus. Un bannissement de comptes n'est pas un accident improbable — c'est
le mode de panne nominal à anticiper.

**S3 — Python worker non sandboxé.** L'ADR-009 l'assume pour un usage « solo /
trusted ». Correct tant que les workers sont des comptes contrôlés par
l'auteur — **mais alors il faut le dire**, et fermer définitivement la porte
aux workers tiers dans la doc (sinon la porte « bus de calcul ouvert » reste
un piège de sécurité).

---

## 4. La question centrale : quel produit ?

Avant tout choix technique, il faut trancher l'identité. Deux chemins
honnêtes :

### Chemin A — Assumer le produit réel : « Transcription/sous-titres serverless sur GPU gratuit »
Le projet fait déjà ça, et raisonnablement bien. On y va à fond :
- Recadrage : un **Whisper-as-a-Service auto-hébergé** qui carbure sur du GPU
  scavengé gratuit — un Deepgram/AssemblyAI du pauvre, self-hostable.
- On **tue toute la généricité** : plus de `task_type`, plus de matching de
  runtime, plus de `requirements_json`, plus de reliquats WASM. Une tâche =
  `{source_audio, model, langue, format}`. La machine à états s'effondre à
  `QUEUED → RUNNING → DONE/FAILED`.
- Surface produit : `POST /transcribe`, `GET /results`, batch playlist,
  **webhooks**, **cache par (url, model)**, et surtout la **traduction de
  sous-titres** (SRT/VTT → SRT traduit) déjà pressentie dans la roadmap — c'est
  le besoin utilisateur concret (sous-titres pour PotPlayer/VLC).
- Résultat : ~35-40 % de code en moins, 100 % cohérent.

### Chemin B — Devenir réellement le « bus de calcul générique »
Si la généricité est le but, il faut la mériter *vraiment* : un SDK de jobs
(`scrapower.submit(fn, data)` via cloudpickle), un **vrai sandboxing** (l'ADR
admet que Python n'est pas isolé → impossible d'accepter des workers tiers),
un scheduler de capacités digne de ClassAds (HTCondor), et **au moins deux
workloads réels** (whisper + inférence LLM + jobs batch). C'est un projet
bien plus gros — et l'étude de faisabilité du projet conclut elle-même que
les parties les plus ambitieuses (pool de RAM, GPU unifié) sont **impossibles**.

### Recommandation : **Chemin A.**
Les faits parlent : chaque brique générique (WASM, navigateur, réputation) a
été supprimée parce qu'elle n'avait pas de ROI. **Les actions passées de
l'équipe révèlent déjà la réponse.** On garde **une seule** couture interne —
l'abstraction « runner » — pour pouvoir ajouter plus tard un `llm_runner` comme
*deuxième* workload sans rouvrir l'architecture, mais on **arrête de payer** la
généricité complète dans l'API, le schéma DB et le scheduler.

---

## 5. Comment je le referais — blueprint par composant

En supposant le Chemin A. L'objectif : **la même valeur, moitié moins de
surprises.**

### 5.1 Modèle de domaine — un `Job`, une file à lease
Remplacer la machine à 8 états (`PENDING/DOWNLOADING/QUEUED/ASSIGNED/COMPLETED/
FAILED/TIMEOUT/CANCELLED`) par :

```
QUEUED → RUNNING → DONE | FAILED        (+ CANCELLED)
```

- `DOWNLOADING`, `ASSIGNED`, `TIMEOUT` deviennent des **attributs**, pas des
  états : `attempts`, `lease_expires_at`, `last_heartbeat_at`.
- Primitive unique : **une file à lease** (visibility timeout, comme SQS). Un
  worker « pull » = prendre un lease de N secondes ; heartbeat = prolonger le
  lease ; submit = consommer le lease. Le trio actuel
  `assignment_token` + `requeue_stale` + `_purge_orphaned_assignments` fond en
  un seul concept, atomique et testable.
- La préparation d'input (download audio playlist) sort du modèle de tâche :
  c'est un **producteur** qui pousse des jobs déjà `QUEUED`.

### 5.2 Stockage
- **SQLite, mais fait correctement** : garder WAL, ajouter `busy_timeout`,
  une table `schema_version` + migrations ordonnées et idempotentes,
  timestamps en `INTEGER`/`REAL` (jamais `str`), et **sérialiser les écritures**
  (une seule connexion writer, ou un petit acteur d'écriture). Postgres (Neon
  free 500 Mo) seulement si un jour multi-coordinateur.
- **Blob store** : supprimer le ref-counting incrémental (source de C1). GC par
  requête : `DELETE blob WHERE NOT EXISTS (job referencing it) AND age > TTL`.
  Le compteur dérivant devient un `JOIN`, impossible à désynchroniser.
- **Déporter les blobs** sur du stockage objet (Cloudflare R2 / Backblaze B2,
  egress gratuit) pour ne pas saturer le petit disque Oracle — les audios et
  transcripts ne sont pas des données à garder sur le nœud de contrôle.

### 5.3 Providers / Harvester — le joyau, à généraliser proprement
- Protocole `Provider` net : `remaining_credits() -> Credits`,
  `launch(job_hint) -> WorkerHandle`, `reap() -> list[dead]`.
- **API-first** pour les quotas (Modal billing, Kaggle quota), avec cache et
  **fallback estimé** si l'API échoue (aujourd'hui un échec billing est avalé
  et renvoie silencieusement 0).
- **Concurrence par compte** (corrige C7), pas globale.
- **Circuit breaker par compte** : sur échec de lancement répété ou signal de
  ban, mettre le compte en quarantaine N minutes. Traiter le GPU gratuit comme
  un **marché spot instable** — ce pour quoi le harvester est déjà bien armé.
- Sortir les I/O bloquantes de l'event loop (`asyncio.to_thread` / `aiosqlite`).

### 5.4 Worker
- **Un seul worker canonique** packagé proprement (fin du duo
  `pip install git+…` pour Kaggle / string bundlé pour Modal ; publier un
  wheel, ou un unique module auto-suffisant généré par un build reproductible
  et testé).
- **Chunking audio** (déjà en roadmap) : vidéo > 30 min → N segments → N jobs
  parallèles sur comptes différents → merge ordonné. C'est le **multiplicateur
  de débit** direct du tiers gratuit — donc le cœur de la valeur.
- Checkpoint = un chunk = une unité de perte maximale acceptable. Plus besoin
  d'un « Checkpoint Manager » séparé.

### 5.5 Fiabilité & tests (le chantier n°1)
- **Simulation déterministe de la file** : property-based testing où les
  workers meurent, soumettent en retard, dupliquent, se racent. C'est le seul
  moyen fiable de tenir une file à lease.
- **Idempotence du submit** : clé `(job_id, lease_token)` ; un double-submit
  est un no-op explicite, pas un rejet ambigu.
- **Mocks de providers** : tester le harvester (quota → launch → reap →
  circuit breaker) sans toucher Kaggle/Modal.

### 5.6 Observabilité
- Une table `events(job_id, kind, ts, worker, provider, cost_estimate)`. Elle
  donne gratuitement : coût/transcription, fiabilité par provider, latence de
  file, taux de retry.
- `/stats` + un **dashboard HTML mono-fichier** (file, workers actifs,
  dernières transcriptions, quota par compte). Suffisant, pas besoin de
  Prometheus/Grafana à ce stade.

### 5.7 Sécurité / conformité
- **Trancher le tenancy** : si mono-user (probable), le dire et retirer le faux
  `_check_owner`. Sinon, clés par client hashées + quota par clé.
- **ToS comme préoccupation de première classe** : rotation de comptes, respect
  strict des quotas, jitter sur les lancements, détection de ban → quarantaine.
  Concevoir *pour* le churn de comptes plutôt que de l'espérer inexistant.
- Documenter noir sur blanc : « workers = comptes contrôlés par l'opérateur,
  Python non sandboxé, **ne jamais** exposer le pull à des tiers ».

### 5.8 Vérité documentaire
- Réécrire `ARCHITECTURE.md` pour décrire **ce qui existe**.
- Archiver l'étude de faisabilité en « vision historique » (elle est de bonne
  qualité, mais elle décrit un autre projet).
- Un seul README honnête : « Transcription/sous-titres Whisper auto-hébergée
  sur GPU gratuit scavengé. Voici l'architecture réelle. »

---

## 6. Plan par phases (incrémental, sans big-bang)

| Phase | Objectif | Contenu | Levier |
|---|---|---|---|
| **P0 — Assainir** | Arrêter l'hémorragie | Fix C1 (GC ref-count), C3 (schema_version + busy_timeout), C4 (timestamps), C6 (encodage), R2 (I/O async) | Faible effort, supprime des bugs latents |
| **P1 — Filet de sécurité** | Pouvoir refactorer sans peur | Tests de la file à lease + mocks providers + idempotence submit | Débloque tout le reste |
| **P2 — Assumer le produit** | Cohérence | Supprimer la généricité (task_type/runtime/requirements), collapser la machine à états, aligner la doc | −35 % de code |
| **P3 — Durcir le joyau** | Fiabiliser le débit | Concurrence par compte (C7), circuit breaker, quotas API-first, quarantaine de ban | Cœur de la valeur |
| **P4 — Multiplier la valeur** | Débit & usages | Chunking audio parallèle, cache par (url,model), webhooks, **traduction de sous-titres** | Nouvelle valeur utilisateur |
| **P5 — Observer** | Piloter | Table d'events, coût/transcription, dashboard mono-fichier | Décisions data-driven |

---

## 7. Garder / Changer / Supprimer

| Décision | Éléments |
|---|---|
| **✅ Garder** | Blob store content-addressed · assignment-token race-safety (fusionné dans le lease) · harvester quota-aware · astuce IP résidentielle WireGuard · Mode B HTTP pull · durcissement Docker · discipline anti-code-mort |
| **🔧 Changer** | Machine à états 8→4 · file à lease unique · ref-count → GC par requête · timestamps typés · migrations versionnées · concurrence par compte · I/O async · doc alignée sur le réel |
| **🗑️ Supprimer** | `task_type`/`runtime`/`requirements_json`/`_match_capabilities` (généricité mono-workload) · défauts WASM résiduels · faux multi-tenant `_check_owner` · provider HF (CPU, incapable du workload GPU) sauf si un runner CPU réel arrive · l'étude de faisabilité en tant que doc active |

---

## 8. Le mot de la fin

Scrapower n'est pas un projet raté : c'est un projet qui a **découvert son
vrai produit en cours de route sans encore le nommer**. La meilleure version de
Scrapower n'est pas le « bus de calcul universel » de la documentation — c'est
un **service de transcription/sous-titrage rock-solid, auto-hébergeable, qui
transforme du GPU gratuit épars en une API fiable**. Tout le talent déjà investi
(blob store, race-safety, harvester quota-aware, contournement YouTube) sert
exactement ce produit-là. Il reste à retirer l'échafaudage de l'ambition
abandonnée, poser un filet de tests, et durcir le seul mécanisme qui compte
vraiment : récolter du calcul gratuit sans se faire bannir.
