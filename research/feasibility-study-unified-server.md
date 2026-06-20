# Étude de Faisabilité : Agrégation de Ressources Hétérogènes Gratuites en un Serveur Unifié — Scrapower

**Auteur** : Analyse architecturale système distribué  
**Date** : Juin 2026  
**Contexte** : Scrapower v0.3 (coordinateur FastAPI + SQLite, WebSocket, WASM/wasmtime, content-addressing SHA-256, harvester GitHub Actions/Colab/local)

---

## 1. Projets Similaires — État de l'Art

### 1.1 BOINC (Berkeley, 2002)

| Axe | Analyse |
|-----|---------|
| **Modèle de confiance** | Vérification par redondance (quorum ≥ 2). Résultats comparés bit-à-bit. Si mismatch → nouveau worker. Fondamentalement identique au mécanisme de *challenge* de Scrapower. |
| **Runtime** | Exécutables natifs x86. **Pas de runtime universel.** Chaque projet distribue son propre binaire compilé. Pas de sandboxing fort — les workers sont volontaires et *trustent* le projet. |
| **Maturité** | 20 ans, 30+ projets scientifiques (SETI@home, Folding@home), centaines de petaflops. **Référence absolue du calcul volontaire.** |
| **Ressources** | CPU/GPU x86 de volontaires. Pas de navigateurs, pas de cloud gratuit. |
| **Forces** | Robustesse éprouvée, modèle de vérification copiable. |
| **Faiblesses** | Pas de runtime universel, pas d'agrégation de RAM, déploiement lourd (installer un binaire), pas de GPU Web. |

**Pertinence Scrapower** : Le mécanisme de double-exécution de Scrapower (`challenge`, `CHALLENGE_RATE = 0.10`) est directement inspiré de BOINC. Différence clé : Scrapower veut un runtime universel (WASM) et des workers zéro-friction (onglet navigateur).

---

### 1.2 Golem Network (2018, blockchain Ethereum/Polygon)

| Axe | Analyse |
|-----|---------|
| **Modèle de confiance** | Marché libre avec paiement en tokens GLM. Vérification via VM Golem (runtime custom). Résultats vérifiables par le réseau. |
| **Runtime** | VM Golem custom (pas WASM standard). Support CPU uniquement (GPU expérimental). Pas de WebGPU. |
| **Maturité** | Mainnet depuis 2018, ~1 500 workers actifs, marketplace fonctionnel. Volume faible. |
| **Ressources** | CPU/GPU de volontaires rémunérés. N'agrège pas les plateformes gratuites. |
| **Forces** | Économie de marché fonctionnelle, VM sandboxée, vérification on-chain. |
| **Faiblesses** | Runtime custom non standard, pas de WASM, communauté modeste, pas de navigateur. La VM Golem est un verrou propriétaire. |

**Pertinence Scrapower** : La roadmap Scrapower mentionne Golem comme cible v0.5 (« brancher Scrapower comme provider sur le marketplace »). Le token GLM est le seul mécanisme de rémunération décentralisé mature aujourd'hui.

---

### 1.3 iExec (2018, blockchain Ethereum)

| Axe | Analyse |
|-----|---------|
| **Modèle de confiance** | Marché avec token RLC. TEE (Intel SGX) pour la confidentialité — les workers ne peuvent pas voir les données exécutées. |
| **Runtime** | Docker conteneurisé. Travaille sur WASM via un composant optionnel. |
| **Maturité** | Mainnet, partenariat Intel, ~50 workers. Faible adoption. |
| **Ressources** | CPU de volontaires rémunérés. TEE = machines avec SGX, pas de cloud gratuit. |
| **Forces** | Confidentialité forte (SGX), modèle de confiance minimal. |
| **Faiblesses** | Dépendance matérielle SGX, Docker ≠ WASM sandbox léger, pas de navigateur, réseau minuscule. |

**Pertinence Scrapower** : L'approche TEE est orthogonal au modèle Scrapower (sandbox wasmtime léger). ZK-proofs (mentionné dans la roadmap Scrapower v0.5) serait une alternative plus portable à SGX.

---

### 1.4 Akash Network (2020, blockchain Cosmos)

| Axe | Analyse |
|-----|---------|
| **Modèle de confiance** | Marché de location de conteneurs. Paiement en AKT. Pas de vérification de calcul — Akash fournit l'infrastructure, pas la vérification. |
| **Runtime** | Docker/Kubernetes standard. N'importe quel conteneur. Pas de runtime calcul unifié. |
| **Maturité** | ~50 000 CPUs actifs, marketplace fonctionnel, GPU en preview. |
| **Ressources** | CPU/GPU/stockage de providers infrastructure. Pas de navigateur, pas de gratuité (même si très bon marché). |
| **Forces** | Énorme capacité, Docker standard, marketplace mature, GPU en approche. |
| **Faiblesses** | Pas gratuit, pas de vérification de calcul, pas de runtime universel unifié. |

**Pertinence Scrapower** : Complémentaire. Akash peut fournir l'infrastructure persistante sur laquelle faire tourner le coordinateur Scrapower, mais pas les workers éphémères gratuits.

---

### 1.5 Fluence Network (2022, blockchain)

