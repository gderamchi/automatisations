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
   - Renseigner `PUBLIC_BASE_URL` avec le domaine public HTTPS expose par le NAS.
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

## Durcissement recommande

- Changer `INTERNAL_API_TOKEN`, `VALIDATION_PASSWORD`.
- Passer `OCR_MOCK_MODE=false`.
- Exposer l'UI derriere VPN ou reverse proxy NAS.
- Sauvegarder regulierement `state/sqlite` et `archive`.
