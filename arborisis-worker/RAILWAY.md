# Déploiement Arborisis Worker sur Railway

## ⚠️ Limitations

- **Pas de GPU** : Railway ne supporte pas les GPU. L'analyse audio se fera en CPU uniquement.
- **Mémoire** : Prévoir au moins **2-4 GB RAM** (l'analyse audio est gourmande).
- **Stockage** : Les conteneurs Railway sont éphémères. Les modèles seront re-téléchargés à chaque redémarrage.
- **BirdNET** : Désactivé par défaut car très lourd. À activer manuellement si besoin.

## 🚀 Déploiement Rapide

### 1. Créer un service Railway

```bash
# Se connecter à Railway
railway login

# Créer un projet (ou utiliser un existant)
railway init

# Ajouter ce repo comme service
cd workers/arborisis-worker
railway add
```

### 2. Configurer les variables d'environnement

Dans le dashboard Railway, ajouter ces variables :

```env
WORKER_TOKEN=token_généré_par_laravel
API_URL=https://arborisis.com
WORKER_NAME=railway-worker-01

# R2 (pour télécharger les fichiers audio)
R2_ENDPOINT=https://xxx.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=xxx
R2_SECRET_ACCESS_KEY=xxx
R2_BUCKET_NAME=arborisis-audio

# Optionnel : config audio
MAX_CONCURRENT_JOBS=1
MAX_FILE_SIZE_MB=100
```

**Obtenir le WORKER_TOKEN :**
```bash
# Sur Laravel, enregistrer un nouveau worker via l'API
# Ou utiliser la commande si tu l'as créée :
php artisan audio-worker:register "railway-worker-01"
```

### 3. Configuration du service

Dans Railway :
- **Build Command** : laisser vide (utilise le Dockerfile)
- **Start Command** : `python3 worker.py`
- **Healthcheck Path** : `/health`
- **Healthcheck Timeout** : `30`

### 4. Scaling

Pour avoir plusieurs workers sur Railway :
```bash
railway scale --service arborisis-worker --replicas 3
```

Ou via le dashboard : Settings → Replicas

## 🔧 Dockerfile Railway vs Standard

Le `Dockerfile.railway` diffère du standard :
- Base `python:3.11-slim` (plus légère, sans CUDA)
- Pas de drivers NVIDIA
- BirdNET désactivé (peut être réactivé en décommentant)
- Pas de téléchargement auto des modèles LLM

## 📊 Monitoring

Le worker expose :
- `GET /health` → statut du worker
- `GET /stats` → statistiques (jobs complétés, temps moyen, etc.)

## 💡 Recommandations

1. **Pour la production** : combine Railway + VPS perso
   - Railway : workers légers, toujours online
   - VPS : workers GPU pour l'inférence lourde

2. **Pour les tests** : Railway est parfait

3. **Évite les redémarrages fréquents** : configure Railway en `restartPolicyType = "on_failure"`
