#!/usr/bin/env python3
"""
Arborisis Audio Worker Client
Connecte une machine locale au cluster de traitement audio Arborisis.
"""

import os
import sys
import time
import json
import logging
import subprocess
import platform
import psutil
import requests
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('arborisis-worker')

class AudioWorkerClient:
    def __init__(self):
        self.token = os.getenv('WORKER_TOKEN')
        self.api_url = os.getenv('API_URL', 'https://arborisis.com')
        self.worker_name = os.getenv('WORKER_NAME', 'unknown')
        self.worker_id = os.getenv('WORKER_ID')
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        self.running = True
        self.current_jobs = {}
        
    def get_system_info(self):
        """Récupère les informations système de la machine."""
        cpu_count = psutil.cpu_count(logical=True)
        memory = psutil.virtual_memory()
        
        gpu_info = None
        try:
            if platform.system() == 'Linux':
                result = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader'], 
                                      capture_output=True, text=True)
                if result.returncode == 0:
                    gpu_info = result.stdout.strip()
        except FileNotFoundError:
            pass
            
        return {
            'cpu_cores': cpu_count,
            'memory_gb': round(memory.total / (1024**3)),
            'cpu_usage': psutil.cpu_percent(interval=1),
            'memory_usage': memory.percent,
            'has_gpu': gpu_info is not None,
            'gpu_model': gpu_info,
            'os': f"{platform.system()} {platform.release()}"
        }
        
    def register(self):
        """Enregistre le worker auprès du serveur."""
        info = self.get_system_info()
        
        payload = {
            'name': self.worker_name,
            'hostname': platform.node(),
            'cpu_cores': info['cpu_cores'],
            'memory_gb': info['memory_gb'],
            'has_gpu': info['has_gpu'],
            'gpu_model': info.get('gpu_model'),
            'os': info['os'],
            'capabilities': ['audio-analysis', 'birdnet', 'spectrogram']
        }
        
        try:
            response = requests.post(
                f'{self.api_url}/api/audio-workers',
                headers=self.headers,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            logger.info(f"Worker enregistré: {data['worker']['id']}")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur d'enregistrement: {e}")
            return None
            
    def send_heartbeat(self):
        """Envoie un heartbeat au serveur."""
        info = self.get_system_info()
        
        payload = {
            'cpu_usage': info['cpu_usage'],
            'memory_usage': info['memory_usage'],
            'current_jobs': len(self.current_jobs),
            'ip_address': self.get_ip_address(),
            'port': 8080
        }
        
        try:
            response = requests.post(
                f'{self.api_url}/api/audio-workers/heartbeat',
                headers=self.headers,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if data.get('pending_jobs'):
                for job in data['pending_jobs']:
                    self.process_job(job)
                    
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur heartbeat: {e}")
            return False
            
    def get_ip_address(self):
        """Récupère l'adresse IP publique."""
        try:
            response = requests.get('https://api.ipify.org?format=json', timeout=5)
            return response.json().get('ip')
        except:
            return '127.0.0.1'
            
    def request_job(self):
        """Demande un nouveau job au serveur."""
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
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur lors de la demande de job: {e}")
            return None
            
    def process_job(self, job):
        """Traite un job d'analyse audio."""
        assignment_id = job['assignment_id']
        analysis_id = job['analysis_id']
        r2_key = job['r2_key']
        
        logger.info(f"Traitement du job {assignment_id} pour l'analyse {analysis_id}")
        
        start_time = time.time()
        
        try:
            # Ici vous intégrez votre logique d'analyse audio
            # Par exemple : télécharger depuis R2, exécuter BirdNET, etc.
            
            # Simuler le traitement (à remplacer par la vraie analyse)
            result = self.run_audio_analysis(r2_key, job.get('parameters', {}))
            
            processing_time = int(time.time() - start_time)
            
            # Envoyer le résultat
            self.submit_result(assignment_id, 'completed', processing_time, result)
            
            logger.info(f"Job {assignment_id} terminé en {processing_time}s")
            
        except Exception as e:
            processing_time = int(time.time() - start_time)
            self.submit_result(assignment_id, 'failed', processing_time, error=str(e))
            logger.error(f"Job {assignment_id} échoué: {e}")
            
    def run_audio_analysis(self, r2_key, parameters):
        """Exécute l'analyse audio (à implémenter selon vos besoins)."""
        # TODO: Implémenter la logique d'analyse réelle
        # - Télécharger le fichier depuis R2
        # - Exécuter FFmpeg/BirdNET
        # - Générer les résultats
        
        return {
            'status': 'completed',
            'message': 'Analyse terminée'
        }
        
    def submit_result(self, assignment_id, status, processing_time, results=None, error=None):
        """Envoie le résultat d'un job au serveur."""
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
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur lors de l'envoi du résultat: {e}")
            return False
            
    def setup_cloudflare_tunnel(self):
        """Configure un tunnel Cloudflare pour la connexion sécurisée."""
        try:
            # Vérifier si cloudflared est installé
            result = subprocess.run(['which', 'cloudflared'], capture_output=True)
            if result.returncode != 0:
                logger.info("Installation de cloudflared...")
                if platform.system() == 'Linux':
                    subprocess.run([
                        'bash', '-c',
                        'curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && sudo dpkg -i cloudflared.deb'
                    ], check=True)
                elif platform.system() == 'Darwin':
                    subprocess.run(['brew', 'install', 'cloudflared'], check=True)
                    
            # Démarrer le tunnel
            logger.info("Démarrage du tunnel Cloudflare...")
            tunnel_process = subprocess.Popen(
                ['cloudflared', 'tunnel', '--url', 'http://localhost:8080'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Attendre que le tunnel soit prêt
            time.sleep(5)
            
            logger.info("Tunnel Cloudflare démarré")
            return tunnel_process
            
        except Exception as e:
            logger.error(f"Erreur lors de la configuration du tunnel: {e}")
            return None
            
    def run(self):
        """Boucle principale du worker."""
        logger.info("Démarrage du worker Arborisis Audio...")
        
        # Enregistrer le worker
        if not self.worker_id:
            registration = self.register()
            if not registration:
                logger.error("Impossible d'enregistrer le worker")
                sys.exit(1)
            self.worker_id = registration['worker']['id']
            
        # Configurer le tunnel Cloudflare
        tunnel = self.setup_cloudflare_tunnel()
        
        logger.info("Worker prêt! En attente de jobs...")
        
        try:
            while self.running:
                # Envoyer un heartbeat
                self.send_heartbeat()
                
                # Demander un nouveau job si disponible
                if len(self.current_jobs) < 2:  # Max 2 jobs simultanés
                    job = self.request_job()
                    if job:
                        self.process_job(job)
                        
                # Attendre avant le prochain cycle
                time.sleep(30)
                
        except KeyboardInterrupt:
            logger.info("Arrêt du worker...")
            self.running = False
            if tunnel:
                tunnel.terminate()
                
        logger.info("Worker arrêté")

if __name__ == '__main__':
    if not os.getenv('WORKER_TOKEN'):
        print("Erreur: WORKER_TOKEN non défini")
        print("Usage: WORKER_TOKEN=votre_token python worker.py")
        sys.exit(1)
        
    worker = AudioWorkerClient()
    worker.run()