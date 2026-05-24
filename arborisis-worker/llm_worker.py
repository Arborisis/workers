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
    """Worker LLM pour le cluster distribué."""
    
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
        self.capabilities = self._detect_capabilities()
        
        logger.info(f"=== Arborisis LLM Worker ===")
        logger.info(f"Capabilities: {json.dumps(self.capabilities, indent=2)}")
    
    def _detect_capabilities(self) -> List[str]:
        """Détecte les modèles disponibles."""
        capabilities = []
        
        # Vérifier les modèles disponibles
        models_dir = os.getenv('MODELS_DIR', './models')
        
        if os.path.exists(os.path.join(models_dir, 'sylve.gguf')):
            capabilities.append('sylve')
        
        if os.path.exists(os.path.join(models_dir, 'sylve-mini.gguf')):
            capabilities.append('sylve-mini')
        
        # Vérifier GPU
        try:
            import subprocess
            result = subprocess.run(['nvidia-smi'], capture_output=True)
            if result.returncode == 0:
                capabilities.append('sylve-gpu')
                logger.info("GPU NVIDIA détecté")
        except:
            pass
        
        if not capabilities:
            logger.warning("Aucun modèle trouvé dans ./models/")
            logger.info("Téléchargez un modèle GGUF dans ./models/ pour commencer")
        
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
        """Traite un job d'inférence LLM."""
        job_id = job['id']
        model_slug = job['model']
        prompt = job['prompt']
        metadata = job.get('metadata', {})
        
        logger.info(f"Processing LLM job {job_id} with model {model_slug}")
        
        start_time = time.time()
        
        try:
            # Charger le modèle si nécessaire
            models_dir = os.getenv('MODELS_DIR', './models')
            
            model_paths = {
                'sylve': os.path.join(models_dir, 'sylve.gguf'),
                'sylve-mini': os.path.join(models_dir, 'sylve-mini.gguf'),
                'sylve-gpu': os.path.join(models_dir, 'sylve.gguf'),
            }
            
            model_path = model_paths.get(model_slug)
            
            if not model_path or not os.path.exists(model_path):
                raise Exception(f"Modèle {model_slug} non trouvé")
            
            # Déterminer le nombre de layers GPU
            n_gpu_layers = -1 if model_slug == 'sylve-gpu' else 0
            
            # Charger si pas déjà chargé ou si différent
            if not self.inference_engine.is_loaded or \
               self.inference_engine.model_path != model_path or \
               (model_slug == 'sylve-gpu' and n_gpu_layers == 0):
                
                if not self.inference_engine.load_model(model_path, n_gpu_layers):
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