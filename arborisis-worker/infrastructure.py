import os
import time
import json
import logging
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum

import requests
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger('arborisis-worker')


class JobStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    ANALYZING = "analyzing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class JobContext:
    assignment_id: int
    analysis_id: int
    r2_key: str
    parameters: Dict[str, Any]
    status: JobStatus
    attempts: int = 0
    max_attempts: int = 3
    start_time: Optional[float] = None
    error_message: Optional[str] = None
    local_path: Optional[str] = None


class RetryableError(Exception):
    """Erreur qui peut être retry."""
    pass


class FatalError(Exception):
    """Erreur fatale, ne pas retry."""
    pass


class RobustR2Storage:
    """Client R2 avec retry logic intégrée."""
    
    def __init__(self, max_retries: int = 3, retry_delay: float = 2.0):
        self.client = boto3.client(
            's3',
            endpoint_url=os.getenv('R2_ENDPOINT'),
            aws_access_key_id=os.getenv('R2_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('R2_SECRET_ACCESS_KEY'),
            config=Config(
                signature_version='s3v4',
                retries={'max_attempts': max_retries, 'mode': 'adaptive'}
            ),
            region_name='auto'
        )
        self.bucket = os.getenv('R2_BUCKET_NAME')
        self.max_retries = max_retries
        self.retry_delay = retry_delay
    
    def download(self, r2_key: str, local_path: str) -> bool:
        """Télécharge un fichier depuis R2 avec retry."""
        for attempt in range(self.max_retries):
            try:
                logger.info(f"Downloading {r2_key} (attempt {attempt + 1}/{self.max_retries})")
                self.client.download_file(self.bucket, r2_key, local_path)
                
                # Vérifier que le fichier existe et a une taille > 0
                if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                    logger.info(f"Downloaded {r2_key} ({os.path.getsize(local_path)} bytes)")
                    return True
                else:
                    raise RetryableError("Downloaded file is empty or missing")
                    
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                if error_code in ['NoSuchKey', 'AccessDenied']:
                    raise FatalError(f"R2 access error: {error_code}")
                
                logger.warning(f"Download attempt {attempt + 1} failed: {error_code}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    raise RetryableError(f"Failed to download after {self.max_retries} attempts")
            
            except Exception as e:
                logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    raise RetryableError(f"Failed to download: {e}")
        
        return False
    
    def upload(self, local_path: str, r2_key: str, content_type: Optional[str] = None) -> bool:
        """Upload un fichier vers R2 avec retry."""
        for attempt in range(self.max_retries):
            try:
                extra_args = {}
                if content_type:
                    extra_args['ContentType'] = content_type
                
                logger.info(f"Uploading {local_path} to {r2_key} (attempt {attempt + 1})")
                self.client.upload_file(local_path, self.bucket, r2_key, ExtraArgs=extra_args)
                
                # Vérifier que le fichier existe sur R2
                self.client.head_object(Bucket=self.bucket, Key=r2_key)
                
                logger.info(f"Uploaded {r2_key}")
                return True
                
            except Exception as e:
                logger.warning(f"Upload attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    logger.error(f"Failed to upload {r2_key} after {self.max_retries} attempts")
                    return False
        
        return False
    
    def get_file_size(self, r2_key: str) -> Optional[int]:
        """Récupère la taille d'un fichier R2."""
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=r2_key)
            return response['ContentLength']
        except Exception as e:
            logger.warning(f"Failed to get file size for {r2_key}: {e}")
            return None


class RobustAPIClient:
    """Client API avec retry et circuit breaker."""
    
    def __init__(self, api_url: str, token: str):
        self.api_url = api_url.rstrip('/')
        self.token = token
        self.headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        self.max_retries = 3
        self.retry_delay = 2.0
        self.consecutive_failures = 0
        self.circuit_open = False
        self.circuit_threshold = 5
        self.circuit_timeout = 60  # seconds
        self.circuit_opened_at = None
    
    def _check_circuit(self):
        """Vérifie si le circuit breaker est ouvert."""
        if self.circuit_open:
            if self.circuit_opened_at and \
               (datetime.now() - self.circuit_opened_at).seconds > self.circuit_timeout:
                logger.info("Circuit breaker timeout, trying again")
                self.circuit_open = False
                self.consecutive_failures = 0
            else:
                raise RetryableError("Circuit breaker is open")
    
    def _handle_success(self):
        """Réinitialise le compteur d'erreurs."""
        self.consecutive_failures = 0
    
    def _handle_failure(self):
        """Incrémente le compteur d'erreurs."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.circuit_threshold:
            logger.error(f"Circuit breaker opened after {self.consecutive_failures} failures")
            self.circuit_open = True
            self.circuit_opened_at = datetime.now()
    
    def heartbeat(self, cpu_usage: float, memory_usage: float, current_jobs: int) -> Optional[Dict]:
        """Envoie un heartbeat avec retry."""
        self._check_circuit()
        
        payload = {
            'cpu_usage': cpu_usage,
            'memory_usage': memory_usage,
            'current_jobs': current_jobs
        }
        
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    f'{self.api_url}/api/audio-workers/heartbeat',
                    headers=self.headers,
                    json=payload,
                    timeout=10
                )
                response.raise_for_status()
                self._handle_success()
                return response.json()
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"Heartbeat attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    self._handle_failure()
                    return None
        
        return None
    
    def request_job(self) -> Optional[Dict]:
        """Demande un job avec retry."""
        self._check_circuit()
        
        for attempt in range(self.max_retries):
            try:
                response = requests.get(
                    f'{self.api_url}/api/audio-workers/job',
                    headers=self.headers,
                    timeout=30
                )
                
                if response.status_code == 204:
                    self._handle_success()
                    return None
                
                response.raise_for_status()
                self._handle_success()
                return response.json().get('job')
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"Job request attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    self._handle_failure()
                    return None
        
        return None
    
    def submit_result(self, assignment_id: int, status: str, processing_time: int,
                     results: Optional[Dict] = None, error: Optional[str] = None) -> bool:
        """Soumet un résultat avec retry."""
        self._check_circuit()
        
        payload = {
            'status': status,
            'processing_time_seconds': processing_time,
            'results': results,
            'error_message': error
        }
        
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    f'{self.api_url}/api/audio-workers/assignments/{assignment_id}/result',
                    headers=self.headers,
                    json=payload,
                    timeout=30
                )
                response.raise_for_status()
                self._handle_success()
                return True
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"Result submission attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    self._handle_failure()
                    return False
        
        return False
    
    def register(self, worker_info: Dict) -> Optional[Dict]:
        """Enregistre le worker avec retry."""
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    f'{self.api_url}/api/audio-workers',
                    headers=self.headers,
                    json=worker_info,
                    timeout=30
                )
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"Registration attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
        
        return None


class WorkerStats:
    """Collecte et rapporte les statistiques du worker."""
    
    def __init__(self):
        self.jobs_completed = 0
        self.jobs_failed = 0
        self.total_processing_time = 0
        self.started_at = datetime.now()
        self.errors: List[Dict] = []
    
    def record_success(self, processing_time: int):
        self.jobs_completed += 1
        self.total_processing_time += processing_time
    
    def record_failure(self, error: str):
        self.jobs_failed += 1
        self.errors.append({
            'timestamp': datetime.now().isoformat(),
            'error': error
        })
        # Garder seulement les 10 dernières erreurs
        self.errors = self.errors[-10:]
    
    def get_stats(self) -> Dict[str, Any]:
        uptime = datetime.now() - self.started_at
        avg_time = (self.total_processing_time / self.jobs_completed) if self.jobs_completed > 0 else 0
        
        return {
            'jobs_completed': self.jobs_completed,
            'jobs_failed': self.jobs_failed,
            'total_processing_time': self.total_processing_time,
            'average_processing_time': round(avg_time, 2),
            'uptime_seconds': int(uptime.total_seconds()),
            'error_count': len(self.errors),
            'recent_errors': self.errors[-3:]
        }


class HealthCheckServer:
    """Serveur HTTP simple pour health checks."""
    
    def __init__(self, port: int = 8080):
        self.port = port
        self.is_healthy = True
        self.last_heartbeat = datetime.now()
        self.stats = WorkerStats()
    
    def start(self):
        """Démarre le serveur de health check."""
        try:
            from http.server import HTTPServer, BaseHTTPRequestHandler
            
            stats = self.stats
            health_check = self
            
            class HealthHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == '/health':
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        
                        response = {
                            'status': 'healthy' if health_check.is_healthy else 'unhealthy',
                            'timestamp': datetime.now().isoformat(),
                            'stats': stats.get_stats()
                        }
                        self.wfile.write(json.dumps(response).encode())
                    
                    elif self.path == '/stats':
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps(stats.get_stats()).encode())
                    
                    else:
                        self.send_response(404)
                        self.end_headers()
                
                def log_message(self, format, *args):
                    # Supprimer les logs HTTP par défaut
                    pass
            
            server = HTTPServer(('0.0.0.0', self.port), HealthHandler)
            logger.info(f"Health check server started on port {self.port}")
            
            import threading
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            
        except Exception as e:
            logger.warning(f"Failed to start health check server: {e}")