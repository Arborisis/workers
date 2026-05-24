#!/usr/bin/env python3
"""
Arborisis Audio Worker - Client robuste avec analyse audio adaptative
"""

import os
import sys
import time
import json
import logging
import tempfile
import shutil
import signal
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

import psutil
import platform
from dotenv import load_dotenv

from config import AdaptiveConfig
from audio_analyzer import AudioAnalyzer
from infrastructure import (
    RobustR2Storage, 
    RobustAPIClient, 
    WorkerStats, 
    HealthCheckServer,
    JobContext,
    JobStatus,
    RetryableError,
    FatalError
)

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


class ArborisisWorker:
    """Worker client robuste pour le traitement audio distribué."""
    
    def __init__(self):
        self.token = os.getenv('WORKER_TOKEN')
        self.api_url = os.getenv('API_URL', 'https://arborisis.com')
        self.worker_name = os.getenv('WORKER_NAME', platform.node())
        self.worker_id = os.getenv('WORKER_ID')
        
        if not self.token:
            raise ValueError("WORKER_TOKEN non défini")
        
        self.running = True
        self.shutdown_requested = False
        self.config = AdaptiveConfig.get_full_config()
        self.analyzer = AudioAnalyzer(self.config)
        self.storage = RobustR2Storage()
        self.api = RobustAPIClient(self.api_url, self.token)
        self.stats = WorkerStats()
        self.health_server = HealthCheckServer(port=int(os.getenv('WORKER_PORT', 8080)))
        
        self.temp_dir = tempfile.mkdtemp(prefix="arborisis_worker_")
        self.active_jobs: Dict[int, JobContext] = {}
        
        # Gestion des signaux
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
        logger.info(f"=== Arborisis Audio Worker v2.0 ===")
        logger.info(f"Name: {self.worker_name}")
        logger.info(f"Config: {json.dumps(self.config, indent=2)}")
    
    def _signal_handler(self, signum, frame):
        """Gère les signaux d'arrêt."""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.shutdown_requested = True
        self.running = False
    
    def get_system_info(self) -> Dict[str, Any]:
        """Récupère les informations système."""
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
        payload['capabilities'] = [c for c in payload['capabilities'] if c]
        
        data = self.api.register(payload)
        if data:
            self.worker_id = data['worker']['id']
            logger.info(f"Worker registered: {self.worker_id}")
            return True
        
        logger.error("Registration failed")
        return False
    
    def heartbeat_cycle(self) -> None:
        """Envoie un heartbeat et récupère les jobs en attente."""
        info = self.get_system_info()
        
        response = self.api.heartbeat(
            cpu_usage=info['cpu_usage'],
            memory_usage=info['memory_usage'],
            current_jobs=len(self.active_jobs)
        )
        
        if response and response.get('pending_jobs'):
            for job_data in response['pending_jobs']:
                self._queue_job(job_data)
    
    def _queue_job(self, job_data: Dict) -> None:
        """Ajoute un job à la file d'attente."""
        assignment_id = job_data['assignment_id']
        
        if assignment_id in self.active_jobs:
            return
        
        context = JobContext(
            assignment_id=assignment_id,
            analysis_id=job_data['analysis_id'],
            r2_key=job_data['r2_key'],
            parameters=job_data.get('parameters', {}),
            status=JobStatus.PENDING
        )
        
        self.active_jobs[assignment_id] = context
        logger.info(f"Queued job {assignment_id} for analysis {context.analysis_id}")
    
    def request_job(self) -> bool:
        """Demande un nouveau job au serveur."""
        if len(self.active_jobs) >= self.config['max_concurrent_jobs']:
            return False
        
        job = self.api.request_job()
        if job:
            self._queue_job(job)
            return True
        
        return False
    
    def process_job(self, context: JobContext) -> None:
        """Traite un job avec gestion d'erreurs robuste."""
        context.status = JobStatus.DOWNLOADING
        context.start_time = time.time()
        local_path = None
        
        try:
            # 1. Télécharger le fichier
            logger.info(f"[{context.assignment_id}] Downloading {context.r2_key}")
            local_path = os.path.join(self.temp_dir, f"audio_{context.analysis_id}_{context.assignment_id}.wav")
            
            if not self.storage.download(context.r2_key, local_path):
                raise RetryableError("Failed to download audio file")
            
            # Vérifier la taille
            file_size_mb = os.path.getsize(local_path) / (1024 * 1024)
            if file_size_mb > self.config['max_file_size_mb']:
                raise FatalError(f"File too large: {file_size_mb:.1f}MB > {self.config['max_file_size_mb']}MB")
            
            context.local_path = local_path
            context.status = JobStatus.ANALYZING
            
            # 2. Analyse audio
            logger.info(f"[{context.assignment_id}] Analyzing audio...")
            results = self.analyzer.analyze(local_path, context.analysis_id, context.parameters)
            
            if results.get('status') == 'failed':
                raise Exception(results.get('error', 'Analysis failed'))
            
            context.status = JobStatus.UPLOADING
            
            # 3. Upload des résultats
            logger.info(f"[{context.assignment_id}] Uploading results...")
            uploaded_files = {}
            
            for file_type, file_path in results.get('files', {}).items():
                if file_path and os.path.exists(file_path):
                    r2_result_key = f"sounds/analysis/{context.analysis_id}/{file_type}"
                    
                    content_types = {
                        'spectrogram': 'image/webp',
                        'birdnet': 'application/json',
                        'preview': 'audio/mpeg'
                    }
                    
                    if self.storage.upload(file_path, r2_result_key, content_types.get(file_type)):
                        uploaded_files[file_type] = r2_result_key
            
            # 4. Soumettre le résultat
            processing_time = int(time.time() - context.start_time)
            context.status = JobStatus.COMPLETED
            
            success = self.api.submit_result(
                assignment_id=context.assignment_id,
                status='completed',
                processing_time=processing_time,
                results={
                    'files': uploaded_files,
                    'metadata': results.get('metadata'),
                    'features': results.get('features'),
                    'detections_count': len(results.get('detections', [])),
                    'detections': results.get('detections', [])[:50]
                }
            )
            
            if success:
                self.stats.record_success(processing_time)
                logger.info(f"[{context.assignment_id}] Completed in {processing_time}s")
            else:
                raise RetryableError("Failed to submit results")
            
        except RetryableError as e:
            context.attempts += 1
            context.error_message = str(e)
            
            if context.attempts < context.max_attempts:
                context.status = JobStatus.RETRYING
                logger.warning(f"[{context.assignment_id}] Retryable error, attempt {context.attempts}/{context.max_attempts}: {e}")
            else:
                context.status = JobStatus.FAILED
                self._submit_failure(context, str(e))
                
        except FatalError as e:
            context.status = JobStatus.FAILED
            context.error_message = str(e)
            self._submit_failure(context, str(e))
            logger.error(f"[{context.assignment_id}] Fatal error: {e}")
            
        except Exception as e:
            context.status = JobStatus.FAILED
            context.error_message = str(e)
            self._submit_failure(context, str(e))
            logger.error(f"[{context.assignment_id}] Unexpected error: {e}", exc_info=True)
        
        finally:
            # Nettoyage
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except:
                    pass
            
            # Retirer de la liste des jobs actifs si terminé ou en échec
            if context.status in [JobStatus.COMPLETED, JobStatus.FAILED]:
                if context.assignment_id in self.active_jobs:
                    del self.active_jobs[context.assignment_id]
    
    def _submit_failure(self, context: JobContext, error: str) -> None:
        """Soumet un échec au serveur."""
        processing_time = int(time.time() - context.start_time) if context.start_time else 0
        
        self.api.submit_result(
            assignment_id=context.assignment_id,
            status='failed',
            processing_time=processing_time,
            error=error
        )
        
        self.stats.record_failure(error)
    
    def process_pending_jobs(self) -> None:
        """Traite les jobs en attente."""
        jobs_to_process = [
            ctx for ctx in self.active_jobs.values()
            if ctx.status in [JobStatus.PENDING, JobStatus.RETRYING]
        ]
        
        for context in jobs_to_process:
            if self.shutdown_requested:
                break
            
            self.process_job(context)
            
            # Petite pause entre les jobs
            time.sleep(1)
    
    def run(self) -> None:
        """Boucle principale du worker."""
        logger.info("Starting Arborisis Audio Worker...")
        
        # Démarrer le serveur de health check
        self.health_server.stats = self.stats
        self.health_server.start()
        
        # Enregistrement
        if not self.worker_id:
            if not self.register():
                logger.error("Registration failed, exiting")
                sys.exit(1)
        
        logger.info("Worker ready! Waiting for jobs...")
        
        try:
            while self.running:
                # Heartbeat
                self.heartbeat_cycle()
                
                # Demander un nouveau job si capacité disponible
                if len(self.active_jobs) < self.config['max_concurrent_jobs']:
                    self.request_job()
                
                # Traiter les jobs en attente
                self.process_pending_jobs()
                
                # Attendre avant le prochain cycle
                time.sleep(30)
                
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        
        finally:
            self._shutdown()
        
        logger.info("Worker stopped")
    
    def _shutdown(self) -> None:
        """Arrêt gracieux."""
        logger.info("Shutting down gracefully...")
        
        # Attendre que les jobs en cours se terminent (max 60s)
        if self.active_jobs:
            logger.info(f"Waiting for {len(self.active_jobs)} active jobs to complete...")
            start_wait = time.time()
            
            while self.active_jobs and (time.time() - start_wait) < 60:
                time.sleep(1)
        
        # Nettoyage
        self._cleanup()
    
    def _cleanup(self) -> None:
        """Nettoie les ressources."""
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
                logger.info("Cleaned up temp directory")
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")


def main():
    """Point d'entrée principal."""
    required_vars = ['WORKER_TOKEN']
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        print("❌ Erreur: Variables d'environnement manquantes:")
        for var in missing:
            print(f"   - {var}")
        print("")
        print("Usage:")
        print("  export WORKER_TOKEN=votre_token")
        print("  export API_URL=https://arborisis.com")
        print("  python3 worker.py")
        print("")
        print("Variables optionnelles:")
        print("  R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME")
        sys.exit(1)
    
    worker = ArborisisWorker()
    worker.run()


if __name__ == '__main__':
    main()