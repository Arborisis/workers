FROM python:3.11-slim

# Éviter les buffers Python
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_DEFAULT_TIMEOUT=300
ENV PIP_RETRIES=10

# Mettre à jour pip
RUN pip install --upgrade pip

# Dépendances système
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Installer BirdNET-Analyzer
RUN git clone --depth 1 https://github.com/kahst/BirdNET-Analyzer.git /opt/birdnet && \
    pip install --no-cache-dir --prefer-binary -e /opt/birdnet

# Répertoire de travail
WORKDIR /app

# Copier les dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# Copier le code
COPY *.py ./

# Créer le répertoire de travail
RUN mkdir -p /tmp/worker

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python3 -c "import requests; requests.get('http://localhost:8080/health')" || exit 1

# Commande par défaut
CMD ["python3", "worker.py"]