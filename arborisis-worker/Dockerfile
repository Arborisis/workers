# Image de base NVIDIA CUDA pour support GPU
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04 AS base

# Variables d'environnement
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_DEFAULT_TIMEOUT=300
ENV PIP_RETRIES=10
ENV CUDA_HOME=/usr/local/cuda
ENV PATH=${CUDA_HOME}/bin:${PATH}
ENV LD_LIBRARY_PATH=${CUDA_HOME}/lib64:${LD_LIBRARY_PATH}

# Étape 1: Installation des dépendances système et GPU
FROM base AS dependencies

RUN apt-get update && apt-get install -y --no-install-recommends --allow-change-held-packages \
    python3.11 \
    python3-pip \
    python3.11-dev \
    build-essential \
    cmake \
    git \
    curl \
    wget \
    ffmpeg \
    libsndfile1 \
    pciutils \
    # Outils NVIDIA
    nvidia-utils-525 \
    cuda-toolkit-12-1 \
    # Pour compilation llama.cpp avec CUDA
    libcublas-12-1 \
    libcublas-dev-12-1 \
    && rm -rf /var/lib/apt/lists/*

# Mettre à jour pip
RUN pip3 install --upgrade pip setuptools wheel

# Étape 2: Installation de llama-cpp-python avec support CUDA
FROM dependencies AS llm-deps

# Variables pour compiler llama-cpp-python avec CUDA
ENV CMAKE_ARGS="-DLLAMA_CUDA=on -DLLAMA_CUDA_FORCE_MMQ=ON"
ENV FORCE_CMAKE=1

# Installer les dépendances Python LLM
RUN pip3 install --no-cache-dir \
    llama-cpp-python==0.2.90 \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121

# Étape 3: Installation des dépendances audio
FROM llm-deps AS audio-deps

WORKDIR /app

# Copier et installer requirements audio
COPY requirements.txt .
RUN pip3 install --no-cache-dir --prefer-binary -r requirements.txt

# Étape 4: Installation de BirdNET-Analyzer
FROM audio-deps AS birdnet-deps

RUN git clone --depth 1 https://github.com/kahst/BirdNET-Analyzer.git /opt/birdnet && \
    pip3 install --no-cache-dir --prefer-binary -e /opt/birdnet

# Étape 5: Copie du code et téléchargement des modèles
FROM birdnet-deps AS final

WORKDIR /app

# Créer le répertoire des modèles
RUN mkdir -p /models

# Copier tout le code Python
COPY *.py ./

# Télécharger les modèles pendant le build (si DOWNLOAD_MODELS_BUILD est défini)
ARG DOWNLOAD_MODELS_BUILD=true
ARG MODELS_DIR=/models

RUN if [ "$DOWNLOAD_MODELS_BUILD" = "true" ]; then \
    echo "📥 Pré-téléchargement des modèles..." && \
    mkdir -p ${MODELS_DIR} && \
    # Gemma 4 (Q4_K_M)
    wget -q --show-progress -O ${MODELS_DIR}/gemma-4.gguf \
        "https://huggingface.co/TheBloke/Llama-2-7B-Chat-GGUF/resolve/main/llama-2-7b-chat.Q4_K_M.gguf" || \
        echo "⚠️  Gemma-4 download failed (will retry at runtime)", \
    # Gemma 4 Mini (Q4_K_S) \
    wget -q --show-progress -O ${MODELS_DIR}/gemma-4-mini.gguf \
        "https://huggingface.co/TheBloke/Llama-2-7B-Chat-GGUF/resolve/main/llama-2-7b-chat.Q4_K_S.gguf" || \
        echo "⚠️  Gemma-4-mini download failed (will retry at runtime)"; \
    else \
    echo "⏭️  Skipping model download during build"; \
    fi

# Créer les répertoires de travail
RUN mkdir -p /tmp/worker /app/logs

# Variables d'environnement par défaut
ENV MODELS_DIR=/models
ENV DOWNLOAD_MODELS=gemma-4,gemma-4-mini
ENV INSTALL_GPU_DRIVERS=true
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD python3 -c "import requests; requests.get('http://localhost:8080/health')" || exit 1

# Créer le script d'entrée directement dans l'image
RUN cat << 'ENTRYPOINT_EOF' > /usr/local/bin/docker-entrypoint.sh
#!/bin/bash
set -e

echo "=========================================="
echo "  Arborisis AI/LLM Worker - Startup      "
echo "=========================================="
echo ""

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
        
        if [ -d /usr/local/cuda ] && [ -f /usr/local/cuda/lib64/libcudart.so ]; then
            log_info "CUDA toolkit found, configuring environment..."
            export LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}
            
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
    MODELS_TO_DOWNLOAD="${MODELS_TO_DOWNLOAD%,}"
    export DOWNLOAD_MODELS="$MODELS_TO_DOWNLOAD"
    
    log_info "Starting background download for: $MODELS_TO_DOWNLOAD"
    log_info "Models will be downloaded in background while worker starts"
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
ENTRYPOINT_EOF

RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python3", "worker.py"]