| Axe | Analyse |
|-----|---------|
| **Modèle de confiance** | Calcul distribué avec preuves. Modèle Aqua (langage de composition). |
| **Runtime** | **WASM/WASI first-class** via Marine (runtime basé sur wasmtime). C'est le projet le plus proche de Scrapower sur le plan technique. |
| **Maturité** | Mainnet, ~100 nœuds, encore émergent. |
| **Ressources** | CPU de providers. Pas de navigateur, pas de cloud gratuit, pas de GPU. |
| **Forces** | **Runtime WASM natif**, modèle de composition Aqua, vérification cryptographique. |
| **Faiblesses** | Pas de navigateurs, pas de GPU, réseau petit, pas d'agrégation de ressources gratuites. |

**Pertinence Scrapower** : Fluence est l'inspiration technique la plus proche. Le runtime Marine (wasmtime-based) est quasi identique à celui de Scrapower. La roadmap Scrapower de fédération de coordinateurs et P2P (déjà partiellement implémenté : WebRTC, Kademlia DHT, GossipSub) converge vers le design Fluence.

---

### 1.6 NuNet (2021, blockchain Cardano)

| Axe | Analyse |
|-----|---------|
| **Modèle de confiance** | Marché décentralisé de compute avec token NTX. Encore en développement. |
| **Runtime** | Docker + plateforme custom. Pas de WASM. |
| **Maturité** | Très précoce, testnet uniquement. |
| **Ressources** | CPU/GPU de volontaires. |
| **Forces** | Ambitieux, GPU supporté. |
| **Faiblesses** | Pré-maturité, pas de WASM, pas de navigateur. |

**Pertinence Scrapower** : Négligeable à ce stade de maturité.

---

### 1.7 Dfinity ICP (2021, blockchain Internet Computer)

| Axe | Analyse |
|-----|---------|
| **Modèle de confiance** | Consensus BFT entre nœuds validés. Pas de volontariat — seuls les nœuds autorisés participent. |
| **Runtime** | **WASM system-wide.** Chaque canister est un module WASM sandboxé. C'est le seul réseau majeur entièrement WASM-native. |
| **Maturité** | Mainnet, centaines de nœuds validés, écosystème actif. |
| **Ressources** | CPU/RAM de data centers autorisés. Pas de gratuité (paiement en cycles). Pas de navigateur worker. Pas de GPU. |
| **Forces** | WASM partout, sandboxing éprouvé, scalabilité via sous-réseaux, stockage persistant on-chain. |
| **Faiblesses** | Centralisé (nœuds autorisés), coûteux (cycles), pas de GPU, pas d'agrégation de ressources gratuites. |

**Pertinence Scrapower** : ICP valide que WASM comme runtime universel est viable à l'échelle. Mais le modèle économique et la gouvernance sont à l'opposé du « compute gratuit/volontaire ».

---

### 1.8 Ray (Anyscale, 2017)

| Axe | Analyse |
|-----|---------|
| **Modèle de confiance** | Cluster privé, confiance totale entre nœuds. Pas de vérification. |
| **Runtime** | Python natif. Tasks/fonctions distribuées via Ray object store (Plasma). Pas de WASM. |
| **Maturité** | Très mature, utilisé en production (OpenAI, Netflix, Uber). |
| **Ressources** | CPU/GPU de machines privées ou cloud provisionnées. Pas de ressources gratuites hétérogènes. |
| **Forces** | **Ray object store** est la meilleure implémentation de mémoire distribuée accessible aujourd'hui. Framework ML distribué complet (Ray Train, Ray Serve, RLlib). |
| **Faiblesses** | Pas de sandboxing, pas de WASM, pas de workers non fiables, pas de navigateur. |

**Pertinence Scrapower** : Le Ray object store (Plasma) est le modèle mental de ce à quoi pourrait ressembler un « pool de RAM unifié ». Mais Ray suppose un datacenter — latence < 1ms, réseau fiable, nœuds persistants. Aucune de ces hypothèses n'est vraie pour Scrapower.

---

### 1.9 HTCondor (Wisconsin, 1988)

| Axe | Analyse |
|-----|---------|
| **Modèle de confiance** | Grille de calcul institutionnelle. Confiance organisationnelle, pas cryptographique. |
| **Runtime** | Exécutables natifs. Supporte les jobs parallèles (MPI). Pas de WASM. |
| **Maturité** | 35 ans, des milliers de sites (LHC, universités), robustesse inégalée. |
| **Ressources** | CPU/GPU de clusters universitaires et data centers. Pas de gratuité hétérogène. |
| **Forces** | Matching de ressources très sophistiqué (ClassAds), tolérance aux pannes, checkpointing. |
| **Faiblesses** | Pas de navigateur, pas de cloud gratuit, pas de WASM, modèle de confiance non adapté à l'open web. |

**Pertinence Scrapower** : Le *scheduler ClassAd* de HTCondor est le gold standard du matching de capacités hétérogènes. Le scheduler actuel de Scrapower (`Scheduler._tick_loop`, match par runtime + GPU flag) est rudimentaire en comparaison.

---

### 1.10 Cloudflare Workers (2017)

| Axe | Analyse |
|-----|---------|
| **Modèle de confiance** | Plateforme propriétaire. Exécution sur l'edge network Cloudflare. Pas de modèle de vérification distribué. |
| **Runtime** | **V8 Isolates + WASM.** Exactement le même modèle que les navigateurs Scrapower (onglet). Supporte WASI Preview 2 partiellement. |
| **Maturité** | Très mature, millions de déploiements. |
| **Ressources** | CPU edge léger, 128 MB RAM/isolate, pas de GPU, durée d'exécution CPU limitée (30s gratuit, 30 min payant). |
| **Forces** | WASM natif, zéro cold start, massivement parallèle, déploiement mondial. |
| **Faiblesses** | RAM très limitée (128 MB), pas de GPU, pas d'état persistant (sauf Durable Objects payants), ToS restrictifs. |

