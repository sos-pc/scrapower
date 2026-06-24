# Scrapower — HuggingFace Spaces Worker

Ajoute **16 GB de RAM CPU always-on** au pool Scrapower, gratuitement,
via un Docker Space HuggingFace.

## Déploiement (3 étapes)

### 1. Créer un Space Docker sur HuggingFace

- Aller sur https://huggingface.co/new-space
- Choisir **Docker** comme SDK
- Nom : `scrapower-worker` (ou ce que tu veux)
- Visibilité : Public (obligatoire pour le plan gratuit)

### 2. Configurer les variables d'environnement

Dans les settings du Space, ajouter :

| Variable | Valeur | Obligatoire |
|----------|--------|-------------|
| `COORDINATOR_URL` | `wss://your-coordinator.example.com/worker/ws` | Oui |
| `SCRAPOWER_API_KEY` | `your-api-key` | Non (pour auth_level=1) |
| `WORKER_ID` | `hf-montpellier-01` | Non (auto-généré) |

### 3. Pusher les fichiers

```bash
git clone https://huggingface.co/spaces/TON_USERNAME/scrapower-worker
cd scrapower-worker
cp /chemin/vers/scrapower/deploy/hf-spaces/Dockerfile .
cp /chemin/vers/scrapower/deploy/hf-spaces/app.py .
cp -r /chemin/vers/scrapower/src/scrapower/worker .
git add . && git commit -m "Scrapower worker" && git push
```

Le Space build automatiquement et le worker se connecte au coordinateur.

## Vérifier que ça marche

```bash
# Sur le coordinateur, vérifier que le worker est connecté
curl https://your-coordinator.example.com/stats | jq '.workers'
```

Le worker apparaît avec `ram_mb: 16384` et `availability_profile: "always_on"`.

## Capacités déclarées

| Ressource | Valeur |
|-----------|--------|
| RAM | 16 384 MB |
| CPU | 2 cœurs |
| Disque | 50 GB |
| Runtimes | WASM, Python |
| Dispo | always_on (persistant) |
| Tâches max | 600 secondes |
| Concurrence | 2 tâches simultanées |

## Notes

- **ToS :** HuggingFace tolère le compute distribué, surtout dans un cadre ML.
  Ce worker est légitime.
- **Idle timeout :** HF ne tue pas les Spaces Docker inactifs (contrairement à
  Render ou Fly.io). Le worker reste connecté 24/7.
- **Limite :** Pas de GPU sur le plan gratuit HF Spaces.
