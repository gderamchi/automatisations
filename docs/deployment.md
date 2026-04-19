# Deploiement

## Pre-requis

- Docker et Docker Compose sur le Synology ou sur une VM reliee au NAS.
- Un partage NAS pour `/data`.
- Acces IMAP.
- Cle API Mistral.
- Acces Interfast et spec export cabinet.

## Etapes

1. Copier `.env.example` vers `.env` et renseigner les secrets.
   - Positionner `ENVIRONMENT=production` sur NAS.
   - Renseigner `PUBLIC_BASE_URL` avec l'URL publique HTTPS exacte exposee par le NAS.
   - Si le reverse proxy publie l'app sous un sous-chemin, inclure ce sous-chemin dans `PUBLIC_BASE_URL` (ex: `https://domaine.tld/automatisations`).
   - Renseigner `INTERNAL_API_BASE_URL` (par defaut `http://api:8080`) pour les workflows n8n.
2. Monter le partage NAS en volume Docker pour `/data`.
3. Initialiser la base (one-shot):

```bash
docker compose -f infra/compose/docker-compose.yml --profile init run --rm worker-init
```

4. Demarrer les services continus:

```bash
docker compose -f infra/compose/docker-compose.yml up --build -d api mail-worker n8n
```

5. Importer les workflows n8n depuis `n8n/workflows`.
6. Tester:
   - `GET /healthz`
   - une ingestion manuelle
   - un OCR
   - une validation
   - un export Inexweb
   - un email de test avec verification que les liens recus utilisent `PUBLIC_BASE_URL` (jamais localhost)
   - un clic reel sur un lien de mail (`/review`, `/validate` ou `/route`) pour verifier que l'URL publique NAS ouvre bien l'interface, en racine comme sous sous-chemin

## Durcissement recommande

- Changer `INTERNAL_API_TOKEN`, `VALIDATION_PASSWORD`.
- Passer `OCR_MOCK_MODE=false`.
- Exposer l'UI derriere VPN ou reverse proxy NAS.
- Sauvegarder regulierement `state/sqlite` et `archive`.
- Aucun changement de schema DB ni regeneration de token n'est necessaire pour activer un sous-chemin public.

## Mode auto-update (recommande)

Objectif: ne plus toucher le NAS pour chaque release.

### Principe

1. A chaque push sur `main`, GitHub Actions publie une image Docker dans GHCR.
2. Le NAS execute [infra/compose/docker-compose.nas.yml](infra/compose/docker-compose.nas.yml).
3. `watchtower` detecte les nouvelles images et redemarre automatiquement les services applicatifs.

Scope actuel auto-update:

- `api` et `mail-worker` sont auto-updates via image GHCR.
- Les workflows n8n sont montes depuis le NAS (`n8n/workflows`) et ne sont pas auto-synchronises par image.

### Initialisation one-time sur NAS

```bash
docker compose -f infra/compose/docker-compose.nas.yml --profile init run --rm worker-init
docker compose -f infra/compose/docker-compose.nas.yml up -d api mail-worker n8n watchtower
```

### Prerequis GHCR

- Si le package GHCR est public: aucun login supplementaire requis.
- Si le package GHCR est prive: configurer un login registre GHCR sur le NAS (PAT avec `read:packages`).
- Le workflow publie une image multi-architecture (`linux/amd64`, `linux/arm64`) pour compatibilite Synology.

### Rollback

1. Modifier `.env` sur le NAS et fixer `AUTOMATISATIONS_IMAGE` sur un tag SHA connu (`ghcr.io/gderamchi/automatisations:sha-...`).
2. Relancer:

```bash
docker compose -f infra/compose/docker-compose.nas.yml up -d api mail-worker
```