**Pertinence Scrapower** : Cloudflare Workers est une source potentielle de workers WASM. Mais la RAM de 128 MB est 125× inférieure aux 16 GB d'un navigateur moderne. Et les ToS interdisent explicitement le « mining » et le « compute intensif ».

---

### Tableau Synthétique

| Projet | Modèle de confiance | Runtime universel | Maturité | Ressources | Gratuit ? |
|--------|---------------------|-------------------|----------|------------|-----------|
| **BOINC** | Redondance (quorum ≥ 2) | ❌ Natif x86 | Très mature | CPU/GPU volontaires | ✅ Oui |
| **Golem** | Marché + VM custom | ❌ VM propriétaire | Mature | CPU volontaires rémunérés | ❌ Payant |
| **iExec** | Marché + SGX | ⚠️ Docker/WASM | Émergent | CPU + SGX rémunérés | ❌ Payant |
| **Akash** | Marché infra | ✅ Docker standard | Mature | CPU/GPU infra | ❌ Payant |
| **Fluence** | Preuves cryptographiques | ✅ **WASM/WASI** | Émergent | CPU providers | ❌ Payant |
| **NuNet** | Marché décentralisé | ❌ Docker custom | Précoce | CPU/GPU volontaires | ❌ Payant |
| **Dfinity ICP** | Consensus BFT | ✅ **WASM** | Mature | CPU/RAM autorisés | ❌ Payant |
| **Ray** | Confiance totale | ❌ Python natif | Très mature | CPU/GPU privés | N/A |
| **HTCondor** | Confiance organisationnelle | ❌ Natif | Très mature | CPU/GPU grille | N/A |
| **Cloudflare Workers** | Plateforme propriétaire | ✅ **WASM/V8** | Très mature | CPU edge, 128 MB | ✅ Gratuit (limité) |
| **Scrapower** | Challenge + redondance | ✅ **WASM/wasmtime** | Précoce (v0.3) | Navigateur, GHA, Colab, serveurs | ✅ Oui |

**Conclusion** : Aucun projet n'a résolu l'agrégation de ressources **gratuites + hétérogènes + navigateur + cloud + Python + WASM** dans un système unifié. Scrapower est en terrain vierge sur la combinaison complète, mais chaque « brique » existe déjà isolément.

---

## 2. Faisabilité Technique

### 2.1 Runtime Universel : WASM est-il suffisant ?

**✅ Oui, pour le CPU. ❌ Non, pour le GPU et l'écosystème Python.**

| Question | Réponse |
|----------|---------|
| **WASM est-il un bon runtime universel CPU ?** | Oui. Wasmtime (utilisé par Scrapower) supporte WASI Preview 1 stable. WASM est déjà exécutable sur : navigateur, GitHub Actions, Colab (via wasmtime-py), serveurs Python. Le sandboxing natif (sandbox wasmtime + fuel metering) est excellent. |
| **WASI Preview 2 (sockets, threads) comble-t-il l'écart ?** | Partiellement. WASI Preview 2 ajoute les sockets asynchrones — un worker WASM pourrait théoriquement se connecter lui-même au coordinateur. Mais le support navigateur est inexistant (les navigateurs n'exposent pas WASI). Et `wasi-threads` est encore expérimental (pas de shared memory multi-thread standard). |
| **Et pour les workloads Python (NumPy, PyTorch, Pandas) ?** | Impossible en WASM pur. Pyodide (Python compilé en WASM) permet d'exécuter du Python dans le navigateur, mais (1) les performances sont 3-5× inférieures au Python natif, (2) pas de GPU via WASM, (3) l'écosystème de packages est limité. L'ADR-009 de Scrapower est correct : **WASM reste le runtime universel CPU, Python natif est un runtime additionnel pour workers serveur/Colab.** |
| **Peut-on unifier WASM + Python sous une même abstraction ?** | Le protocole Worker v2.1 de Scrapower le fait déjà (`runtime: "wasm" | "python"`). Le scheduler matche le runtime demandé avec les capacités déclarées du worker. C'est pragmatique et suffisant. |

**Recommandation** : Ne pas essayer de tout unifier sous WASM. Garder la dichotomie actuelle (WASM pour le calcul vérifiable/portable, Python pour la puissance brute sur workers trustés) est le bon choix. L'ajout de WASI Preview 2 dans wasmtime sera utile pour les workers serveur, pas pour les navigateurs.

---

### 2.2 Mémoire : Peut-on « pooler » la RAM de workers éphémères ?

**❌ Non, pas de manière générale. C'est le point le plus dur de tout le projet.**

La question est : peut-on présenter 4 GB de RAM dans un navigateur + 7 GB dans GitHub Actions + 12 GB dans Oracle Cloud comme un « pool de 23 GB de RAM » accessible par une application ?

