#!/usr/bin/env python3
"""
Mécanisme de mise à jour automatique du worker Arborisis.
Vérifie les mises à jour depuis le serveur et redémarre proprement.
"""

import os
import sys
import time
import json
import logging
import hashlib
import tempfile
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
from datetime import datetime

import requests

logger = logging.getLogger('arborisis-worker')


@dataclass
class UpdateInfo:
    """Informations sur une mise à jour disponible."""
    version: str
    download_url: str
    checksum: str
    changelog: str
    required: bool = False
    

class AutoUpdater:
    """
    Gère les mises à jour automatiques du worker.
    
    Le worker vérifie périodiquement si une nouvelle version est disponible
    sur le serveur. Si oui, il télécharge les fichiers, vérifie l'intégrité,
    et redémarre proprement.
    """
    
    def __init__(
        self,
        api_url: str,
        token: str,
        current_version: str = "2.0.0",
        check_interval_hours: int = 1,
        on_update_ready: Optional[Callable] = None
    ):
        self.api_url = api_url.rstrip('/')
        self.token = token
        self.current_version = current_version
        self.check_interval = check_interval_hours * 3600  # en secondes
        self.on_update_ready = on_update_ready
        
        self.last_check = 0
        self.update_available: Optional[UpdateInfo] = None
        self._stop_event = threading.Event()
        self._check_thread: Optional[threading.Thread] = None
        
        # Répertoire du worker (où se trouve worker.py)
        self.worker_dir = Path(__file__).parent.absolute()
        self.backup_dir = self.worker_dir / '.backup'
        self.update_dir = self.worker_dir / '.update'
        
    def start(self):
        """Démarre le thread de vérification des mises à jour."""
        if self._check_thread and self._check_thread.is_alive():
            logger.warning("Auto-updater already running")
            return
            
        self._stop_event.clear()
        self._check_thread = threading.Thread(target=self._check_loop, daemon=True)
        self._check_thread.start()
        logger.info(f"Auto-updater started (checking every {self.check_interval // 3600}h)")
        
    def stop(self):
        """Arrête le thread de vérification."""
        self._stop_event.set()
        if self._check_thread:
            self._check_thread.join(timeout=5)
            
    def _check_loop(self):
        """Boucle principale de vérification des mises à jour."""
        # Première vérification après 60 secondes
        time.sleep(60)
        
        while not self._stop_event.is_set():
            try:
                self.check_for_update()
            except Exception as e:
                logger.error(f"Error checking for updates: {e}")
                
            # Attendre l'intervalle avant la prochaine vérification
            self._stop_event.wait(self.check_interval)
            
    def check_for_update(self) -> Optional[UpdateInfo]:
        """Vérifie si une mise à jour est disponible sur le serveur."""
        try:
            headers = {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.get(
                f'{self.api_url}/api/audio-workers/update',
                headers=headers,
                params={'current_version': self.current_version},
                timeout=10
            )
            
            if response.status_code == 204:
                # Pas de mise à jour disponible
                self.update_available = None
                return None
                
            response.raise_for_status()
            data = response.json()
            
            update_info = UpdateInfo(
                version=data['version'],
                download_url=data['download_url'],
                checksum=data['checksum'],
                changelog=data.get('changelog', ''),
                required=data.get('required', False)
            )
            
            self.update_available = update_info
            logger.info(f"Update available: {self.current_version} -> {update_info.version}")
            
            if self.on_update_ready:
                self.on_update_ready(update_info)
            elif update_info.required:
                logger.warning("Required update available, will apply soon...")
                
            return update_info
            
        except requests.exceptions.RequestException as e:
            logger.debug(f"Failed to check for updates: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error checking for updates: {e}")
            return None
            
    def apply_update(self, update_info: Optional[UpdateInfo] = None) -> bool:
        """
        Applique une mise à jour.
        
        1. Télécharge le package de mise à jour
        2. Vérifie le checksum
        3. Sauvegarde les fichiers actuels
        4. Applique la mise à jour
        5. Redémarre le worker
        """
        if update_info is None:
            update_info = self.update_available
            
        if update_info is None:
            logger.warning("No update to apply")
            return False
            
        logger.info(f"Applying update to version {update_info.version}...")
        
        try:
            # 1. Télécharger le package
            package_path = self._download_update(update_info)
            if not package_path:
                return False
                
            # 2. Vérifier le checksum
            if not self._verify_checksum(package_path, update_info.checksum):
                logger.error("Checksum verification failed, aborting update")
                return False
                
            # 3. Sauvegarder les fichiers actuels
            if not self._backup_current():
                logger.error("Failed to backup current files, aborting update")
                return False
                
            # 4. Appliquer la mise à jour
            if not self._extract_update(package_path):
                logger.error("Failed to extract update, restoring backup...")
                self._restore_backup()
                return False
                
            # 5. Mettre à jour la version
            self._update_version_file(update_info.version)
            
            logger.info(f"Update to {update_info.version} applied successfully!")
            
            # 6. Redémarrer le worker
            self._restart_worker()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to apply update: {e}")
            self._restore_backup()
            return False
            
    def _download_update(self, update_info: UpdateInfo) -> Optional[Path]:
        """Télécharge le package de mise à jour."""
        try:
            self.update_dir.mkdir(exist_ok=True)
            package_path = self.update_dir / f"update_{update_info.version}.zip"
            
            logger.info(f"Downloading update from {update_info.download_url}...")
            
            headers = {'Authorization': f'Bearer {self.token}'}
            response = requests.get(
                update_info.download_url,
                headers=headers,
                stream=True,
                timeout=300
            )
            response.raise_for_status()
            
            with open(package_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
            logger.info(f"Update downloaded: {package_path} ({package_path.stat().st_size} bytes)")
            return package_path
            
        except Exception as e:
            logger.error(f"Failed to download update: {e}")
            return None
            
    def _verify_checksum(self, file_path: Path, expected_checksum: str) -> bool:
        """Vérifie le checksum SHA256 d'un fichier."""
        try:
            sha256 = hashlib.sha256()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    sha256.update(chunk)
                    
            actual_checksum = sha256.hexdigest()
            
            if actual_checksum == expected_checksum:
                logger.info("Checksum verification passed")
                return True
            else:
                logger.error(f"Checksum mismatch: expected {expected_checksum}, got {actual_checksum}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to verify checksum: {e}")
            return False
            
    def _backup_current(self) -> bool:
        """Sauvegarde les fichiers actuels du worker."""
        try:
            self.backup_dir.mkdir(exist_ok=True)
            
            # Créer un timestamp pour la backup
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = self.backup_dir / f"backup_{timestamp}"
            backup_path.mkdir(exist_ok=True)
            
            # Copier les fichiers Python
            for py_file in self.worker_dir.glob('*.py'):
                shutil.copy2(py_file, backup_path / py_file.name)
                
            # Copier requirements.txt s'il existe
            req_file = self.worker_dir / 'requirements.txt'
            if req_file.exists():
                shutil.copy2(req_file, backup_path / 'requirements.txt')
                
            logger.info(f"Backup created: {backup_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            return False
            
    def _extract_update(self, package_path: Path) -> bool:
        """Extrait et applique la mise à jour."""
        try:
            import zipfile
            
            extract_dir = self.update_dir / 'extracted'
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir()
            
            # Extraire le zip
            with zipfile.ZipFile(package_path, 'r') as z:
                z.extractall(extract_dir)
                
            # Copier les nouveaux fichiers
            for src_file in extract_dir.rglob('*'):
                if src_file.is_file():
                    # Calculer le chemin relatif
                    rel_path = src_file.relative_to(extract_dir)
                    dst_file = self.worker_dir / rel_path
                    
                    # Créer les répertoires parents si nécessaire
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Copier le fichier
                    shutil.copy2(src_file, dst_file)
                    logger.debug(f"Updated: {dst_file}")
                    
            logger.info("Update extracted and applied")
            return True
            
        except Exception as e:
            logger.error(f"Failed to extract update: {e}")
            return False
            
    def _restore_backup(self) -> bool:
        """Restaure la dernière backup."""
        try:
            # Trouver la backup la plus récente
            backups = sorted(self.backup_dir.glob('backup_*'), reverse=True)
            if not backups:
                logger.error("No backup found to restore")
                return False
                
            latest_backup = backups[0]
            
            # Restaurer les fichiers
            for backup_file in latest_backup.glob('*'):
                if backup_file.is_file():
                    dst_file = self.worker_dir / backup_file.name
                    shutil.copy2(backup_file, dst_file)
                    
            logger.info(f"Restored from backup: {latest_backup}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore backup: {e}")
            return False
            
    def _update_version_file(self, new_version: str):
        """Met à jour le fichier de version."""
        version_file = self.worker_dir / '.version'
        with open(version_file, 'w') as f:
            f.write(new_version)
        self.current_version = new_version
        
    def _restart_worker(self):
        """Redémarre le worker proprement."""
        logger.info("Restarting worker to apply update...")
        
        # Donner le temps aux logs de s'écrire
        time.sleep(2)
        
        # Redémarrer avec le même interpréteur et les mêmes arguments
        python = sys.executable
        script = self.worker_dir / 'worker.py'
        
        # Exécuter le nouveau worker
        os.execv(python, [python, str(script)] + sys.argv[1:])
        
    def get_status(self) -> Dict[str, Any]:
        """Retourne le statut du système de mise à jour."""
        return {
            'current_version': self.current_version,
            'update_available': self.update_available is not None,
            'update_version': self.update_available.version if self.update_available else None,
            'last_check': self.last_check,
            'check_interval_hours': self.check_interval // 3600,
        }
