#!/usr/bin/env python3
"""
Arborisis LLM Worker - Inférence distribuée pour modèles de langage
"""

import os
import sys
import time
import json
import logging
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

import requests
from dotenv import load_dotenv

from model_manager import get_model_manager, GPUDriverManager

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('llm_worker.log', encoding='utf-8')
    ]
)
logger = logging.getLogger('arborisis-llm')

load_dotenv()


class LlamaInference:
    """Wrapper pour l'inférence via llama.cpp Python bindings."""
    
    def __init__(self):
        self.model = None
        self.model_path = None
        self.is_loaded = False
        
    def load_model(self, model_path: str, n_gpu_layers: int = 0) -> bool:
        """Charge un modèle GGUF."""
        try:
            from llama_cpp import Llama
            
            logger.info(f"Chargement du modèle: {model_path}")
            
            # Détecter si GPU est disponible et optimiser
            if n_gpu_layers == -1:
                gpu_manager = GPUDriverManager()
                n_gpu_layers = gpu_manager.get_optimal_gpu_layers(8.0)  # 8GB par défaut
                logger.info(f"GPU layers auto-configured: {n_gpu_layers}")
            
            self.model = Llama(
                model_path=model_path,
                n_ctx=8192,
                n_gpu_layers=n_gpu_layers,
                verbose=False,
            )
            
            self.model_path = model_path
            self.is_loaded = True
            
            logger.info("Modèle chargé avec succès")
            return True
            
        except ImportError:
            logger.error("llama_cpp n'est pas installé. Installez-le avec: pip install llama-cpp-python")
            return False
        except Exception as e:
            logger.error(f"Erreur chargement modèle: {e}")
            return False
    
    def generate(self, prompt: str, max_tokens: int = 2048, 
                temperature: float = 0.7, top_p: float = 0.9) -> Dict[str, Any]:
        """Génère une réponse."""
        if not self.is_loaded:
            raise Exception("Modèle non chargé")
        
        start_time = time.time()
        
        try:
            output = self.model(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop=["<|im_end|>", "<|endoftext|>", "</s>"],
                echo=False,
            )
            
            processing_time = time.time() - start_time
            
            return {
                'text': output['choices'][0]['text'],
                'input_tokens': len(self.model.tokenize(prompt.encode())),
                'output_tokens': output['usage']['completion_tokens'] if 'usage' in output else len(output['choices'][0]['text'].split()),
                'processing_time_ms': int(processing_time * 1000),
                'tokens_per_second': output['usage']['completion_tokens'] / processing_time if 'usage' in output and processing_time > 0 else 0,
            }
            
        except Exception as e:
            logger.error(f"Erreur génération: {e}")
            raise


