# Plateforme d'automatisation comptable

Stack NAS-first pour automatiser la réception documentaire, l'OCR, la validation humaine, l'écriture Excel, le rapprochement bancaire, les écritures comptables, les exports Inexweb et le DOE.

Le repo inclut aussi un POC local `Gmail -> OCR -> reponse email` qui peut tourner sur une machine locale avant migration vers un Synology.

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
docker compose -f infra/compose/docker-compose.yml up --build
```

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

Le worker traite les emails non lus avec pièce jointe, archive les fichiers localement, appelle Mistral OCR et renvoie un email de synthèse à `REPLY_TO_EMAIL`.
