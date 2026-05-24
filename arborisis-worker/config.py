import os
import json
import psutil
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class WorkerCapabilities:
    cpu_cores: int
    memory_gb: int
    has_gpu: bool
    gpu_model: Optional[str]
    os: str
    can_run_birdnet: bool
    can_run_deep_learning: bool
    max_file_size_mb: int
    max_concurrent_jobs: int
    use_light_features: bool
    spectrogram_quality: str  # 'low', 'medium', 'high'
    processing_timeout: int
    

class AdaptiveConfig:
    """Configuration qui s'adapte automatiquement aux specs de la machine."""
    
    @staticmethod
    def detect_capabilities() -> WorkerCapabilities:
        cpu_count = psutil.cpu_count(logical=True)
        memory = psutil.virtual_memory()
        memory_gb = round(memory.total / (1024**3))
        
        # Détection GPU
        has_gpu = False
        gpu_model = None
        try:
            import subprocess
            result = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                has_gpu = True
                gpu_model = result.stdout.strip().split('\n')[0]
        except:
            pass
            
        # Configuration adaptative basée sur les specs
        capabilities = WorkerCapabilities(
            cpu_cores=cpu_count,
            memory_gb=memory_gb,
            has_gpu=has_gpu,
            gpu_model=gpu_model,
            os=os.name,
            can_run_birdnet=memory_gb >= 4,  # BirdNET nécessite au moins 4GB
            can_run_deep_learning=has_gpu and memory_gb >= 8,
            max_file_size_mb=min(500, max(50, memory_gb * 10)),
            max_concurrent_jobs=max(1, min(4, cpu_count // 2)),
            use_light_features=memory_gb < 8,  # Features légères si < 8GB
            spectrogram_quality='high' if memory_gb >= 16 else ('medium' if memory_gb >= 8 else 'low'),
            processing_timeout=1800 if memory_gb >= 8 else 600  # 30min ou 10min
        )
        
        return capabilities
    
    @staticmethod
    def get_birdnet_config(capabilities: WorkerCapabilities) -> Dict[str, Any]:
        """Configuration BirdNET adaptée."""
        if not capabilities.can_run_birdnet:
            return {'enabled': False}
            
        config = {
            'enabled': True,
            'confidence_threshold': 0.3,
            'overlap': 1.5,
            'sensitivity': 1.25,
            'timeout': 300 if capabilities.memory_gb >= 8 else 180
        }
        
        # Sur les machines faibles, on augmente le threshold pour aller plus vite
        if capabilities.memory_gb < 8:
            config['confidence_threshold'] = 0.5
            config['overlap'] = 1.0
            
        return config
    
    @staticmethod
    def get_feature_config(capabilities: WorkerCapabilities) -> Dict[str, Any]:
        """Configuration extraction features adaptée."""
        config = {
            'target_sr': 22050,
            'hop_length': 2048,
            'n_fft': 2048,
            'n_mfcc': 13 if capabilities.memory_gb >= 8 else 8,
            'compute_heavy_features': capabilities.memory_gb >= 8,
            'heavy_duration_threshold': 90 if capabilities.memory_gb >= 8 else 30
        }
        
        # Sur machine très faible, on réduit la qualité
        if capabilities.memory_gb < 4:
            config['target_sr'] = 16000
            config['hop_length'] = 4096
            
        return config
    
    @staticmethod
    def get_spectrogram_config(capabilities: WorkerCapabilities) -> Dict[str, Any]:
        """Configuration spectrogramme adaptée."""
        quality = capabilities.spectrogram_quality
        
        configs = {
            'high': {'fig_width': 12, 'fig_height': 6, 'dpi': 100, 'quality': 85},
            'medium': {'fig_width': 10, 'fig_height': 5, 'dpi': 80, 'quality': 75},
            'low': {'fig_width': 8, 'fig_height': 4, 'dpi': 60, 'quality': 60}
        }
        
        return configs.get(quality, configs['medium'])
    
    @classmethod
    def get_full_config(cls) -> Dict[str, Any]:
        """Retourne la configuration complète adaptée."""
        capabilities = cls.detect_capabilities()
        
        return {
            'capabilities': asdict(capabilities),
            'birdnet': cls.get_birdnet_config(capabilities),
            'features': cls.get_feature_config(capabilities),
            'spectrogram': cls.get_spectrogram_config(capabilities),
            'max_concurrent_jobs': capabilities.max_concurrent_jobs,
            'processing_timeout': capabilities.processing_timeout,
            'max_file_size_mb': capabilities.max_file_size_mb
        }