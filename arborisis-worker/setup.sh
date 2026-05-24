#!/bin/bash
set -e

echo "=========================================="
echo "  Arborisis Audio Worker - Installation  "
echo "=========================================="
echo ""

# Vérifier les arguments
if [ -z "$1" ]; then
    echo "Usage: $0 <WORKER_TOKEN> [API_URL]"
    echo ""
    echo "Exemple:"
    echo "  $0 abc123 https://arborisis.com"
    echo ""
    exit 1
fi

WORKER_TOKEN=$1
API_URL=${2:-"https://arborisis.com"}

# Vérifier Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker n'est pas installé."
    echo ""
    echo "Installation Docker:"
    echo "  - Ubuntu/Debian: curl -fsSL https://get.docker.com | sh"
    echo "  - macOS: brew install docker"
    echo "  - Windows: https://docs.docker.com/desktop/install/windows/"
    echo ""
    exit 1
fi

# Vérifier docker compose
if ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose n'est pas installé."
    exit 1
fi

echo "✅ Docker détecté"

# Créer le répertoire d'installation
INSTALL_DIR="$HOME/.arborisis-worker"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

echo "📁 Répertoire d'installation: $INSTALL_DIR"

# Télécharger les fichiers nécessaires
echo "📥 Téléchargement des fichiers..."

# Détection GPU
GPU_FLAGS=""
if command -v nvidia-smi &> /dev/null; then
    echo "✅ NVIDIA GPU detected: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
    GPU_FLAGS="
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility"
    echo "🎮 GPU support enabled"
else
    echo "⚠️  No GPU detected - will run in CPU mode"
    echo "   To enable GPU, install NVIDIA drivers and Docker toolkit"
fi

# Créer le docker-compose.yml
cat > docker-compose.yml << EOF
services:
  audio-worker:
    image: arborisis/audio-worker:latest
    restart: unless-stopped
    environment:
      - WORKER_TOKEN=${WORKER_TOKEN}
      - API_URL=${API_URL}
      - WORKER_NAME=$(hostname)
      - MODELS_DIR=/models
      - DOWNLOAD_MODELS=gemma-4,gemma-4-mini
      - INSTALL_GPU_DRIVERS=true
      - R2_ENDPOINT=${R2_ENDPOINT:-}
      - R2_ACCESS_KEY_ID=${R2_ACCESS_KEY_ID:-}
      - R2_SECRET_ACCESS_KEY=${R2_SECRET_ACCESS_KEY:-}
      - R2_BUCKET_NAME=${R2_BUCKET_NAME:-}${GPU_FLAGS}
    volumes:
      - ./data:/tmp/worker
      - ./logs:/app/logs
      - ./models:/models
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    deploy:
      resources:
        limits:
          memory: ${MEMORY_LIMIT:-4G}
        reservations:
          memory: ${MEMORY_RESERVATION:-1G}
EOF

echo "✅ Configuration créée"

# Créer les répertoires
mkdir -p data logs

# Démarrer le worker
echo ""
echo "🚀 Démarrage du worker..."
docker compose pull
docker compose up -d

echo ""
echo "=========================================="
echo "  ✅ Worker installé avec succès !       "
echo "=========================================="
echo ""
echo "Commandes utiles:"
echo "  cd $INSTALL_DIR"
echo "  docker compose logs -f    # Voir les logs"
echo "  docker compose ps         # Statut"
echo "  docker compose down       # Arrêter"
echo "  docker compose restart    # Redémarrer"
echo ""
echo "Le worker va maintenant:"
echo "  1. Se connecter à l'API Arborisis"
echo "  2. Détecter automatiquement vos capacités"
echo "  3. Traiter les analyses audio selon vos specs"
echo ""

# Afficher les capacités détectées
echo "💻 Capacités détectées:"
echo "  CPU: $(nproc) cœurs"
echo "  RAM: $(free -h | awk '/^Mem:/ {print $2}')"

if command -v nvidia-smi &> /dev/null; then
    echo "  GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
    echo "  VRAM: $(nvidia-smi --query-gpu=memory.total --format=csv,noheader | head -1)"
    echo "  Driver: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
else
    echo "  GPU: Non détecté (mode CPU)"
fi

echo ""
echo "🤖 Modèles IA qui seront téléchargés:"
echo "  - Gemma 4 (4GB) - Modèle principal"
echo "  - Gemma 4 Mini (2.5GB) - Version légère"
echo ""
echo "Les modèles seront téléchargés automatiquement au démarrage."

echo ""
echo "Pour plus d'informations: https://docs.arborisis.com/workers"

# Lancer les logs
echo ""
echo "Affichage des logs (Ctrl+C pour quitter)..."
docker compose logs -f