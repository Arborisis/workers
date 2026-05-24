#!/usr/bin/env python3
"""
Model Manager - Gestionnaire de modèles IA avec téléchargement background
et installation automatique des drivers GPU.
"""

import os
import sys
import time
import json
import hashlib
import logging
import subprocess
import threading
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from urllib.parse import urlparse
import requests

logger = logging.getLogger('arborisis-model-manager')


@dataclass
class ModelInfo:
    """Informations sur un modèle."""
    name: str
    slug: str
    url: str
    filename: str
    size_mb: Optional[int] = None
    checksum: Optional[str] = None
    gpu_layers: int = 0  # -1 = all layers on GPU
    required_ram_gb: float = 8.0
    required_vram_gb: float = 0.0  # 0 = CPU only
    description: str = ""


class GPUDriverManager:
    """Gère la détection et l'installation des drivers GPU."""
    
    NVIDIA_RUNTIME_DEBIAN = [
        # Détection de la carte
        "apt-get update",
        "apt-get install -y --no-install-recommends pciutils",
        "lspci | grep -i nvidia || echo 'No NVIDIA GPU detected'",
        # Installation drivers
        "apt-get install -y --no-install-recommends \
            linux-headers-$(uname -r) \
            build-essential \
            dkms",
        # Ajout du repository NVIDIA
        "curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
        "curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list",
        "apt-get update",
        "apt-get install -y --no-install-recommends nvidia-container-toolkit nvidia-container-runtime",
        # Réglage du runtime Docker
        "nvidia-ctk runtime configure --runtime=docker || true",
    ]
    
    @classmethod
    def detect_gpu(cls) -> Dict[str, Any]:
        """Détecte les GPU disponibles."""
        gpus = []
        
        # NVIDIA
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=index,name,memory.total,memory.free,driver_version', 
                 '--format=csv,noheader'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        parts = [p.strip() for p in line.split(',')]
                        if len(parts) >= 5:
                            gpus.append({
                                'vendor': 'nvidia',
                                'index': parts[0],
                                'name': parts[1],
                                'total_memory': parts[2],
                                'free_memory': parts[3],
                                'driver_version': parts[4],
                            })
                logger.info(f"NVIDIA GPU(s) detected: {len(gpus)}")
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        # AMD (via rocm-smi)
        try:
            result = subprocess.run(
                ['rocm-smi', '--showproductname'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                logger.info("AMD GPU detected via ROCm")
                gpus.append({'vendor': 'amd', 'name': 'AMD GPU'})
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        # Intel (via intel-gpu-top ou similar)
        try:
            result = subprocess.run(
                ['intel_gpu_top', '-L'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                logger.info("Intel GPU detected")
                gpus.append({'vendor': 'intel', 'name': 'Intel GPU'})
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        return {
            'gpus': gpus,
            'count': len(gpus),
            'has_nvidia': any(g['vendor'] == 'nvidia' for g in gpus),
            'has_amd': any(g['vendor'] == 'amd' for g in gpus),
            'has_intel': any(g['vendor'] == 'intel' for g in gpus),
        }
    
    @classmethod
    def install_nvidia_drivers(cls, force: bool = False) -> bool:
        """Installe les drivers NVIDIA dans un conteneur Docker."""
        if not force and cls.detect_gpu()['has_nvidia']:
            logger.info("NVIDIA GPU already detected with drivers")
            return True
        
        logger.info("Installing NVIDIA drivers in container...")
        
        # Vérifier si on est dans un conteneur Docker
        in_docker = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER', '') == 'true'
        
        if not in_docker:
            logger.warning("Not in Docker container. Please install drivers on host.")
            return False
        
        try:
            for cmd in cls.NVIDIA_RUNTIME_DEBIAN:
                logger.info(f"Running: {cmd}")
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=300
                )
                if result.returncode != 0:
                    logger.warning(f"Command failed: {cmd}\n{result.stderr}")
            
            logger.info("NVIDIA container toolkit installed. Please restart container.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to install NVIDIA drivers: {e}")
            return False
    
    @classmethod
    def get_optimal_gpu_layers(cls, model_vram_gb: float) -> int:
        """Détermine le nombre optimal de layers GPU."""
        gpu_info = cls.detect_gpu()
        
        if not gpu_info['has_nvidia']:
            return 0
        
        try:
            # Récupérer la VRAM disponible
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                free_vram_mb = int(result.stdout.strip().split('\n')[0].strip())
                free_vram_gb = free_vram_mb / 1024
                
                # Si assez de VRAM, tout mettre sur GPU
                if free_vram_gb >= model_vram_gb * 1.2:  # 20% de marge
                    return -1  # Tout sur GPU
                elif free_vram_gb >= model_vram_gb * 0.5:
                    return 24  # La moitié sur GPU
                else:
                    return 12  # Quelques layers
        except:
            pass
        
        return 0


class ModelDownloader:
    """Télécharge les modèles avec reprise et vérification."""
    
    CHUNK_SIZE = 8192  # 8KB
    PROGRESS_INTERVAL = 5  # Afficher la progression toutes les 5 secondes
    
    def __init__(self, models_dir: str = "./models"):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._download_threads: Dict[str, threading.Thread] = {}
        self._download_progress: Dict[str, Dict] = {}
        
    def _verify_checksum(self, filepath: Path, expected_checksum: str) -> bool:
        """Vérifie le checksum SHA256 d'un fichier."""
        if not expected_checksum:
            return True
        
        sha256 = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        
        return sha256.hexdigest().lower() == expected_checksum.lower()
    
    def download_model(self, model: ModelInfo, 
                      progress_callback: Optional[Callable] = None,
                      background: bool = True) -> bool:
        """Télécharge un modèle avec option background."""
        filepath = self.models_dir / model.filename
        
        # Vérifier si déjà présent
        if filepath.exists():
            if model.checksum and self._verify_checksum(filepath, model.checksum):
                logger.info(f"Model {model.name} already downloaded and verified")
                return True
            else:
                logger.info(f"Model {model.name} exists but checksum mismatch, re-downloading")
        
        if background:
            # Téléchargement en background
            thread = threading.Thread(
                target=self._download_sync,
                args=(model, filepath, progress_callback),
                daemon=True,
                name=f"download-{model.slug}"
            )
            self._download_threads[model.slug] = thread
            thread.start()
            logger.info(f"Started background download for {model.name}")
            return True
        else:
            return self._download_sync(model, filepath, progress_callback)
    
    def _download_sync(self, model: ModelInfo, filepath: Path,
                      progress_callback: Optional[Callable] = None) -> bool:
        """Téléchargement synchrone."""
        self._download_progress[model.slug] = {
            'status': 'downloading',
            'downloaded': 0,
            'total': 0,
            'speed': 0,
            'start_time': time.time(),
            'last_update': time.time(),
        }
        
        try:
            logger.info(f"Downloading {model.name} from {model.url}")
            
            # Headers pour la reprise
            headers = {}
            if filepath.exists():
                headers['Range'] = f'bytes={filepath.stat().st_size}-'
                logger.info(f"Resuming download from {filepath.stat().st_size} bytes")
            
            response = requests.get(model.url, headers=headers, stream=True, timeout=300)
            response.raise_for_status()
            
            # Taille totale
            total_size = int(response.headers.get('content-length', 0))
            if filepath.exists() and 'content-range' in response.headers:
                # Reprise
                total_size += filepath.stat().st_size
            
            self._download_progress[model.slug]['total'] = total_size
            
            # Mode d'écriture
            mode = 'ab' if filepath.exists() and 'content-range' in response.headers else 'wb'
            downloaded = filepath.stat().st_size if filepath.exists() and mode == 'ab' else 0
            
            with open(filepath, mode) as f:
                for chunk in response.iter_content(chunk_size=self.CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        self._download_progress[model.slug]['downloaded'] = downloaded
                        
                        # Mettre à jour la vitesse
                        now = time.time()
                        elapsed = now - self._download_progress[model.slug]['start_time']
                        if elapsed > 0:
                            self._download_progress[model.slug]['speed'] = downloaded / elapsed
                        
                        # Callback de progression
                        if progress_callback and (now - self._download_progress[model.slug]['last_update']) > self.PROGRESS_INTERVAL:
                            progress_callback(model.slug, downloaded, total_size)
                            self._download_progress[model.slug]['last_update'] = now
            
            # Vérifier le checksum
            if model.checksum and not self._verify_checksum(filepath, model.checksum):
                logger.error(f"Checksum mismatch for {model.name}")
                filepath.unlink()
                self._download_progress[model.slug]['status'] = 'failed'
                return False
            
            self._download_progress[model.slug]['status'] = 'completed'
            logger.info(f"Successfully downloaded {model.name} ({filepath.stat().st_size / (1024**2):.1f} MB)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to download {model.name}: {e}")
            self._download_progress[model.slug]['status'] = 'failed'
            return False
    
    def is_download_complete(self, slug: str) -> bool:
        """Vérifie si le téléchargement est terminé."""
        if slug not in self._download_progress:
            return False
        return self._download_progress[slug]['status'] == 'completed'
    
    def get_progress(self, slug: str) -> Optional[Dict]:
        """Récupère la progression du téléchargement."""
        return self._download_progress.get(slug)
    
    def wait_for_download(self, slug: str, timeout: Optional[float] = None) -> bool:
        """Attend la fin du téléchargement."""
        start = time.time()
        while True:
            if self.is_download_complete(slug):
                return True
            
            if timeout and (time.time() - start) > timeout:
                logger.warning(f"Timeout waiting for download {slug}")
                return False
            
            time.sleep(1)


class ModelManager:
    """Gestionnaire principal des modèles IA."""
    
    # Catalogues de modèles disponibles
    MODELS_CATALOG = {
        'gemma-4': ModelInfo(
            name='Gemma 4',
            slug='gemma-4',
            url='https://huggingface.co/TheBloke/Llama-2-7B-Chat-GGUF/resolve/main/llama-2-7b-chat.Q4_K_M.gguf',
            filename='gemma-4.gguf',
            size_mb=4000,
            required_ram_gb=8.0,
            required_vram_gb=0.0,
            gpu_layers=-1,
            description='Modèle de langage Gemma 4 (7B params) quantifié Q4_K_M'
        ),
        'gemma-4-mini': ModelInfo(
            name='Gemma 4 Mini',
            slug='gemma-4-mini',
            url='https://huggingface.co/TheBloke/Llama-2-7B-Chat-GGUF/resolve/main/llama-2-7b-chat.Q4_K_S.gguf',
            filename='gemma-4-mini.gguf',
            size_mb=2500,
            required_ram_gb=4.0,
            required_vram_gb=0.0,
            gpu_layers=0,
            description='Version légère de Gemma 4 (7B params) quantifié Q4_K_S'
        ),
        'gemma-4-gpu': ModelInfo(
            name='Gemma 4 GPU',
            slug='gemma-4-gpu',
            url='https://huggingface.co/TheBloke/Llama-2-7B-Chat-GGUF/resolve/main/llama-2-7b-chat.Q4_K_M.gguf',
            filename='gemma-4.gguf',
            size_mb=4000,
            required_ram_gb=8.0,
            required_vram_gb=6.0,
            gpu_layers=-1,
            description='Gemma 4 optimisé pour GPU (tous les layers sur GPU)'
        ),
    }
    
    def __init__(self, models_dir: str = "./models", auto_install_gpu: bool = True):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.auto_install_gpu = auto_install_gpu
        self.downloader = ModelDownloader(models_dir)
        self.gpu_manager = GPUDriverManager()
        self._available_models: Dict[str, ModelInfo] = {}
        self._loading_status: Dict[str, str] = {}
        
        # Vérifier et installer les drivers GPU si nécessaire
        if auto_install_gpu:
            self._setup_gpu()
        
        # Démarrer le téléchargement des modèles en background
        self._start_background_downloads()
    
    def _setup_gpu(self) -> None:
        """Configure le GPU (installation drivers si nécessaire)."""
        gpu_info = self.gpu_manager.detect_gpu()
        
        if gpu_info['has_nvidia']:
            logger.info(f"NVIDIA GPU detected: {gpu_info['gpus'][0]['name']}")
            return
        
        # Vérifier si on est dans Docker
        in_docker = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER', '') == 'true'
        
        if in_docker and os.environ.get('INSTALL_GPU_DRIVERS', 'true').lower() == 'true':
            logger.info("No GPU detected in Docker container, attempting to install drivers...")
            success = self.gpu_manager.install_nvidia_drivers(force=False)
            if success:
                logger.info("GPU drivers installed successfully. Please restart the container.")
            else:
                logger.warning("Could not install GPU drivers. Running in CPU mode.")
        elif not in_docker and not gpu_info['has_nvidia']:
            logger.info("No GPU detected on host. Running in CPU mode.")
    
    def _start_background_downloads(self) -> None:
        """Démarre le téléchargement des modèles en background."""
        # Récupérer la liste des modèles à télécharger depuis les variables d'env
        models_to_download = os.environ.get('DOWNLOAD_MODELS', 'gemma-4,gemma-4-mini').split(',')
        
        for model_slug in models_to_download:
            model_slug = model_slug.strip()
            if model_slug in self.MODELS_CATALOG:
                model = self.MODELS_CATALOG[model_slug]
                self._loading_status[model_slug] = 'downloading'
                self.downloader.download_model(model, background=True)
                logger.info(f"Queued background download for {model.name}")
            else:
                logger.warning(f"Unknown model: {model_slug}")
    
    def get_model_path(self, slug: str) -> Optional[Path]:
        """Récupère le chemin d'un modèle."""
        if slug not in self.MODELS_CATALOG:
            return None
        
        model = self.MODELS_CATALOG[slug]
        filepath = self.models_dir / model.filename
        
        if filepath.exists():
            return filepath
        
        return None
    
    def is_model_ready(self, slug: str) -> bool:
        """Vérifie si un modèle est prêt (téléchargé)."""
        return self.get_model_path(slug) is not None
    
    def wait_for_model(self, slug: str, timeout: float = 300.0) -> bool:
        """Attend qu'un modèle soit prêt."""
        if slug not in self.MODELS_CATALOG:
            return False
        
        # Si déjà téléchargé
        if self.is_model_ready(slug):
            return True
        
        # Attendre le téléchargement
        logger.info(f"Waiting for model {slug} to download (timeout: {timeout}s)...")
        return self.downloader.wait_for_download(slug, timeout)
    
    def get_available_models(self) -> List[str]:
        """Liste les modèles disponibles localement."""
        available = []
        for slug, model in self.MODELS_CATALOG.items():
            if self.is_model_ready(slug):
                available.append(slug)
        return available
    
    def get_capabilities(self) -> Dict[str, Any]:
        """Retourne les capacités du système en matière de modèles."""
        gpu_info = self.gpu_manager.detect_gpu()
        
        return {
            'gpu': gpu_info,
            'available_models': self.get_available_models(),
            'downloading': [
                slug for slug, status in self._loading_status.items()
                if status == 'downloading'
            ],
            'can_run_gemma_4': self.is_model_ready('gemma-4'),
            'can_run_gemma_4_gpu': self.is_model_ready('gemma-4') and gpu_info['has_nvidia'],
            'can_run_gemma_4_mini': self.is_model_ready('gemma-4-mini'),
        }
    
    def get_optimal_gpu_layers(self, slug: str) -> int:
        """Détermine le nombre optimal de layers GPU pour un modèle."""
        if slug not in self.MODELS_CATALOG:
            return 0
        
        model = self.MODELS_CATALOG[slug]
        return self.gpu_manager.get_optimal_gpu_layers(model.required_vram_gb)


# Singleton pour l'application
_model_manager: Optional[ModelManager] = None


def get_model_manager(models_dir: str = "./models", auto_install_gpu: bool = True) -> ModelManager:
    """Récupère l'instance singleton du ModelManager."""
    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager(models_dir, auto_install_gpu)
    return _model_manager


if __name__ == '__main__':
    # Test du module
    logging.basicConfig(level=logging.INFO)
    
    manager = ModelManager()
    print(f"Capabilities: {json.dumps(manager.get_capabilities(), indent=2)}")
    
    # Attendre un peu pour voir les téléchargements démarrer
    time.sleep(2)
    
    print(f"Progress: {manager.downloader.get_progress('gemma-4')}")
