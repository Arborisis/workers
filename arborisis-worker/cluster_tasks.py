import os
import time
import json
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from infrastructure import RobustAPIClient, RetryableError, FatalError

logger = logging.getLogger('arborisis-worker')


class ClusterTaskType(Enum):
    INFERENCE = "inference"
    TRAINING = "training"
    EMBEDDING = "embedding"
    ANALYSIS = "analysis"


@dataclass
class ClusterTask:
    id: int
    type: str
    model: str
    payload: Dict[str, Any]
    status: str = "assigned"
    result: Optional[Dict] = None
    error: Optional[str] = None


class ClusterTaskManager:
    """Gère les tâches cluster IA pour le worker."""
    
    def __init__(self, api_client: RobustAPIClient):
        self.api = api_client
        self.current_task: Optional[ClusterTask] = None
        self.supported_models = self._detect_available_models()
        
    def _detect_available_models(self) -> Dict[str, Any]:
        """Détecte les modèles IA disponibles sur cette machine."""
        models = {}
        
        # Vérifier Gemma 4
        gemma_path = os.getenv('GEMMA_MODEL_PATH', '/models/gemma-4')
        if os.path.exists(gemma_path):
            models['gemma-4'] = {
                'path': gemma_path,
                'type': 'local',
                'gpu': False,
            }
            logger.info(f"Gemma 4 model detected at {gemma_path}")
        
        # Vérifier GPU (NVIDIA)
        gpu_available = False
        try:
            import subprocess
            result = subprocess.run(['nvidia-smi'], capture_output=True)
            if result.returncode == 0:
                gpu_available = True
                models['gpu_available'] = True
                # Si GPU dispo, Gemma 4 GPU aussi
                if 'gemma-4' in models:
                    models['gemma-4-gpu'] = {
                        'path': gemma_path,
                        'type': 'local',
                        'gpu': True,
                    }
        except:
            pass
        
        # Vérifier MPS (Metal Performance Shaders) pour Mac ARM
        mps_available = False
        try:
            import platform
            if platform.system() == 'Darwin' and platform.machine() in ['arm64', 'aarch64']:
                import torch
                if torch.backends.mps.is_available() and torch.backends.mps.is_built():
                    mps_available = True
                    models['mps_available'] = True
                    logger.info("MPS (Metal Performance Shaders) detected on Apple Silicon")
                    # Si MPS dispo, Gemma 4 MPS aussi
                    if 'gemma-4' in models:
                        models['gemma-4-mps'] = {
                            'path': gemma_path,
                            'type': 'local',
                            'gpu': True,  # MPS compte comme accélération GPU
                            'mps': True,
                        }
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"MPS detection error: {e}")
        
        if not gpu_available and not mps_available:
            models['gpu_available'] = False
        
        # BirdNET est toujours disponible
        models['birdnet-cluster'] = {
            'type': 'tool',
            'command': 'python3 -m birdnet_analyzer.analyze',
        }
        
        return models
    
    def request_task(self) -> Optional[ClusterTask]:
        """Demande une tâche cluster au serveur."""
        try:
            response = self.api.request_job()
            
            if response and 'task' in response:
                task_data = response['task']
                self.current_task = ClusterTask(
                    id=task_data['id'],
                    type=task_data['type'],
                    model=task_data['model'],
                    payload=task_data['payload'],
                )
                logger.info(f"Received cluster task {self.current_task.id} for model {self.current_task.model}")
                return self.current_task
                
        except Exception as e:
            logger.error(f"Failed to request cluster task: {e}")
        
        return None
    
    def execute_task(self, task: ClusterTask) -> Dict[str, Any]:
        """Exécute une tâche cluster selon le modèle demandé."""
        start_time = time.time()
        
        try:
            logger.info(f"Executing cluster task {task.id} for model {task.model}")
            
            if task.model == 'gemma-4' or task.model == 'gemma-4-gpu' or task.model == 'gemma-4-mps':
                result = self._execute_gemma(task)
            elif task.model == 'gemma-4-mini':
                result = self._execute_gemma(task)
            elif task.model == 'birdnet-cluster':
                result = self._execute_birdnet_cluster(task)
            else:
                raise FatalError(f"Unsupported model: {task.model}")
            
            processing_time = int(time.time() - start_time)
            
            # Soumettre le résultat
            self._submit_result(task, 'completed', result, processing_time)
            
            return {
                'status': 'completed',
                'processing_time': processing_time,
                'result': result,
            }
            
        except Exception as e:
            processing_time = int(time.time() - start_time)
            error_msg = str(e)
            logger.error(f"Cluster task {task.id} failed: {error_msg}")
            
            self._submit_result(task, 'failed', None, processing_time, error_msg)
            
            return {
                'status': 'failed',
                'processing_time': processing_time,
                'error': error_msg,
            }
        
        finally:
            self.current_task = None
    
    def _execute_gemma(self, task: ClusterTask) -> Dict[str, Any]:
        """Exécute une inférence avec le modèle Gemma 4 (Assistant Sylve)."""
        import subprocess
        import tempfile
        
        payload = task.payload
        prompt = payload.get('prompt', '')
        max_tokens = payload.get('max_tokens', 2048)
        temperature = payload.get('temperature', 0.7)
        
        # Utiliser llama.cpp ou autre backend
        model_path = self.supported_models.get('gemma-4', {}).get('path', '/models/gemma-4')
        
        # Vérifier si MPS est disponible pour ce modèle
        use_mps = task.model == 'gemma-4-mps' and self.supported_models.get('mps_available', False)
        
        # Créer un fichier temporaire pour le prompt
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(prompt)
            prompt_file = f.name
        
        try:
            # Exécuter l'inférence
            cmd = [
                'python3', '-m', 'llama_cpp',
                '--model', model_path,
                '--prompt-file', prompt_file,
                '--max-tokens', str(max_tokens),
                '--temperature', str(temperature),
                '--json',
            ]
            
            # Ajouter les flags GPU/MPS si nécessaire
            if use_mps:
                cmd.extend(['--n-gpu-layers', '999'])
                # Définir la variable d'environnement pour MPS
                env = os.environ.copy()
                env['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'
            else:
                env = None
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            
            if result.returncode != 0:
                raise Exception(f"Gemma 4 inference failed: {result.stderr}")
            
            # Parser le résultat JSON
            return json.loads(result.stdout)
            
        finally:
            os.unlink(prompt_file)
    
    def _execute_birdnet_cluster(self, task: ClusterTask) -> Dict[str, Any]:
        """Exécute une analyse BirdNET distribuée."""
        # C'est similaire à l'analyse audio normale mais optimisé pour le cluster
        payload = task.payload
        audio_path = payload.get('audio_path')
        
        if not audio_path:
            raise ValueError("No audio path provided")
        
        # Réutiliser l'analyseur audio existant
        from audio_analyzer import AudioAnalyzer
        from config import AdaptiveConfig
        
        config = AdaptiveConfig.get_full_config()
        analyzer = AudioAnalyzer(config)
        
        results = analyzer.analyze(audio_path, task.id, payload)
        
        return {
            'detections': results.get('detections', []),
            'metadata': results.get('metadata', {}),
        }
    
    def _submit_result(self, task: ClusterTask, status: str, result: Optional[Dict], 
                      processing_time: int, error: Optional[str] = None) -> None:
        """Soumet le résultat d'une tâche cluster."""
        try:
            payload = {
                'status': status,
                'processing_time_seconds': processing_time,
                'result': result,
                'error_message': error,
            }
            
            # Utiliser l'API cluster worker
            response = self.api.submit_result(
                assignment_id=task.id,  # Utiliser l'ID de tâche comme assignment_id
                status=status,
                processing_time=processing_time,
                results=result,
                error=error,
            )
            
            if response:
                logger.info(f"Cluster task {task.id} result submitted")
            else:
                logger.error(f"Failed to submit cluster task {task.id} result")
                
        except Exception as e:
            logger.error(f"Error submitting cluster result: {e}")
    
    def get_capabilities(self) -> Dict[str, Any]:
        """Retourne les capacités cluster de ce worker."""
        return {
            'models': list(self.supported_models.keys()),
            'gpu_available': self.supported_models.get('gpu_available', False),
            'mps_available': self.supported_models.get('mps_available', False),
            'can_run_gemma_4': 'gemma-4' in self.supported_models,
            'can_run_gemma_4_gpu': 'gemma-4-gpu' in self.supported_models,
            'can_run_gemma_4_mps': 'gemma-4-mps' in self.supported_models,
            'can_run_gemma_4_mini': 'gemma-4-mini' in self.supported_models,
            'can_run_birdnet_cluster': 'birdnet-cluster' in self.supported_models,
        }