#!/bin/bash
set -e

echo "=========================================="
echo "  Arborisis AI/LLM Worker - Startup      "
echo "=========================================="
echo ""

# Fonction de logging
log_info() {
    echo "[INFO] $1"
}

log_warn() {
    echo "[WARN] $1"
}

log_error() {
    echo "[ERROR] $1"
}

# 1. Détection GPU
log_info "Detecting GPU..."

if command -v nvidia-smi &> /dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null || echo "")
    if [ -n "$GPU_INFO" ]; then
        log_info "NVIDIA GPU detected:"
        echo "$GPU_INFO" | while IFS=',' read -r name memory driver; do
            echo "  - Name: $(echo $name | xargs)"
            echo "  - Memory: $(echo $memory | xargs)"
            echo "  - Driver: $(echo $driver | xargs)"
        done
        export GPU_AVAILABLE=true
        export NVIDIA_VISIBLE_DEVICES=all
    else
        log_warn "nvidia-smi found but no GPU detected"
        export GPU_AVAILABLE=false
    fi
else
    log_warn "nvidia-smi not found"
    export GPU_AVAILABLE=false
fi

# 2. Installation des drivers GPU si nécessaire (Docker uniquement)
if [ "$GPU_AVAILABLE" = "false" ] && [ "${INSTALL_GPU_DRIVERS:-true}" = "true" ]; then
    if [ -f /.dockerenv ] || [ "$DOCKER_CONTAINER" = "true" ]; then
        log_info "Attempting to install NVIDIA drivers in container..."
        
        # Vérifier si on peut accéder au GPU via le host
        if [ -d /usr/local/cuda ] && [ -f /usr/local/cuda/lib64/libcudart.so ]; then
            log_info "CUDA toolkit found, configuring environment..."
            export LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}
            
            # Vérifier si nvidia-smi est disponible après configuration
            if command -v nvidia-smi &> /dev/null; then
                log_info "GPU now accessible after configuration"
                export GPU_AVAILABLE=true
            fi
        else
            log_warn "CUDA toolkit not found in container"
            log_info "To use GPU, run container with: --gpus all"
            log_info "Or install NVIDIA Container Toolkit on host"
        fi
    fi
fi

# 3. Vérification de la mémoire
MEMORY_GB=$(free -g | awk '/^Mem:/{print $2}')
log_info "System memory: ${MEMORY_GB}GB"

# 4. Vérification des modèles
MODELS_DIR="${MODELS_DIR:-/models}"
mkdir -p "$MODELS_DIR"

log_info "Checking models in $MODELS_DIR..."

# Vérifier quels modèles sont déjà présents
MODELS_TO_DOWNLOAD=""

if [ ! -f "$MODELS_DIR/gemma-4.gguf" ]; then
    log_warn "Gemma 4 model not found"
    MODELS_TO_DOWNLOAD="${MODELS_TO_DOWNLOAD}gemma-4,"
else
    MODEL_SIZE=$(du -h "$MODELS_DIR/gemma-4.gguf" | cut -f1)
    log_info "Gemma 4 model found (${MODEL_SIZE})"
fi

if [ ! -f "$MODELS_DIR/gemma-4-mini.gguf" ]; then
    log_warn "Gemma 4 Mini model not found"
    MODELS_TO_DOWNLOAD="${MODELS_TO_DOWNLOAD}gemma-4-mini,"
else
    MODEL_SIZE=$(du -h "$MODELS_DIR/gemma-4-mini.gguf" | cut -f1)
    log_info "Gemma 4 Mini model found (${MODEL_SIZE})"
fi

# 5. Démarrage du téléchargement en background
if [ -n "$MODELS_TO_DOWNLOAD" ]; then
    # Retirer la dernière virgule
    MODELS_TO_DOWNLOAD="${MODELS_TO_DOWNLOAD%,}"
    export DOWNLOAD_MODELS="$MODELS_TO_DOWNLOAD"
    
    log_info "Starting background download for: $MODELS_TO_DOWNLOAD"
    log_info "Models will be downloaded in background while worker starts"
    
    # Le téléchargement sera géré par model_manager.py au démarrage du worker
else
    log_info "All models are already downloaded"
fi

# 6. Configuration des variables d'environnement
export PYTHONUNBUFFERED=1
export CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0}

if [ "$GPU_AVAILABLE" = "true" ]; then
    log_info "GPU acceleration enabled"
    export LLAMA_CPP_GPU_LAYERS=${LLAMA_CPP_GPU_LAYERS:--1}
else
    log_info "Running in CPU mode"
    export LLAMA_CPP_GPU_LAYERS=0
fi

echo ""
echo "=========================================="
echo "  ✅ Environment configured"
echo "=========================================="
echo "  GPU: ${GPU_AVAILABLE:-false}"
echo "  Memory: ${MEMORY_GB}GB"
echo "  Models: ${MODELS_DIR}"
echo "  Downloading: ${MODELS_TO_DOWNLOAD:-None}"
echo ""
echo "  Starting worker..."
echo ""

# 7. Lancement du worker
exec "$@"
