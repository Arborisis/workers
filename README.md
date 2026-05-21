# Arborisis Workers

<p align="center">
  <img src="https://raw.githubusercontent.com/Arborisis/.github/main/profile/logo.svg" alt="Arborisis Logo" width="150" />
</p>

<p align="center">
  <em>Workers Cloudflare pour la plateforme Arborisis.</em>
</p>

<p align="center">
  <a href="https://github.com/Arborisis/workers/actions"><img src="https://img.shields.io/github/actions/workflow/status/Arborisis/workers/ci.yml?branch=main&style=flat-square&label=CI" alt="CI" /></a>
  <a href="https://github.com/Arborisis/workers/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Arborisis/workers?style=flat-square" alt="License" /></a>
</p>

---

## Overview

Workers Cloudflare (edge computing) qui gerent les traitements asynchrones et les proxy de la plateforme Arborisis. Deploies sur le reseau mondial de Cloudflare pour une latence minimale.

### Workers

| Worker | Description |
|--------|-------------|
| **arborisis-ai-agent** | Agent IA pour le chat et les recommandations |
| **audio-analysis-orchestrator** | Orchestration du pipeline d'analyse audio |
| **audio-analyzer-container** | Conteneur d'analyse audio serverless |
| **r2-proxy** | Proxy securise pour l'acces au stockage R2 |

## Architecture

```
workers/
├── arborisis-ai-agent/         # Agent IA
│   ├── src/index.ts           # Entry point
│   ├── wrangler.toml          # Config Cloudflare
│   └── package.json
│
├── audio-analysis-orchestrator/ # Orchestration analyse
│   ├── src/index.ts
│   ├── wrangler.toml
│   └── package.json
│
├── audio-analyzer-container/   # Analyse audio
│   ├── src/index.ts
│   ├── wrangler.toml
│   └── package.json
│
└── r2-proxy/                   # Proxy R2
    ├── src/index.ts
    ├── wrangler.toml
    └── package.json
```

## Stack technique

- **TypeScript**
- **Wrangler** - CLI Cloudflare
- **Cloudflare Workers Runtime**

## Installation

```bash
git clone https://github.com/Arborisis/workers.git
cd workers

# Installer pour chaque worker
cd arborisis-ai-agent && npm install
cd ../audio-analysis-orchestrator && npm install
cd ../audio-analyzer-container && npm install
cd ../r2-proxy && npm install
```

## Developpement

```bash
# Mode developpement local (pour chaque worker)
cd [worker-name]
npm run dev

# TypeScript check
npx tsc --noEmit

# Deploy
npx wrangler deploy
```

## Configuration

Chaque worker possede un fichier `wrangler.toml` :

```toml
name = "arborisis-ai-agent"
main = "src/index.ts"
compatibility_date = "2024-01-01"

[vars]
API_URL = "https://api.arborisis.com"

[[env.production.vars]]
API_URL = "https://api.arborisis.com"
```

### Variables d'environnement requises

```env
CLOUDFLARE_API_TOKEN=       # Token API Cloudflare
CLOUDFLARE_ACCOUNT_ID=      # ID du compte Cloudflare
```

## Deploiement

### Manuel

```bash
cd [worker-name]
npx wrangler deploy
```

### CI/CD (automatique)

Le deploiement est automatique via GitHub Actions sur push sur `main`.

## Integration

```
Laravel App <-> Cloudflare Workers <-> R2 Storage
                |
                +-> AI Agent (recommandations)
                +-> Audio Orchestrator (pipeline)
                +-> R2 Proxy (fichiers securises)
```

## License

[MIT License](LICENSE)