| Approche | Faisabilité | Explication |
|----------|-------------|-------------|
| **Mémoire partagée distribuée (DSM)** | ❌ Impossible | La DSM suppose une latence réseau < 100µs (RDMA, InfiniBand) et des nœuds persistants. Avec des workers navigateur en Wi-Fi à 50ms de latence, c'est totalement irréalisable. Le théorème CAP s'applique — on ne peut pas avoir cohérence forte ET tolérance aux partitions avec des nœuds éphémères. |
| **Ray object store / Plasma** | ❌ Inapplicable | Ray suppose un cluster privé fiable. Pas de workers éphémères. Pas de navigateurs. |
| **Modèle MapReduce / data-parallel** | ✅ Réalisable | Diviser les données en partitions, distribuer chaque partition à un worker avec son exécutable, collecter les résultats. Chaque worker travaille sur ses propres données locales. C'est ce que Scrapower fait déjà (une tâche = un exécutable + un input). |
| **Modèle « virtual memory » par blobs** | ⚠️ Partiel | Le blob store content-addressed de Scrapower permet à un worker de télécharger ses inputs depuis le coordinateur et d'uploader ses outputs. Mais il n'y a pas de « mémoire partagée » — chaque worker a sa propre RAM privée. C'est un modèle **FaaS, pas un serveur unifié.** |
| **Dask collections distribuées** | ❌ Inapplicable | Dask suppose des workers Python persistants avec TCP fiable. Pas de navigateurs. Pas de WASM. |

**Conclusion** : On ne peut pas « pooler la RAM » au sens d'une mémoire unifiée cohérente. On peut distribuer des tâches data-parallèles où chaque worker traite un sous-ensemble de données dans sa RAM locale. C'est un modèle **FaaS, pas SMP (symmetric multiprocessing).**

---

### 2.3 GPU : WebGPU (navigateur) vs CUDA (Colab/Kaggle) — Peut-on les unifier ?

**❌ Non, pas techniquement. Mais on peut les traiter comme deux capacités distinctes.**

| Aspect | WebGPU (navigateur) | CUDA (Colab/Kaggle/Modal) |
|--------|---------------------|---------------------------|
| **API** | WebGPU (JavaScript/WASM bindings) | CUDA C/C++/Python (PyTorch, JAX) |
| **Shaders** | WGSL (WebGPU Shading Language) | PTX / CUDA C++ / Triton |
| **Mémoire** | Tampons GPU mappés en JS, max ~2-4 GB selon navigateur | Mémoire GPU dédiée, jusqu'à 16 GB (T4) |
| **Portabilité** | Tout navigateur avec WebGPU (Chrome 113+, Edge, Firefox Nightly) | Toute machine avec GPU NVIDIA |
| **Performance** | ~30-50% d'un GPU natif équivalent (overhead WebGPU) | Proche du métal, 100% |
| **Utilisable via ?** | Shaders WGSL dans le worker navigateur Scrapower | Runtime Python sur worker serveur avec PyTorch |

**La vraie question** : peut-on écrire un seul programme qui s'exécute sur WebGPU ou CUDA selon le worker ?

- **ML** : Non. PyTorch ne tourne pas en WASM. Un modèle ONNX pourrait théoriquement être exécuté via WebNN/WebGPU dans le navigateur et via ONNX Runtime (CUDA) sur serveur, mais l'API ONNX est C/Python, pas WASM.
- **Calcul matriciel générique** : Oui, avec **un shader WGSL compilé en SPIR-V puis en PTX**… mais ce pipeline n'existe pas. Ou avec **Triton** (langage de kernels), mais Triton ne cible pas WebGPU.
- **Approche pragmatique Scrapower** : Le worker navigateur expose déjà WebGPU pour du calcul matriciel (`matmul 256×256 en ~100ms`). C'est un runtime GPU **distinct** du CUDA des workers Python. Le scheduler peut router les tâches GPU vers le bon type de worker (`gpu_required: true` + `runtime: "python"` ou `runtime: "wasm"` avec capacité `gpu.supported`).

**Conclusion** : Pas d'unification technique possible. Deux runtimes GPU distincts, routage par capacités déclarées. Il n'y a pas de « CUDA-like » universel pour le web. WebGPU est une révolution, mais il reste une API graphique, pas une API compute généraliste comme CUDA.

---

### 2.4 Latence et Fiabilité : Comment gérer des workers qui se déconnectent ?

