# Arborisis Audio Analyzer Container

Cloudflare Worker gateway that runs the existing Python `services/audio-analyzer`
Docker image on Cloudflare Containers.

The existing queue orchestrator can keep its current behavior. To route analyses
to Containers, set its `ANALYZER_URL` or `ANALYZER_URLS` secret to this Worker's
public URL.

Current deployed URL:

```text
https://arborisis-audio-analyzer-container.bastienjavaux-bc3.workers.dev
```

## Architecture

```text
R2 event
  -> audio-analysis-queue
  -> workers/audio-analysis-orchestrator
  -> workers/audio-analyzer-container
  -> Cloudflare Container running services/audio-analyzer
  -> R2 results
  -> Laravel callback
```

## Deploy

Cloudflare Containers requires a Workers Paid plan and Docker available locally
for `wrangler deploy`.

```bash
cd workers/audio-analyzer-container
npm install
npm run deploy
```

Set secrets before production traffic:

```bash
npx wrangler secret put ANALYZER_SECRET
npx wrangler secret put LARAVEL_API_URL
npx wrangler secret put LARAVEL_API_SECRET
npx wrangler secret put R2_ENDPOINT
npx wrangler secret put R2_ACCESS_KEY_ID
npx wrangler secret put R2_SECRET_ACCESS_KEY
npx wrangler secret put R2_BUCKET
npx wrangler secret put R2_REGION
```

Optional tuning:

```bash
npx wrangler secret put APP_ENV
npx wrangler secret put MAX_FILE_SIZE_MB
npx wrangler secret put MAX_DURATION_SECONDS
npx wrangler secret put BIRDNET_CONFIDENCE_THRESHOLD
npx wrangler secret put BIRDNET_PUBLISH_CONFIDENCE_THRESHOLD
npx wrangler secret put BIRDNET_REPEATED_CONFIDENCE_THRESHOLD
npx wrangler secret put BIRDNET_MAX_DETECTIONS
npx wrangler secret put BIRDNET_MODEL_PATH
npx wrangler secret put LOG_LEVEL
```

## Cutover

1. Deploy this Worker and wait until `npx wrangler containers list` shows the
   deployment is ready.
2. Test `GET /health`.
3. Put this Worker's URL into the orchestrator's `ANALYZER_URLS` secret next to
   one VPS URL.
4. Upload a real audio file and verify status `queued -> processing -> completed`.
5. Remove the VPS URL from `ANALYZER_URLS` after several successful analyses.

## Runtime notes

The FastAPI `/analyze` endpoint responds with `202 Accepted` and continues the
analysis in a background task. `sleepAfter` is set to 30 minutes so the
container is not stopped immediately after the HTTP response while FFmpeg,
librosa, or BirdNET are still running.

## Sizing

The initial container type is `standard-3` because the current VPS config gives
each analyzer instance up to 2 CPU and 2 GB RAM, and the service runs FFmpeg,
librosa, matplotlib, and BirdNET. Reduce to `standard-2` only after measuring
memory and processing time on real 500 MB / 600 second files.
