# Arborisis AI Agent Worker

Cloudflare Worker exposing `/chat` for the Arborisis site assistant.

## Deploy

```bash
cd workers/arborisis-ai-agent
npm install
wrangler secret put AGENT_SHARED_TOKEN
wrangler deploy
```

Set the same value in Laravel:

```env
ARBORISIS_AGENT_WORKER_URL=https://arborisis-ai-agent.<account>.workers.dev
ARBORISIS_AGENT_TOKEN=<same-token>
```

The worker uses Workers AI model `@cf/moonshotai/kimi-k2.6` and can call safe
public Arborisis API endpoints as tools.