Le problème fondamental : un onglet navigateur peut se fermer à tout instant (l'utilisateur ferme son PC, change de page, etc.).

| Mécanisme | Implémentation Scrapower actuelle | Efficacité |
|-----------|-----------------------------------|------------|
| **WebSocket keepalive** | `heartbeat` toutes les 30s, `heartbeat_ack` du coordinateur | ✅ Bon pour la détection |
| **Reconnexion automatique** | Backoff exponentiel côté worker (v0.2) | ✅ Implémenté |
| **Service Worker** | Le worker survit en arrière-plan (v0.2) | ⚠️ Fragile — les navigateurs tuent les SW après quelques minutes d'inactivité |
| **Lease timeout** | `deadline_ms` par tâche. Si pas de résultat avant deadline → timeout → requeue (max 3 retries) | ✅ Robuste |
| **Assignment token** | Token unique par tâche. Vérifié avant complétion. Empêche les races. | ✅ Excellent |
| **Requeue stale** | `task_manager.requeue_stale()` vérifie les tâches ASSIGNED sans heartbeat | ✅ Implémenté |
| **Challenge** | 10% des tâches double-exécutées, résultats comparés | ✅ Détecte les résultats incorrects |

**Ce qui reste problématique** :

1. **Tâches longues non checkpointées** : Un calcul de 30 minutes perdu à 29 minutes si le worker se déconnecte. Le protocole Worker v2.1 prévoit un *Checkpoint Manager* (section 14), mais pas encore implémenté. Sans checkpointing, le coût de la déconnexion augmente linéairement avec la durée de la tâche.

2. **Workers navigateur = temps de session imprévisible** : Un onglet peut durer 5 secondes ou 8 heures. Aucune garantie. Le champ `expected_remaining_sec` dans les capabilities est auto-déclaratif et peu fiable.

3. **Pas de mécanisme de « grace period »** : Quand un worker détecte qu'il va être tué (page unload), il devrait notifier le coordinateur et libérer ses tâches. L'événement `beforeunload` est peu fiable (les navigateurs limitent ce qu'on peut y faire).

**Recommandation** : Implémenter le Checkpoint Manager (déjà spécifié dans le protocole v2.1) pour les tâches longues. Pour les tâches courtes (< 30s), le modèle actuel (timeout + requeue) est suffisant.

---

### 2.5 État : Stateless (FaaS) ou Stateful ?

**L'architecture Scrapower est fondamentalement stateless, et c'est la bonne décision.**

| Modèle | Description | Réaliste pour Scrapower ? |
|--------|-------------|---------------------------|
| **FaaS pur (stateless)** | Fonctions sans état, inputs/outputs via blob store. Chaque exécution est indépendante. | ✅ C'est le modèle actuel. Naturellement adapté aux workers éphémères. |
| **Stateful avec caches** | Workers gardent des données en RAM entre les tâches. | ⚠️ Possible pour les workers persistants (Oracle ARM, serveurs). Inutile pour les navigateurs. |
| **Base de données distribuée** | Workers partagent un état mutables (CRDT, consensus). | ❌ Impossible avec des workers éphémères. Le théorème CAP tue toute cohérence forte. |
| **Volumes persistants simulés** | Chaque worker a un « disque » simulé par blob store. | ⚠️ Théoriquement possible via le blob store content-addressed, mais lent (chaque lecture = téléchargement). |

**Le blob store content-addressed de Scrapower est la bonne abstraction pour le stockage** :
- Immutabilité : pas de corruption, pas de race condition
- Déduplication naturelle (SHA-256, ref counting)
- Workers téléchargent/pushent des blobs, le coordinateur est la source de vérité

**Ce qui manque** : un mécanisme de « sticky sessions » où un worker reçoit prioritairement des tâches qui utilisent les mêmes données (cache local). Pas critique pour v1.

---

## 3. Architectures de Pooling — Trois Schémas

### 3.1 Modèle FaaS Pur (fonctions sans état, style Lambda)

```
┌──────────┐    POST /tasks     ┌─────────────┐    task_assign    ┌──────────┐
│  Client  │ ─────────────────► │ Coordinator │ ────────────────► │  Worker  │
│          │ ◄───────────────── │  (FastAPI)  │ ◄──────────────── │ (nav/GHA)│
└──────────┘    GET /results    └──────┬──────┘    task_result    └──────────┘
                                       │
                                ┌──────┴──────┐
                                │  Blob Store │
                                │  (SHA-256)  │
                                └─────────────┘
```

**Fonctionnement** :
1. Client upload exécutable + input dans le blob store → obtient `executable_hash`, `input_hash`
2. Client POST `/tasks` avec ces hashs + `runtime`, `gpu_required`
3. Scheduler matche un worker compatible, lui envoie `task_assign` avec les hashs
4. Worker télécharge exécutable + input depuis le blob store, exécute, upload output
5. Worker envoie `task_result` avec `output_hash`
6. Client GET `/results/{id}` → télécharge l'output

**Avantages** :
- ✅ Déjà implémenté dans Scrapower v0.3
- ✅ Naturellement tolerant aux déconnexions (tâche atomique)
- ✅ Scaling horizontal trivial (plus de workers = plus de throughput)
- ✅ Sécurité forte (content-addressing + assignment tokens)

**Limites** :
- ❌ Pas de « pool de RAM » — chaque tâche est isolée
- ❌ Latence de transfert des blobs (télécharger un exécutable + input peut prendre du temps)
- ❌ Pas de communication inter-tâche (pas de shuffle, pas de reduce distribué)
- ❌ Taille d'input/output limitée par la RAM du worker

**Quand l'utiliser** : Travail par lots indépendants (rendu 3D, brute-force, inference ML indépendante, web scraping parallèle).

---

### 3.2 Modèle Cluster Virtuel (style K8s avec volumes persistants simulés)

```
┌──────────────────────────────────────────────────────────────┐
│                    "Serveur Virtuel Unifié"                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐   │
│  │  Worker  │  │  Worker  │  │  Worker  │  │  Worker     │   │
│  │  Nav 4GB │  │  GHA 7GB │  │  OCI 12GB│  │  Colab 12GB │   │
│  │ ┌──────┐ │  │ ┌──────┐ │  │ ┌──────┐ │  │ ┌──────┐   │   │
│  │ │VOL v1│ │  │ │VOL v2│ │  │ │VOL v3│ │  │ │VOL v4│   │   │
│  │ └──────┘ │  │ └──────┘ │  │ └──────┘ │  │ └──────┘   │   │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘   │
│       │             │              │              │           │
│       └─────────────┴──────────────┴──────────────┘           │
│                          │                                    │
│                   ┌──────┴──────┐                             │
│                   │ Coordinator │                             │
│                   │  + BlobStore│  ← "Disque dur virtuel"     │
│                   │  + Scheduler│                             │
│                   └─────────────┘                             │
└──────────────────────────────────────────────────────────────┘
```

**Fonctionnement** :
1. Le blob store est présenté comme un « filesystem distribué » (chaque blob = un fichier, hash = chemin)
2. Les workers déclarent un « volume » (portion de leur RAM/disk) mis à disposition du cluster
3. Le scheduler tente de placer les tâches sur les workers qui ont déjà les données en cache
4. Les données persistantes vivent dans le blob store ; les workers ont un cache local LRU
5. Un « filesystem virtuel » (FUSE-like over HTTP) permet à un worker de lire un fichier distant à la demande

**Avantages** :
- ✅ Illusion d'un « serveur » avec stockage persistant
- ✅ Cache local réduit la latence pour les tâches répétitives
- ✅ Abstraction familière (Unix filesystem)

**Limites** :
- ❌ **Trompeur** : la latence d'accès à un blob distant est de 50ms-5s, pas de 100µs comme un disque local. Illusion dangereuse.
- ❌ Pas de cohérence : si deux workers modifient le même fichier → conflit
- ❌ Complexité énorme pour un gain faible
- ❌ Les « volumes » ne survivent pas aux déconnexions des workers éphémères

**Conclusion** : **Déconseillé.** L'abstraction d'un « filesystem distribué » sur des workers éphémères est une illusion qui causera plus de bugs que de valeur. Le blob store content-addressed est la bonne abstraction.

---

### 3.3 Modèle Hybride (Stockage Permanent + Compute Éphémère) — **Recommandé**

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                   │
│  ┌──────────────────┐          ┌──────────────────┐              │
│  │  Stockage          │          │  Compute Pool      │              │
│  │  (Persistant)      │          │  (Éphémère)        │              │
│  │                    │          │                     │              │
│  │  Oracle ARM 12GB  │◄────────►│  Navigateur 4GB    │              │
│  │  (blob store +    │  HTTP/WS │  GitHub Actions 7GB│              │
│  │   PostgreSQL)     │          │  Colab GPU 12GB    │              │
│  │                    │          │  Kaggle GPU 30GB  │              │
│  │  Coordinator       │          │  Modal GPU credits │              │
│  │  FastAPI :8777     │          │  HuggingFace 16GB │              │
│  │                    │          │  Cloud Run burst   │              │
│  └──────────────────┘          └──────────────────┘              │
│                                                                   │
│  Le stockage est centralisé et fiable.                             │
│  Le compute est éphémère, hétérogène, gratuit, et jetable.         │
│  L'ensemble est présenté comme une API REST + WebSocket unifiée.   │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

**Fonctionnement** :
1. **Couche stockage persistante** (Oracle Cloud ARM + HuggingFace Spaces) :
   - Le coordinateur FastAPI + SQLite/PostgreSQL → état du cluster
   - Le blob store content-addressed → immuable, cache perpétuel
   - Optionnel : Redis pour files d'attente et cache chaud

2. **Couche compute éphémère** (navigateurs, GHA, Colab, Kaggle, Modal) :
   - Workers se connectent en WebSocket (mode A) ou HTTP pull (mode B)
   - Ils téléchargent exécutable + input, exécutent, uploadent output
   - Zéro état local persistant — ils sont jetables

3. **Abstraction utilisateur** :
   - **API FaaS** : `POST /tasks` → résultat (modèle actuel, fonctionnel)
   - **API Batch** : `POST /batch` → soumet N tâches, collecte les résultats
   - **API MapReduce** (futur) : `POST /mapreduce` → map phase (N workers) → shuffle (coordinateur) → reduce phase (M workers)
   - **API GPU** : `POST /tasks` avec `gpu_required: true`, le scheduler route vers WebGPU ou CUDA selon disponibilité

**Avantages** :
- ✅ Séparation claire des responsabilités : stockage fiable, compute jetable
- ✅ Le blob store est le seul état mutable (et il est append-only, immuable)
- ✅ Les workers éphémères peuvent apparaître/disparaître sans perte de données
- ✅ Extension naturelle : ajouter un nouveau type de worker = nouveau provider dans le harvester
- ✅ Le modèle FaaS + blob store est déjà implémenté

**Limites** :
- ❌ Pas de « serveur unifié » au sens traditionnel (pas de shell SSH, pas de filesystem cohérent, pas de mémoire partagée)
- ⚠️ La latence dépend du worker assigné (un navigateur Wi-Fi vs un Colab en datacenter)
- ⚠️ L'API MapReduce nécessite que le coordinateur fasse le shuffle (goulot d'étranglement)

**Pourquoi c'est la meilleure approche** : Elle maximise les forces de Scrapower (blob store content-addressed, scheduler hétérogène, workers zéro-friction) sans essayer de simuler une abstraction impossible (mémoire partagée, latence uniforme).

---

## 4. Verrous Identifiés

### 4.1 Violations des Conditions d'Utilisation (ToS)

**C'est le risque #1. La plupart des plateformes gratuites interdisent le « repurposing » comme backend de calcul.**

| Plateforme | Clause problématique | Niveau de risque |
|------------|---------------------|------------------|
| **Google Colab** | Interdit : « cryptocurrency mining », « uses that are primarily for computation » non interactif | 🔴 **Très élevé** — Bannissement automatique détecté par leurs heuristiques. Colab détecte les patterns non-interactifs. |
| **Kaggle** | Interdit : « mining cryptocurrency », « inappropriate automated access » | 🔴 **Élevé** — Kaggle est plus permissif que Colab, mais l'automatisation non-interactive de notebooks reste détectable. |
| **GitHub Actions** | Interdit : « cryptocurrency mining », « serverless computing », « using GHA as a free compute platform » | 🟠 **Modéré-élevé** — Les ToS interdisent explicitement l'utilisation comme plateforme de calcul. GHA doit être utilisé pour CI/CD. L'utilisation actuelle de Scrapower (harvester GitHub Actions) est une zone grise risquée. |
| **HuggingFace Spaces** | Pas d'interdiction explicite du compute distribué, mais ToS standard contre l'abus | 🟡 **Faible-modéré** — HF est plus tolérant pour le ML distribué. Mais l'utilisation comme worker générique pourrait être mal vue. |
| **Modal** | $30/mois de crédits gratuits. Usage légitime attendu. | 🟢 **Faible** — Le modèle freemium est explicite. Pas de violation si usage dans les limites. |
| **Oracle Cloud** | Idle reclamation policy (instances inactives > 7 jours → reclaim). Utilisation légitime. | 🟢 **Très faible** — Oracle Always Free est conçu pour être utilisé. |
| **Cloudflare Workers** | Interdit : « running computationally intensive tasks », « cryptocurrency mining », « excessive CPU usage » | 🔴 **Élevé** — CF Workers est conçu pour l'edge computing léger, pas le calcul intensif. |

**Stratégie de mitigation** :
1. **Prioriser les plateformes « légitimes »** : Oracle Cloud (toujours permis), Modal (crédits freemium), HuggingFace Spaces (ML toléré)
2. **Pour GitHub Actions** : Remplacer le harvester actuel par un mécanisme qui ne lance des workers que pendant les vrais workflows CI/CD (opportuniste, pas dédié)
3. **Pour Colab/Kaggle** : Usage uniquement via interaction utilisateur manuelle (pas de harvester automatique). La roadmap Scrapower mentionne déjà « Harvester Colab » (fichier `colab.py` existe) — c'est risqué.
4. **Ajouter un mode « opt-in explicit »** : Le worker vérifie que le propriétaire de la ressource a consenti (ex : OAuth GitHub = l'utilisateur autorise explicitement)

---

### 4.2 Sécurité : Workers Malveillants

| Menace | Mécanisme Scrapower actuel | Efficacité | Amélioration suggérée |
|--------|---------------------------|------------|----------------------|
| **Résultat falsifié** | Challenge 10% (double-exécution + comparaison SHA-256) | ⚠️ Détecte seulement 10% des fraudes. Un worker peut tricher 90% du temps. | Rendre le challenge adaptatif (roadmap v0.4) : nouveau worker = 100% challengé, fiable = 1%, suspect = 50%. |
| **Sybil (faux workers)** | Rate limiting 5 workers/IP + pas de mécanisme de réputation | ❌ Un attaquant peut créer des centaines de workers via proxies/VPN | Implémenter la réputation workers (roadmap v0.4) + preuve de travail à la connexion. |
| **Blob empoisonné** | Content-addressing SHA-256 : un worker ne peut pas modifier l'exécutable (le hash ne correspondrait plus) | ✅ Excellent | RAS. |
| **Worker qui vole l'output** | L'output est uploadé dans le blob store. Le worker ne peut pas empêcher le coordinateur de le lire. | ✅ OK si le blob store est sécurisé | Chiffrement bout-en-bout pour les données sensibles (futur). |
| **Déni de service** | Rate limiting 30 req/min/IP | ⚠️ Protège les endpoints REST, pas les WebSocket workers | Ajouter un mécanisme de « preuve de travail » (challenge cryptographique) pour accepter une connexion worker. |
| **Exécution de code malveillant** | WASM sandbox (wasmtime, timeout 30s, fuel 100M, RAM max 16 MB). Python : subprocess non sandboxé. | ✅ WASM excellent. 🔴 Python non sandboxé (ADR-009 reconnaît le risque). | Firejail/Docker pour Python (Phase 4, roadmap). Ne jamais exécuter de Python non sandboxé de workers non trustés. |

**Vulnérabilité critique actuelle** : Le mode « challenge 10% » est insuffisant pour un système ouvert. Un worker malveillant peut retourner des résultats faux 90% du temps sans détection. **La réputation adaptative (roadmap v0.4) est indispensable avant d'ouvrir à des workers anonymes.**

---

### 4.3 Limitations Physiques

| Limitation | Impact | Réalité |
|------------|--------|---------|
| **Bande passante** | Transférer un input de 500 MB à un worker navigateur prend plusieurs minutes sur une connexion domestique. | 🔴 Critique. Oracle Cloud a 10 TB/mois mais les navigateurs sont sur des connexions grand public (upload lent). |
| **Latence** | Un worker en Wi-Fi a 20-100ms de latence vers le coordinateur. Rend impossibles les algorithmes synchrones (MPI, barrier, shuffle distribué). | 🟠 Significatif. Forcer un modèle asynchrone. Pas de « barrières » entre workers. |
| **Pas de RDMA** | Pas de transfert direct worker-worker à faible latence. WebRTC (déjà implémenté, P2P) améliore mais reste > 10ms. | 🟡 Acceptable si pas de communication inter-worker synchrone. |
| **CPU hétérogènes** | Un navigateur sur un vieux PC vs un serveur Oracle ARM = performances 10× différentes. | 🟡 Acceptable avec timeouts adaptatifs. Le scheduler devrait estimer la capacité CPU (future). |
| **Stockage éphémère** | Un worker navigateur perd toutes ses données à la déconnexion. | ✅ Déjà géré par le modèle stateless. |

---

## 5. Recommandations pour Scrapower

### 5.1 Ce qu'il faut faire (priorités)

| Priorité | Action | Justification |
|----------|--------|---------------|
| **P0** | Implémenter la réputation adaptative (roadmap v0.4) | Sans réputation, le système est vulnérable à 90% de fraude non détectée. La réputation est un prérequis pour ouvrir aux workers anonymes. |
| **P0** | Remplacer le harvester GitHub Actions par un mode « opportuniste CI/CD » | Le harvester GHA actuel viole les ToS GitHub. Risque de bannissement du repo. Le remplacer par une intégration qui ne lance des workers que pendant les vrais workflows de CI. |
| **P1** | Implémenter le Checkpoint Manager (protocole v2.1, section 14) | Les tâches longues (> 5 min) sur workers éphémères sont trop risquées sans checkpointing. |
| **P1** | Adopter PostgreSQL en remplacement de SQLite pour le coordinateur | SQLite est excellent pour v0.1-v0.3, mais la concurrence d'accès (1 writer) deviendra un goulot. PostgreSQL (via Neon gratuit 500 MB ou Oracle Cloud auto-hébergé) est le move naturel. |
| **P1** | Ajouter le support Modal (crédits gratuits $30/mois) | Modal est la plateforme GPU gratuite la plus légitime et performante. Intégration Python native triviale. |
| **P2** | Sandbox Python avec Firejail/Docker | ADR-009 reconnaît le risque. À faire avant d'accepter des workers Python non trustés. |
| **P2** | Mode batch (POST /batch) pour soumettre N tâches d'un coup | Améliore l'UX et permet des optimisations de groupage. |
| **P3** | Web Crypto / Ed25519 pour signatures worker | Remplace le assignment token vulnérable au replay. Permet l'authentification forte des workers. |

### 5.2 Ce qu'il ne faut PAS faire

| À éviter | Pourquoi |
|----------|----------|
| **Simuler un « serveur unifié » avec mémoire partagée** | Illusion dangereuse. La latence et la volatilité des workers rendent toute cohérence forte impossible. Rester sur le modèle FaaS + blob store. |
| **Utiliser Colab/Kaggle en harvester automatique** | Violation directe des ToS. Bannissement quasi-certain. Ces plateformes doivent être utilisées uniquement en mode manuel opt-in. |
| **Exécuter du Python non sandboxé de workers non trustés** | Risque sécurité critique. Le mode « trusted » actuel est acceptable uniquement pour les workers que l'utilisateur contrôle lui-même. |
| **Poursuivre l'intégration Golem Network avant v1.0** | Golem est une distraction. Le marketplace GLM est mature mais la VM Golem n'est pas WASM. La roadmap mentionne Golem en v0.5 — repousser à v1.0+. |
| **Tout unifier en WASM** | PyTorch, NumPy, CUDA ne sont pas portables en WASM. La dichotomie WASM (universel/cpu/vérifiable) + Python (puissant/gpu/trusté) est la bonne architecture. |

### 5.3 Positionnement Stratégique

**Scrapower n'est pas un « serveur unifié ».** C'est un **bus de calcul distribué** (distributed compute bus) où :
- Le coordinateur est le bus de messages (tâches, résultats, blobs)
- Les workers sont des processeurs hétérogènes branchés sur le bus
- Le blob store est la mémoire partagée immuable

Présenter Scrapower comme un « serveur virtuel » serait une erreur marketing ET technique. Le modèle mental correct est :

> **Scrapower = AWS Lambda + S3, mais avec des workers gratuits et hétérogènes au lieu de serveurs AWS.**

C'est honnête, compréhensible, et techniquement exact.

### 5.4 Feuille de Route Révisée

```
v0.4 (sécurité) : Réputation workers, challenge adaptatif, Web Crypto
v0.5 (scale)    : Modal GPU, HuggingFace Spaces harvester, PostgreSQL, Sandbox Python
v0.6 (UX)       : Mode batch, SDK Python, dashboard temps réel
v0.7 (résilience): Checkpoint Manager, WebRTC P2P shuffle, cache LRU workers
v1.0 (écosystème): Fédération coordinateurs, IPFS blob store, marketplace crédits
```

---

## Références

- **BOINC** : Anderson, D. P. (2004). « BOINC: A System for Public-Resource Computing and Storage. » *Grid Computing.*
- **Golem Network** : [docs.golem.network](https://docs.golem.network)
- **Fluence Network** : [fluence.dev](https://fluence.dev) — Marine runtime WASM
- **Dfinity ICP** : [internetcomputer.org](https://internetcomputer.org)
- **WASI Preview 2** : [github.com/WebAssembly/WASI](https://github.com/WebAssembly/WASI)
- **WebGPU** : [gpuweb.github.io/gpuweb](https://gpuweb.github.io/gpuweb)
- **Ray** : Moritz et al. (2018). « Ray: A Distributed Framework for Emerging AI Applications. » *OSDI.*
- **HTCondor** : Thain, D. et al. (2005). « Distributed Computing in Practice: The Condor Experience. » *Concurrency and Computation.*
- **Scrapower Worker Protocol v2.1** : `docs/worker-protocol-v2.md`
- **Scrapower ADR-009** : `docs/adr-009-python-runtime.md`
- **Free Cloud Tiers 2026** : `research/free-cloud-tiers-2026.md`
- **GitHub Actions ToS** : [docs.github.com/en/site-policy](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service) — Section H (Acceptable Use)
- **Google Colab ToS** : [research.google.com/colaboratory/faq](https://research.google.com/colaboratory/faq.html)
