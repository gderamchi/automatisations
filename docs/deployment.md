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
   - Pour l'ecriture Excel NAS, verifier que `ACCOUNTING_SHARE_HOST_PATH`, `ACCOUNTING_SHARE_MOUNT` et `ACCOUNTING_SHARE_ROOT` pointent vers le partage comptable actif.
2. Monter le partage NAS en volume Docker pour `/data`.
   - Le compose NAS garde l'etat technique dans le volume Docker `automation_data`.
   - Les documents client sont exposes dans `${DOCUMENTS_SHARE_HOST_PATH:-/volume1/Professionnel_CCM/12_AUTOMATISATION/documents}` avec `archive/originals`, `archive/normalized` et `classified`.
   - Le compose NAS monte aussi le partage `Professionnel_CCM` vers `${ACCOUNTING_SHARE_MOUNT:-/mnt/professionnel_ccm}` pour les grands livres et la tresorerie.
3. Initialiser la base (one-shot):

```bash
docker compose -f infra/compose/docker-compose.nas.yml --profile init run --rm worker-init
```

4. Demarrer les services continus:

```bash
docker compose -f infra/compose/docker-compose.nas.yml up -d api mail-worker n8n watchtower
```

5. Importer les workflows n8n depuis `n8n/workflows`.
6. Tester:
   - `GET /healthz`
   - une ingestion manuelle
   - un OCR
   - une validation
   - une validation de routage avec verification d'ecriture dans la tresorerie mensuelle, le grand livre client et le grand livre fournisseur
   - un export Inexweb
   - un email de test avec verification que les liens recus utilisent `PUBLIC_BASE_URL` (jamais localhost)

## Durcissement recommande

- Changer `INTERNAL_API_TOKEN`, `VALIDATION_PASSWORD`.
- Passer `OCR_MOCK_MODE=false`.
- Exposer l'UI derriere VPN ou reverse proxy NAS.
- Sauvegarder regulierement `state/sqlite` et le dossier DSM visible des documents.

## Dossier documents visible DSM

En production NAS, les PDF ne doivent pas rester uniquement dans `/volume1/@docker/...`.
Le compose NAS expose les artefacts documentaires dans:

```text
/volume1/Professionnel_CCM/12_AUTOMATISATION/documents
```

Ce dossier contient:

- `archive/originals`: originaux recus, conserves par date.
- `archive/normalized`: JSON OCR normalises.
- `classified/standard`: copies classees apres dispatch.
- `classified/accounting`: copies comptables apres dispatch.
- `classified/worksites`: copies par chantier apres dispatch.

Avant de demarrer le compose NAS, creer les dossiers visibles:

```bash
mkdir -p /volume1/Professionnel_CCM/12_AUTOMATISATION/documents/archive/originals
mkdir -p /volume1/Professionnel_CCM/12_AUTOMATISATION/documents/archive/normalized
mkdir -p /volume1/Professionnel_CCM/12_AUTOMATISATION/documents/classified
```

Pour migrer un ancien volume Docker vers ce dossier visible:

```bash
OLD_DATA=$(/usr/local/bin/docker volume inspect auto_automation_data --format '{{.Mountpoint}}')
VISIBLE_DATA=/volume1/Professionnel_CCM/12_AUTOMATISATION/documents
rsync -a "$OLD_DATA/archive/originals/" "$VISIBLE_DATA/archive/originals/"
rsync -a "$OLD_DATA/archive/normalized/" "$VISIBLE_DATA/archive/normalized/"
rsync -a "$OLD_DATA/classified/" "$VISIBLE_DATA/classified/"
```

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
