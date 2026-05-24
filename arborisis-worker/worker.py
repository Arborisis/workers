#!/usr/bin/env python3
"""
Arborisis Audio Worker - Client complet avec analyse audio adaptative
"""

import os
import sys
import time
import json
import logging
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

import requests
import boto3
from botocore.config import Config
from dotenv import load_dotenv

from config import AdaptiveConfig
from audio_analyzer import AudioAnalyzer

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('worker.log', encoding='utf-8')
    ]
)
logger = logging.getLogger('arborisis-worker')

load_dotenv()


class R2Storage:
    """Client de stockage R2 (S3-compatible)."""
    
    def __init__(self):
        self.client = boto3.client(
            's3',
            endpoint_url=os.getenv('R2_ENDPOINT'),
            aws_access_key_id=os.getenv('R2_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('R2_SECRET_ACCESS_KEY'),
            config=Config(signature_version='s3v4'),
            region_name='auto'
        )
        self.bucket = os.getenv('R2_BUCKET_NAME')
    
    def download(self, r2_key: str, local_path: str) -> bool:
        """Télécharge un fichier depuis R2."""
        try:
            self.client.download_file(self.bucket, r2_key, local_path)
            logger.info(f"Downloaded {r2_key} to {local_path}")
            return True
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return False
    
    def upload(self, local_path: str, r2_key: str, content_type: Optional[str] = None) -> bool:
        """Upload un fichier vers R2."""
        try:
            extra_args = {}
            if content_type:
                extra_args['ContentType'] = content_type
            
            self.client.upload_file(local_path, self.bucket, r2_key, ExtraArgs=extra_args)
            logger.info(f"Uploaded {local_path} to {r2_key}")
            return True
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            return False


class ArborisisWorker:
    """Worker client pour le traitement audio distribué."""
    
    def __init__(self):
        self.token = os.getenv('WORKER_TOKEN')
        self.api_url = os.getenv('API_URL', 'https://arborisis.com')
        self.worker_name = os.getenv('WORKER_NAME', 'unknown')
        self.worker_id = os.getenv('WORKER_ID')
        
        if not self.token:
            raise ValueError("WORKER_TOKEN non défini")
        
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        self.running = True
        self.current_jobs = {}
        self.config = AdaptiveConfig.get_full_config()
        self.analyzer = AudioAnalyzer(self.config)
        self.storage = R2Storage()
        self.temp_dir = tempfile.mkdtemp(prefix="arborisis_worker_")
        
        logger.info(f"Worker initialized with config: {json.dumps(self.config, indent=2)}")
    
    def get_system_info(self) -> Dict[str, Any]:
        """Récupère les informations système."""
        import psutil
        import platform
        
        return {
            'cpu_cores': psutil.cpu_count(logical=True),
            'memory_gb': round(psutil.virtual_memory().total / (1024**3)),
            'cpu_usage': psutil.cpu_percent(interval=1),
            'memory_usage': psutil.virtual_memory().percent,
            'os': f"{platform.system()} {platform.release()}"
        }
    
    def register(self) -> bool:
        """Enregistre le worker auprès du serveur."""
        if self.worker_id:
            return True
        
        import platform
        
        payload = {
            'name': self.worker_name,
            'hostname': platform.node(),
            'cpu_cores': self.config['capabilities']['cpu_cores'],
            'memory_gb': self.config['capabilities']['memory_gb'],
            'has_gpu': self.config['capabilities']['has_gpu'],
            'gpu_model': self.config['capabilities']['gpu_model'],
            'os': self.config['capabilities']['os'],
            'capabilities': [
                'audio-analysis',
                'birdnet' if self.config['birdnet']['enabled'] else None,
                'spectrogram',
                'feature-extraction',
                'preview-generation'
            ]
        }
        
        # Nettoyer les None
        payload['capabilities'] = [c for c in payload['capabilities'] if c]
        
        try:
            response = requests.post(
                f'{self.api_url}/api/audio-workers',
                headers=self.headers,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            self.worker_id = data['worker']['id']
            logger.info(f"Worker registered: {self.worker_id}")
            return True
        except Exception as e:
            logger.error(f"Registration failed: {e}")
            return False
    
    def send_heartbeat(self) -> bool:
        """Envoie un heartbeat au serveur."""
        info = self.get_system_info()
        
        payload = {
            'cpu_usage': info['cpu_usage'],
            'memory_usage': info['memory_usage'],
            'current_jobs': len(self.current_jobs),
            'ip_address': self.get_ip_address(),
            'port': int(os.getenv('WORKER_PORT', 8080))
        }
        
        try:
            response = requests.post(
                f'{self.api_url}/api/audio-workers/heartbeat',
                headers=self.headers,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Heartbeat failed: {e}")
            return False
    
    def get_ip_address(self) -> str:
        """Récupère l'IP publique."""
        try:
            response = requests.get('https://api.ipify.org?format=json', timeout=5)
            return response.json().get('ip', '127.0.0.1')
        except:
            return '127.0.0.1'
    
    def request_job(self) -> Optional[Dict[str, Any]]:
        """Demande un nouveau job."""
        try:
            response = requests.get(
                f'{self.api_url}/api/audio-workers/job',
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code == 204:
                return None
            
            response.raise_for_status()
            data = response.json()
            return data.get('job')
        except Exception as e:
            logger.error(f"Job request failed: {e}")
            return None
    
    def process_job(self, job: Dict[str, Any]) -> None:
        """Traite un job d'analyse audio."""
        assignment_id = job['assignment_id']
        analysis_id = job['analysis_id']
        r2_key = job['r2_key']
        parameters = job.get('parameters', {})
        
        logger.info(f"Processing job {assignment_id} for analysis {analysis_id}")
        
        start_time = time.time()
        local_path = None
        
        try:
            # 1. Télécharger le fichier audio
            local_path = os.path.join(self.temp_dir, f"audio_{analysis_id}.wav")
            if not self.storage.download(r2_key, local_path):
                raise Exception("Failed to download audio file")
            
            # 2. Vérifier la taille
            file_size_mb = os.path.getsize(local_path) / (1024 * 1024)
            if file_size_mb > self.config['max_file_size_mb']:
                raise Exception(f"File too large: {file_size_mb:.1f}MB > {self.config['max_file_size_mb']}MB")
            
            # 3. Analyse audio
            results = self.analyzer.analyze(local_path, analysis_id, parameters)
            
            # 4. Upload des résultats
            uploaded_files = {}
            
            for file_type, file_path in results.get('files', {}).items():
                if file_path and os.path.exists(file_path):
                    r2_result_key = f"sounds/analysis/{analysis_id}/{file_type}"
                    
                    content_types = {
                        'spectrogram': 'image/webp',
                        'birdnet': 'application/json',
                        'preview': 'audio/mpeg'
                    }
                    
                    if self.storage.upload(file_path, r2_result_key, content_types.get(file_type)):
                        uploaded_files[file_type] = r2_result_key
            
            # 5. Soumettre le résultat
            processing_time = int(time.time() - start_time)
            
            self.submit_result(
                assignment_id=assignment_id,
                status='completed',
                processing_time=processing_time,
                results={
                    'files': uploaded_files,
                    'metadata': results.get('metadata'),
                    'features': results.get('features'),
                    'detections_count': len(results.get('detections', [])),
                    'detections': results.get('detections', [])[:50]  # Limiter à 50 détections
                }
            )
            
            logger.info(f"Job {assignment_id} completed in {processing_time}s")
            
        except Exception as e:
            processing_time = int(time.time() - start_time)
            logger.error(f"Job {assignment_id} failed: {e}")
            
            self.submit_result(
                assignment_id=assignment_id,
                status='failed',
                processing_time=processing_time,
                error=str(e)
            )
        
        finally:
            # Nettoyage
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except:
                    pass
    
    def submit_result(self, assignment_id: int, status: str, processing_time: int,
                     results: Optional[Dict] = None, error: Optional[str] = None) -> bool:
        """Envoie le résultat au serveur."""
        payload = {
            'status': status,
            'processing_time_seconds': processing_time,
            'results': results,
            'error_message': error
        }
        
        try:
            response = requests.post(
                f'{self.api_url}/api/audio-workers/assignments/{assignment_id}/result',
                headers=self.headers,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Result submission failed: {e}")
            return False
    
    def run(self) -> None:
        """Boucle principale."""
        logger.info("=== Arborisis Audio Worker ===")
        logger.info(f"Name: {self.worker_name}")
        logger.info(f"Config: {json.dumps(self.config, indent=2)}")
        
        # Enregistrement
        if not self.worker_id:
            if not self.register():
                logger.error("Registration failed, exiting")
                sys.exit(1)
        
        logger.info("Worker ready! Waiting for jobs...")
        
        try:
            while self.running:
                # Heartbeat
                self.send_heartbeat()
                
                # Demander un job si capacité disponible
                if len(self.current_jobs) < self.config['max_concurrent_jobs']:
                    job = self.request_job()
                    if job:
                        self.process_job(job)
                
                # Attendre avant le prochain cycle
                time.sleep(30)
                
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.running = False
        
        finally:
            self._cleanup()
        
        logger.info("Worker stopped")
    
    def _cleanup(self) -> None:
        """Nettoie les ressources."""
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")


def main():
    """Point d'entrée principal."""
    if not os.getenv('WORKER_TOKEN'):
        print("Erreur: WORKER_TOKEN non défini")
        print("Usage: WORKER_TOKEN=votre_token API_URL=https://arborisis.com python3 worker.py")
        print("")
        print("Variables d'environnement requises:")
        print("  WORKER_TOKEN        Token d'authentification")
        print("  API_URL            URL de l'API (défaut: https://arborisis.com)")
        print("  R2_ENDPOINT        Endpoint R2 (ex: https://xxx.r2.cloudflarestorage.com)")
        print("  R2_ACCESS_KEY_ID   Clé d'accès R2")
        print("  R2_SECRET_ACCESS_KEY  Clé secrète R2")
        print("  R2_BUCKET_NAME     Nom du bucket R2")
        sys.exit(1)
    
    worker = ArborisisWorker()
    worker.run()


if __name__ == '__main__':
    main()