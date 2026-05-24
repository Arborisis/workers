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

RUN apt-get update && apt-get install -y --no-install-recommends \
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

# Point d'entrée qui gère l'installation GPU et le téléchargement
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python3", "worker.py"]
