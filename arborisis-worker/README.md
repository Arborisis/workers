# Arborisis Audio Worker

Worker client pour le traitement audio distribué de la plateforme Arborisis.

## Fonctionnalités

- **Analyse audio complète** : BirdNET (classification d'oiseaux), features librosa, spectrogrammes
- **Adaptation automatique** : Détecte les specs de la machine et ajuste la complexité
- **Gestion R2** : Téléchargement/Upload automatique depuis/vers Cloudflare R2
- **Connexion sécurisée** : Tunnel Cloudflare pour les machines domestiques
- **Monitoring** : Heartbeat, statistiques de performance

## Installation rapide

### Méthode 1 : Script d'installation (Recommandé)

```bash
# Récupérer votre token depuis le dashboard Arborisis (/audio-workers)
export WORKER_TOKEN="votre-token-ici"
export API_URL="https://arborisis.com"

# Optionnel : Configurer R2 (pour le stockage des résultats)
export R2_ENDPOINT="https://xxx.r2.cloudflarestorage.com"
export R2_ACCESS_KEY_ID="xxx"
export R2_SECRET_ACCESS_KEY="xxx"
export R2_BUCKET_NAME="arborisis-audio"

# Lancer l'installation
curl -fsSL https://arborisis.com/api/audio-workers/setup-script | bash
```

### Méthode 2 : Docker

```bash
docker run -d \
  -e WORKER_TOKEN=xxx \
  -e API_URL=https://arborisis.com \
  -e R2_ENDPOINT=xxx \
  -e R2_ACCESS_KEY_ID=xxx \
  -e R2_SECRET_ACCESS_KEY=xxx \
  -e R2_BUCKET_NAME=arborisis-audio \
  --name arborisis-worker \
  arborisis/audio-worker:latest
```

### Méthode 3 : Manuelle

```bash
# 1. Cloner le repo
git clone https://github.com/Arborisis/audio-services.git
cd audio-services/workers/arborisis-worker

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Installer BirdNET
git clone --depth 1 https://github.com/kahst/BirdNET-Analyzer.git /opt/birdnet
pip install -e /opt/birdnet

# 4. Configurer
cp .env.example .env
# Éditer .env avec vos paramètres

# 5. Lancer
python3 worker.py
```

## Configuration

Créez un fichier `.env` :

```env
# Requis
WORKER_TOKEN=votre-token-ici
API_URL=https://arborisis.com

# R2 Storage (pour upload des résultats)
R2_ENDPOINT=https://xxx.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=xxx
R2_SECRET_ACCESS_KEY=xxx
R2_BUCKET_NAME=arborisis-audio

# Optionnel
WORKER_NAME=Ma Machine
WORKER_PORT=8080
```

## Adaptation automatique

Le worker détecte automatiquement vos capacités :

| Specs | Comportement |
|-------|-------------|
| **RAM < 4GB** | Features légères, pas de BirdNET |
| **RAM 4-8GB** | Features standard, BirdNET basique |
| **RAM 8-16GB** | Features complètes, BirdNET avancé |
| **RAM > 16GB** | Tout activé, qualité maximale |
| **GPU détecté** | Accélération deep learning |

## Monitoring

```bash
# Logs en temps réel
docker compose logs -f

# Statistiques du worker
curl http://localhost:8080/stats

# Santé
curl http://localhost:8080/health
```

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Dashboard     │────▶│   API Laravel    │────▶│  Dispatch       │
│   Web UI        │     │                  │     │  Service        │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │                           │
                               ▼                           ▼
                        ┌──────────────────┐     ┌─────────────────┐
                        │  Audio Workers   │     │  SoundAnalysis  │
                        │  (DB)            │     │  Jobs Queue     │
                        └──────────────────┘     └─────────────────┘
                               │                           │
                               └───────────┬───────────────┘
                                           ▼
                                    ┌─────────────────┐
                                    │  Worker Clients │
                                    │  (Votre machine)│
                                    └─────────────────┘
```

## Développement

```bash
# Lancer en mode développement
python3 worker.py

# Tests
pytest tests/

# Build Docker
docker build -t arborisis/audio-worker:latest .
```

## License

MIT - Arborisis Team