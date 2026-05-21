# Worker Cloudflare R2 — Arborisis

Ce Worker sert de proxy sécurisé devant le bucket R2 `arborisis` via le custom domain `storage.arborisis.com`.

## Architecture

```
Utilisateur → storage.arborisis.com/audio/3/xxx.wav?expires=...&signature=...
              ↓
         [Worker Cloudflare]
              ↓
    Vérification HMAC (expires + signature)
              ↓
    Si OK → Récupération dans la binding R2 → Fichier servi
    Si KO → 403 Forbidden
```

## Prérequis

- Wrangler CLI installé : `npm install -g wrangler`
- Connexion au compte Cloudflare : `wrangler login`

## Configuration

### 1. Secret partagé

La clé de signature (`SIGNING_KEY`) doit être **identique** côté Laravel et côté Worker.

```bash
cd workers/r2-proxy
wrangler secret put SIGNING_KEY
# Coller la valeur de R2_SIGNING_KEY du .env Laravel
```

### 2. Déploiement

```bash
wrangler deploy
```

### 3. Route du Worker

Ajoute le route `storage.arborisis.com/*` au Worker dans le dashboard Cloudflare :

1. Dashboard → Workers & Pages → `arborisis-r2-proxy`
2. Triggers → Custom Domains → Add Custom Domain
3. `storage.arborisis.com`

> **Important** : Retire le custom domain natif R2 du bucket avant, sinon il y aura conflit.

## Développement local

```bash
wrangler dev --local
```

## Structure

```
r2-proxy/
├── src/
│   └── index.ts      # Logique HMAC + serve R2
├── wrangler.toml     # Config binding + compatibilité
├── package.json
└── README.md
```

## Sécurité

- Les URLs expirent après le TTL configuré (défaut 60 min)
- La signature est un HMAC-SHA256 de `path:expires`
- Sans `expires` ou `signature` valide → 403
- Le bucket R2 n'est accessible que via le Worker (plus public une fois le custom domain natif retiré)
