# Plateforme d'automatisation comptable

Stack NAS-first pour automatiser la réception documentaire, l'OCR, la double validation humaine, le classement NAS, l'écriture Excel, le rapprochement bancaire, les écritures comptables, les exports Inexweb, le dispatch InterFast et le DOE.

Le repo inclut un worker local `Gmail -> OCR -> routing review -> dispatch` qui peut tourner sur une machine locale avant migration vers un Synology.

## Composants

- `apps/api`: API FastAPI + interface de validation et dashboard.
- `apps/workers`: logique métier Python pour OCR, Excel, Interfast, rapprochement, DOE, exports et notifications.
- `infra/compose`: `docker-compose.yml`, image Python partagée, reverse proxy local.
- `n8n/workflows`: workflows n8n versionnés.
- `config`: contrats JSON, mappings Excel, templates comptables et règles d'exemple.
- `docs`: architecture, déploiement, checklist client et runbooks.

## Démarrage local

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
sed -i '' 's#^DATA_ROOT=.*#DATA_ROOT='"$PWD"'/data#' .env
sed -i '' 's#^DB_PATH=.*#DB_PATH='"$PWD"'/data/state/sqlite/automation.db#' .env
python scripts/init_db.py
uvicorn apps.api.app.main:app --reload --host 0.0.0.0 --port 8080
```

## Docker Compose

```bash
cp .env.example .env
# En production NAS, remplacer par votre domaine public HTTPS
# PUBLIC_BASE_URL=https://auto.votre-domaine.tld
# ENVIRONMENT=production

# Initialisation DB one-shot (optionnel, l'API initialise aussi au demarrage)
docker compose -f infra/compose/docker-compose.yml --profile init run --rm worker-init

# Services continus
docker compose -f infra/compose/docker-compose.yml up --build -d api mail-worker n8n
```

En mode NAS, le service `mail-worker` tourne en continu et remplace l'execution locale via `start-local.sh`.
Les liens envoyes par email (`/review`, `/validate`, `/route`) sont construits a partir de `PUBLIC_BASE_URL`.

## Deploiement NAS avec mise a jour auto

Le fichier [infra/compose/docker-compose.nas.yml](infra/compose/docker-compose.nas.yml) utilise une image Docker publiee par GitHub Actions.

Flux cible:

1. Push sur `main`.
2. Workflow [docker-publish.yml](.github/workflows/docker-publish.yml) publie `ghcr.io/gderamchi/automatisations:main`.
3. `watchtower` (sur NAS) detecte la nouvelle image et redemarre `api` et `mail-worker` automatiquement.

One-time setup sur NAS:

```bash
docker compose -f infra/compose/docker-compose.nas.yml --profile init run --rm worker-init
docker compose -f infra/compose/docker-compose.nas.yml up -d api mail-worker n8n watchtower
```

Une fois ce setup fait, les updates de `api` et `mail-worker` se font via `git push` uniquement.

Notes:

- Les workflows n8n restent montes depuis le NAS (`n8n/workflows`) et doivent etre importes/geres cote n8n.
- Rollback rapide: fixer `AUTOMATISATIONS_IMAGE` dans `.env` sur un tag SHA publie par GHCR, puis relancer `docker compose ... up -d`.

## CLI workers

```bash
python -m apps.workers.cli init-db
python -m apps.workers.cli ingest --source-path /data/incoming/manual/facture.pdf --source-kind manual
python -m apps.workers.cli run-ocr --document-id 1
python -m apps.workers.cli write-excel --document-id 1 --mapping purchases
python -m apps.workers.cli sync-interfast
python -m apps.workers.cli import-bank --csv-path /data/incoming/manual/releve.csv
python -m apps.workers.cli export-inexweb
python -m apps.workers.cli rebuild-doe --project-id 1
python -m apps.workers.cli mail-worker --once
python -m apps.workers.cli mail-worker
python -m apps.workers.cli route-document --document-id 1
python -m apps.workers.cli dispatch-document --document-id 1
python -m apps.workers.cli weekly-accounting
```

## POC local Gmail

Renseigner au minimum dans `.env`:

```bash
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USERNAME=your-mail@gmail.com
IMAP_PASSWORD=your-app-password
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-mail@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=your-mail@gmail.com
REPLY_TO_EMAIL=your-mail@gmail.com
MISTRAL_API_KEY=...
MAIL_POLL_SECONDS=30
OCR_MOCK_MODE=false
```

Puis lancer:

```bash
python scripts/init_db.py
python -m apps.workers.cli mail-worker --once
python -m apps.workers.cli mail-worker
```

Le worker traite les emails non lus avec pièce jointe, archive les fichiers localement, appelle Mistral OCR, crée une validation OCR si nécessaire, crée ensuite une validation de routage, puis dispatch le document après validation humaine.
