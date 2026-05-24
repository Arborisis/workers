import os
import csv
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import structlog

logger = structlog.get_logger()


class AudioAnalyzer:
    """Analyseur audio avec adaptation automatique aux capacités de la machine."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.capabilities = config['capabilities']
        self.birdnet_config = config['birdnet']
        self.feature_config = config['features']
        self.spectrogram_config = config['spectrogram']
        self.temp_dir = tempfile.mkdtemp(prefix="arborisis_worker_")
        
    def analyze(self, local_path: str, sound_id: int, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Effectue l'analyse complète d'un fichier audio."""
        logger.info("starting_analysis", sound_id=sound_id, file=local_path)
        
        results = {
            'sound_id': sound_id,
            'status': 'completed',
            'started_at': datetime.utcnow().isoformat(),
            'files': {},
            'metadata': {},
            'features': {},
            'detections': []
        }
        
        try:
            # 1. Extraction métadonnées
            metadata = self.extract_metadata(local_path)
            results['metadata'] = metadata
            
            # 2. Extraction features
            features = self.extract_features(local_path, metadata)
            results['features'] = features
            
            # 3. Génération spectrogramme
            if self.capabilities['memory_gb'] >= 4:
                spectrogram_path = self.generate_spectrogram(local_path, sound_id)
                results['files']['spectrogram'] = spectrogram_path
            
            # 4. Analyse BirdNET (si activée et fichier assez long)
            if self.birdnet_config['enabled'] and metadata.get('duration_seconds', 0) >= 3.0:
                birdnet_key, detections = self.run_birdnet(
                    local_path, sound_id, metadata,
                    lat=parameters.get('lat') if parameters else None,
                    lon=parameters.get('lon') if parameters else None,
                    recorded_at=parameters.get('recorded_at') if parameters else None
                )
                if birdnet_key:
                    results['files']['birdnet'] = birdnet_key
                results['detections'] = detections
            
            # 5. Génération preview MP3 (si demandé)
            if parameters and parameters.get('generate_preview', True):
                preview_path = self.generate_preview(local_path, sound_id)
                if preview_path:
                    results['files']['preview'] = preview_path
            
            results['completed_at'] = datetime.utcnow().isoformat()
            logger.info("analysis_completed", sound_id=sound_id)
            
        except Exception as e:
            logger.error("analysis_failed", sound_id=sound_id, error=str(e))
            results['status'] = 'failed'
            results['error'] = str(e)
            
        finally:
            # Nettoyage
            self._cleanup()
            
        return results
    
    def extract_metadata(self, local_path: str) -> Dict[str, Any]:
        """Extrait les métadonnées du fichier audio."""
        import librosa
        
        y, sr = librosa.load(local_path, sr=self.feature_config['target_sr'], mono=True)
        duration = librosa.get_duration(y=y, sr=sr)
        
        # Informations supplémentaires via ffprobe
        try:
            cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', 
                   '-show_format', '-show_streams', local_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            ffprobe_data = json.loads(result.stdout) if result.returncode == 0 else {}
        except:
            ffprobe_data = {}
        
        format_info = ffprobe_data.get('format', {})
        streams = ffprobe_data.get('streams', [{}])[0]
        
        return {
            'duration_seconds': round(duration, 2),
            'sample_rate': sr,
            'channels': streams.get('channels', 1),
            'bitrate': int(format_info.get('bit_rate', 0)) // 1000 if format_info.get('bit_rate') else None,
            'format': format_info.get('format_name', 'unknown'),
            'file_size_mb': round(os.path.getsize(local_path) / (1024 * 1024), 2)
        }
    
    def extract_features(self, local_path: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Extrait les features audio avec librosa."""
        import librosa
        import numpy as np
        
        y, sr = librosa.load(local_path, sr=self.feature_config['target_sr'], mono=True)
        
        hop_length = self.feature_config['hop_length']
        n_fft = self.feature_config['n_fft']
        
        # STFT
        stft = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_length))
        
        # Features de base
        features = {
            'temporal': {
                'rms_mean': float(np.mean(librosa.feature.rms(S=stft, hop_length=hop_length)[0])),
                'rms_std': float(np.std(librosa.feature.rms(S=stft, hop_length=hop_length)[0])),
                'zcr_mean': float(np.mean(librosa.feature.zero_crossing_rate(y=y, hop_length=hop_length)[0])),
                'zcr_std': float(np.std(librosa.feature.zero_crossing_rate(y=y, hop_length=hop_length)[0])),
            },
            'spectral': {
                'centroid_mean': float(np.mean(librosa.feature.spectral_centroid(S=stft, sr=sr)[0])),
                'centroid_std': float(np.std(librosa.feature.spectral_centroid(S=stft, sr=sr)[0])),
                'rolloff_mean': float(np.mean(librosa.feature.spectral_rolloff(S=stft, sr=sr)[0])),
                'bandwidth_mean': float(np.mean(librosa.feature.spectral_bandwidth(S=stft, sr=sr)[0])),
                'flatness_mean': float(np.mean(librosa.feature.spectral_flatness(S=stft)[0])),
            },
            'mfcc': {
                'mean': [float(x) for x in np.mean(librosa.feature.mfcc(
                    S=librosa.power_to_db(stft), sr=sr, 
                    n_mfcc=self.feature_config['n_mfcc']), axis=1)],
                'std': [float(x) for x in np.std(librosa.feature.mfcc(
                    S=librosa.power_to_db(stft), sr=sr, 
                    n_mfcc=self.feature_config['n_mfcc']), axis=1)],
            }
        }
        
        # Features lourdes uniquement sur machines puissantes
        if self.feature_config['compute_heavy_features'] and \
           metadata['duration_seconds'] < self.feature_config['heavy_duration_threshold']:
            try:
                contrast = librosa.feature.spectral_contrast(S=stft, sr=sr)
                features['spectral']['contrast_mean'] = [float(x) for x in np.mean(contrast, axis=1)]
                
                harmonic = librosa.effects.harmonic(y, margin=8)
                tonnetz = librosa.feature.tonnetz(y=harmonic, sr=sr)
                features['tonal'] = {
                    'tonnetz_mean': [float(x) for x in np.mean(tonnetz, axis=1)]
                }
            except Exception as e:
                logger.warning("heavy_features_failed", error=str(e))
        
        # Tempo
        try:
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop_length)
            features['temporal']['tempo'] = float(tempo) if isinstance(tempo, (int, float)) else float(tempo.item())
        except:
            features['temporal']['tempo'] = 0.0
        
        return features
    
    def generate_spectrogram(self, local_path: str, sound_id: int) -> Optional[str]:
        """Génère un spectrogramme WebP."""
        import librosa
        import librosa.display
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from PIL import Image
        
        try:
            y, sr = librosa.load(local_path, sr=22050, mono=True)
            
            cfg = self.spectrogram_config
            fig, ax = plt.subplots(figsize=(cfg['fig_width'], cfg['fig_height']))
            fig.patch.set_facecolor("#0a0a0a")
            ax.set_facecolor("#0a0a0a")
            
            D = librosa.amplitude_to_db(np.abs(librosa.stft(y)), ref=np.max)
            img = librosa.display.specshow(D, sr=sr, x_axis=None, y_axis=None, 
                                          cmap="magma", ax=ax)
            
            ax.axis("off")
            plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
            
            temp_png = os.path.join(self.temp_dir, f"spectrogram_{sound_id}.png")
            temp_webp = temp_png.replace(".png", ".webp")
            
            fig.savefig(temp_png, dpi=cfg['dpi'], facecolor="#0a0a0a", edgecolor="none")
            plt.close(fig)
            
            with Image.open(temp_png) as im:
                im.save(temp_webp, "WEBP", quality=cfg['quality'])
            
            os.remove(temp_png)
            
            return temp_webp
            
        except Exception as e:
            logger.error("spectrogram_failed", sound_id=sound_id, error=str(e))
            return None
    
    def run_birdnet(self, local_path: str, sound_id: int, metadata: Dict[str, Any],
                   lat: Optional[float] = None, lon: Optional[float] = None,
                   recorded_at: Optional[str] = None) -> Tuple[Optional[str], List[Dict]]:
        """Exécute BirdNET pour la classification d'oiseaux."""
        if not self.birdnet_config['enabled']:
            return None, []
        
        result_dir = os.path.join(self.temp_dir, f"birdnet_{sound_id}")
        os.makedirs(result_dir, exist_ok=True)
        
        cmd = [
            "python3", "-m", "birdnet_analyzer.analyze",
            local_path,
            "-o", result_dir,
            "--rtype", "csv",
            "--min_conf", str(self.birdnet_config['confidence_threshold']),
            "--overlap", str(self.birdnet_config['overlap']),
            "--sensitivity", str(self.birdnet_config['sensitivity']),
        ]
        
        if lat is not None:
            cmd.extend(["--lat", str(lat)])
        if lon is not None:
            cmd.extend(["--lon", str(lon)])
        
        # Calcul de la semaine pour le filtre saisonnier
        try:
            if recorded_at:
                dt = datetime.fromisoformat(recorded_at.replace('Z', '+00:00'))
                week = dt.isocalendar()[1]
            else:
                week = datetime.utcnow().isocalendar()[1]
            cmd.extend(["--week", str(week)])
        except:
            pass
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, 
                                  check=True, timeout=self.birdnet_config['timeout'])
            logger.info("birdnet_completed", sound_id=sound_id)
        except subprocess.CalledProcessError as e:
            logger.error("birdnet_failed", sound_id=sound_id, error=str(e))
            return None, []
        except FileNotFoundError:
            logger.error("birdnet_not_found", sound_id=sound_id)
            return None, []
        
        # Parse CSV
        detections = []
        for filename in os.listdir(result_dir):
            if filename.endswith('.csv'):
                csv_path = os.path.join(result_dir, filename)
                with open(csv_path, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        detections.append({
                            'start_time': float(row.get('Start (s)', 0)),
                            'end_time': float(row.get('End (s)', 0)),
                            'scientific_name': row.get('Scientific name', ''),
                            'common_name': row.get('Common name', ''),
                            'confidence': float(row.get('Confidence', 0)),
                        })
        
        # Sauvegarder les résultats bruts
        result_json = os.path.join(self.temp_dir, f"birdnet_{sound_id}.json")
        with open(result_json, 'w') as f:
            json.dump(detections, f, indent=2)
        
        return result_json, detections
    
    def generate_preview(self, local_path: str, sound_id: int, duration: int = 30) -> Optional[str]:
        """Génère un extrait MP3 pour la preview."""
        try:
            output_path = os.path.join(self.temp_dir, f"preview_{sound_id}.mp3")
            
            # Extraire 30 secondes du milieu
            cmd = [
                'ffmpeg', '-y', '-i', local_path,
                '-t', str(duration),
                '-af', 'afade=t=out:st=25:d=5',  # Fade out sur les 5 dernières secondes
                '-ar', '22050', '-ac', '1', '-b:a', '96k',
                output_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode == 0 and os.path.exists(output_path):
                return output_path
                
        except Exception as e:
            logger.warning("preview_generation_failed", sound_id=sound_id, error=str(e))
        
        return None
    
    def _cleanup(self):
        """Nettoie les fichiers temporaires."""
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except Exception as e:
            logger.warning("cleanup_failed", error=str(e))
    
    def __del__(self):
        self._cleanup()