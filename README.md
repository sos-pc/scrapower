# Scrapower

> **Agrégateur de calcul distribué** — exécute des tâches WASM et Python sur
> des workers éphémères (Kaggle GPU, navigateurs, HuggingFace Spaces).

```
POST /transcribe {url: "youtube..."}
  → Coordinator (Oracle) → yt-dlp via VPN → blob audio
  → Worker Kaggle T4 GPU → faster-whisper turbo → transcript
```

## Quickstart

```bash
git clone https://github.com/sos-pc/scrapower && cd scrapower
cp .env.example .env   # éditer les secrets
docker compose up -d --build
```

Transcrire une vidéo :
```bash
curl -X POST https://scrapower.talos-int.com/transcribe \
  -H "X-API-Key: $API_KEY" \
  -d '{"url":"https://youtu.be/...","model":"turbo","format":"txt"}'

curl https://scrapower.talos-int.com/results/{task_id} -H "X-API-Key: $API_KEY"
```

## Architecture

- 📖 [ARCHITECTURE.md](ARCHITECTURE.md) — Documentation complète
- 📋 [ROADMAP.md](ROADMAP.md) — Prochaines étapes

**Protocole principal : Mode B (HTTP pull/submit).** Les workers pollent via HTTP, pas de connexion persistante. Idéal pour workers éphémères (Kaggle, Lambda).

**Task lifecycle:** `PENDING → DOWNLOADING → QUEUED → ASSIGNED → COMPLETED`

**Workers actifs :** Kaggle (T4 GPU, 30 GB RAM), HuggingFace Spaces, navigateurs (WASM/WebGPU).

## Stack

| Composant | Technologie |
|---|---|
| Coordinator | FastAPI + SQLite + aiosqlite |
| Worker runtime | faster-whisper (CUDA, batched), wasmtime |
| VPN | OpenVPN + Dante SOCKS5 (CyberGhost) |
| YouTube | yt-dlp + deno JS runtime |
| Déploiement | Docker Compose, Oracle Cloud ARM |
| Harvester | Kaggle CLI, round-robin 2 comptes |

## Endpoints

| Méthode | Chemin | Description |
|---|---|---|
| POST | `/transcribe` | Transcription YouTube → texte |
| GET | `/transcribe/models` | Modèles Whisper (tiny…large-v3) |
| GET | `/results/{id}` | Résultat d'une tâche |
| POST | `/tasks` | Tâche générique WASM/Python |
| GET | `/tasks/{id}` | Statut d'une tâche |
| PUT | `/blobs` | Upload blob (SHA-256) |
| GET | `/blobs/{hash}` | Download blob |
| POST | `/worker/pull` | Mode B : worker pull une tâche |
| POST | `/worker/submit` | Mode B : worker rend son résultat |
| WS | `/worker/ws` | Mode A : connexion navigateur |
| GET | `/stats` | Capacité infrastructure |
| GET | `/health` | Health check |

## Licence

MIT