class LlmWorker:
    """Worker LLM pour le cluster distribué avec gestion automatique des modèles."""
    
    def __init__(self):
        self.token = os.getenv('WORKER_TOKEN')
        self.api_url = os.getenv('API_URL', 'https://arborisis.com')
        self.worker_name = os.getenv('WORKER_NAME', 'llm-worker')
        
        if not self.token:
            raise ValueError("WORKER_TOKEN non défini")
        
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }
        
        self.running = True
        self.inference_engine = LlamaInference()
        self.current_model = None
        
        # Initialiser le ModelManager (télécharge les modèles en background)
        logger.info("=== Initializing Model Manager ===")
        self.model_manager = get_model_manager(
            models_dir=os.getenv('MODELS_DIR', './models'),
            auto_install_gpu=os.getenv('INSTALL_GPU_DRIVERS', 'true').lower() == 'true'
        )
        
        # Attendre un peu que les téléchargements démarrent
        time.sleep(2)
        
        # Détecter les capacités
        self.capabilities = self._detect_capabilities()
        
        logger.info(f"=== Arborisis LLM Worker ===")
        logger.info(f"Capabilities: {json.dumps(self.capabilities, indent=2)}")
    
    def _detect_capabilities(self) -> List[str]:
        """Détecte les modèles disponibles avec le ModelManager."""
        capabilities = []
        
        # Utiliser le ModelManager pour vérifier les modèles
        available_models = self.model_manager.get_available_models()
        
        if 'gemma-4' in available_models:
            capabilities.append('gemma-4')
            capabilities.append('sylve')  # Alias
        
        if 'gemma-4-mini' in available_models:
            capabilities.append('gemma-4-mini')
            capabilities.append('sylve-mini')  # Alias
        
        # Vérifier GPU via le ModelManager
        gpu_info = self.model_manager.gpu_manager.detect_gpu()
        if gpu_info['has_nvidia'] and 'gemma-4' in available_models:
            capabilities.append('gemma-4-gpu')
            capabilities.append('sylve-gpu')  # Alias
            logger.info(f"GPU NVIDIA détecté: {gpu_info['gpus'][0]['name']}")
        
        # Si des modèles sont en cours de téléchargement
        downloading = self.model_manager.get_capabilities()['downloading']
        if downloading:
            logger.info(f"Models downloading in background: {', '.join(downloading)}")
        
        if not capabilities:
            logger.warning("Aucun modèle prêt. Les modèles sont en cours de téléchargement en background.")
            logger.info("Le worker traitera les jobs dès que les modèles seront disponibles.")
        
        return capabilities
    
    def heartbeat(self) -> bool:
        """Envoie un heartbeat au serveur."""
        try:
            response = requests.post(
                f'{self.api_url}/api/audio-workers/heartbeat',
                headers=self.headers,
                json={
                    'cpu_usage': 0,  # TODO: récupérer usage CPU
                    'memory_usage': 0,
                    'current_jobs': 1 if self.current_model else 0,
                },
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Heartbeat failed: {e}")
            return False
    
    def request_llm_job(self) -> Optional[Dict]:
        """Demande un job LLM au serveur."""
        try:
            response = requests.get(
                f'{self.api_url}/api/llm/worker/job',
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code == 204:
                return None
            
            data = response.json()
            return data.get('job')
            
        except Exception as e:
            logger.error(f"Failed to request LLM job: {e}")
            return None
    
    def submit_result(self, job_id: int, status: str, result: Dict, 
                     input_tokens: int, output_tokens: int, 
                     processing_time_ms: int) -> bool:
        """Soumet le résultat."""
        try:
            payload = {
                'status': status,
                'response': result.get('text', ''),
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'processing_time_ms': processing_time_ms,
            }
            
            if status == 'failed':
                payload['error_message'] = result.get('error', 'Unknown error')
            
            response = requests.post(
                f'{self.api_url}/api/llm/worker/jobs/{job_id}/result',
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            return response.status_code == 200
            
        except Exception as e:
            logger.error(f"Failed to submit result: {e}")
            return False
    
    def process_job(self, job: Dict) -> None:
        """Traite un job d'inférence LLM avec gestion automatique des modèles."""
        job_id = job['id']
        model_slug = job['model']
        prompt = job['prompt']
        metadata = job.get('metadata', {})
        
        logger.info(f"Processing LLM job {job_id} with model {model_slug}")
        
        start_time = time.time()
        
        try:
            # Mapper les slugs aux modèles connus
            model_slug_map = {
                'sylve': 'gemma-4',
                'sylve-mini': 'gemma-4-mini',
                'sylve-gpu': 'gemma-4-gpu',
                'gemma-4': 'gemma-4',
                'gemma-4-mini': 'gemma-4-mini',
                'gemma-4-gpu': 'gemma-4-gpu',
            }
            
            canonical_slug = model_slug_map.get(model_slug, model_slug)
            
            # Vérifier si le modèle est prêt (téléchargé)
            if not self.model_manager.is_model_ready(canonical_slug):
                logger.info(f"Model {canonical_slug} not ready, waiting for download...")
                
                # Attendre le téléchargement (timeout: 5 minutes)
                if not self.model_manager.wait_for_model(canonical_slug, timeout=300):
                    raise Exception(f"Modèle {model_slug} non disponible après attente du téléchargement")
            
            # Récupérer le chemin du modèle
            model_path = self.model_manager.get_model_path(canonical_slug)
            
            if not model_path:
                raise Exception(f"Modèle {model_slug} non trouvé après téléchargement")
            
            # Déterminer le nombre de layers GPU optimal
            n_gpu_layers = self.model_manager.get_optimal_gpu_layers(canonical_slug)
            
            # Forcer GPU si demandé explicitement
            if model_slug in ['sylve-gpu', 'gemma-4-gpu']:
                n_gpu_layers = -1  # Tout sur GPU
            
            logger.info(f"Using {n_gpu_layers} GPU layers for {model_slug}")
            
            # Charger si pas déjà chargé ou si différent
            if not self.inference_engine.is_loaded or \
               self.inference_engine.model_path != str(model_path) or \
               (model_slug in ['sylve-gpu', 'gemma-4-gpu'] and n_gpu_layers == 0):
                
                if not self.inference_engine.load_model(str(model_path), n_gpu_layers):
                    raise Exception("Impossible de charger le modèle")
            
            # Exécuter l'inférence
            result = self.inference_engine.generate(
                prompt=prompt,
                max_tokens=metadata.get('max_tokens', 2048),
                temperature=metadata.get('temperature', 0.7),
                top_p=metadata.get('top_p', 0.9),
            )
            
            processing_time_ms = result['processing_time_ms']
            
            logger.info(f"Job {job_id} completed in {processing_time_ms}ms")
            
            # Soumettre le résultat
            self.submit_result(
                job_id=job_id,
                status='completed',
                result=result,
                input_tokens=result['input_tokens'],
                output_tokens=result['output_tokens'],
                processing_time_ms=processing_time_ms,
            )
            
        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            self.submit_result(
                job_id=job_id,
                status='failed',
                result={'error': str(e)},
                input_tokens=0,
                output_tokens=0,
                processing_time_ms=processing_time_ms,
            )
        
        finally:
            self.current_model = None
    
    def run(self) -> None:
        """Boucle principale."""
        logger.info("Starting LLM Worker...")
        logger.info(f"Available models: {self.capabilities}")
        
        try:
            while self.running:
                # Heartbeat
                self.heartbeat()
                
                # Demander un job
                if not self.current_model:
                    job = self.request_llm_job()
                    
                    if job:
                        self.current_model = job['model']
                        self.process_job(job)
                
                # Attendre avant prochain cycle
                time.sleep(5)
                
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.running = False
        
        logger.info("LLM Worker stopped")


def main():
    """Point d'entrée."""
    if not os.getenv('WORKER_TOKEN'):
        print("Erreur: WORKER_TOKEN non défini")
        print("Usage: WORKER_TOKEN=xxx python3 llm_worker.py")
        sys.exit(1)
    
    worker = LlmWorker()
    worker.run()


if __name__ == '__main__':
    main()