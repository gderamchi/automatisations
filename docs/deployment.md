# Deploiement

## Pre-requis

- Docker et Docker Compose sur le Synology ou sur une VM reliee au NAS.
- Un partage NAS pour `/data`.
- Acces IMAP.
- Cle API Mistral.
- Acces Interfast et spec export cabinet.

## Etapes

1. Copier `.env.example` vers `.env` et renseigner les secrets.
2. Monter le partage NAS en volume Docker pour `/data`.
3. Initialiser la base:

```bash
python scripts/init_db.py
```

4. Demarrer la stack:

```bash
docker compose -f infra/compose/docker-compose.yml up --build -d
```

5. Importer les workflows n8n depuis `n8n/workflows`.
6. Tester:
   - `GET /healthz`
   - une ingestion manuelle
   - un OCR
   - une validation
   - un export Inexweb

## Durcissement recommande

- Changer `INTERNAL_API_TOKEN`, `VALIDATION_PASSWORD`.
- Passer `OCR_MOCK_MODE=false`.
- Exposer l'UI derriere VPN ou reverse proxy NAS.
- Sauvegarder regulierement `state/sqlite` et `archive`.
