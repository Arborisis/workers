# Arborisis Audio Analysis Orchestrator

Cloudflare Worker qui consomme la Queue `audio-analysis-queue` et déclenche le service Python Analyzer.

## Architecture

```
R2 Event Notification → Cloudflare Queue → Worker Consumer
    → POST /analyze → Service Python Analyzer
```

## Configuration

### Variables d'environnement (wrangler secret)

| Variable | Description |
|----------|-------------|
| `ANALYZER_URL` | URL unique du service Python (fallback si `ANALYZER_URLS` n'est pas défini) |
| `ANALYZER_URLS` | **Multi-VPS** : liste d'URLs séparées par virgule. Ex: `https://worker1.com,https://worker2.com` |
| `ANALYZER_SECRET` | Token Bearer partagé avec le service Python |

### Wrangler

```bash
npm install
wrangler login

# Secrets
wrangler secret put ANALYZER_URL      # fallback (optionnel si ANALYZER_URLS est défini)
wrangler secret put ANALYZER_URLS    # pour 2+ VPS workers (ex: https://w1.com,https://w2.com)
wrangler secret put ANALYZER_SECRET

# Déployer
wrangler deploy
```

## Queue

La Queue `audio-analysis-queue` et la DLQ `audio-analysis-dlq` doivent être créées dans le dashboard Cloudflare.

```toml
[[queues.consumers]]
queue = "audio-analysis-queue"
max_batch_size = 10
max_batch_timeout = 30
max_retries = 3
dead_letter_queue = "audio-analysis-dlq"
```

## R2 Event Notification

Dans le dashboard Cloudflare R2 :
1. Sélectionner le bucket `arborisis`
2. Event Notifications → Add notification
3. Prefix: `sounds/original/`
4. Destination: Queue `audio-analysis-queue`

## Filtrage

Le Worker ignore automatiquement :
- Les clés ne commençant pas par `sounds/original/`
- Les fichiers sans extension audio (wav, mp3, flac, ogg, m4a, aac, wma, webm)
- Les clés avec path traversal (`../`)

## Retry

- Réponse 5xx du service Python → `message.retry()`
- Erreur réseau → `message.retry()`
- Réponse 2xx → `message.ack()`
