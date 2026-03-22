# POC local Gmail -> OCR -> reponse email

## Objectif

Faire tourner localement un worker Python qui:

- lit les nouveaux emails Gmail via IMAP
- récupère les pièces jointes
- OCRise les fichiers via Mistral
- extrait les champs utiles
- envoie un email de retour avec le résumé extrait

## Variables minimales

- `IMAP_HOST=imap.gmail.com`
- `IMAP_PORT=993`
- `IMAP_USERNAME=<compte gmail>`
- `IMAP_PASSWORD=<mot de passe applicatif>`
- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_USERNAME=<compte gmail>`
- `SMTP_PASSWORD=<mot de passe applicatif>`
- `SMTP_FROM=<compte gmail>`
- `REPLY_TO_EMAIL=<adresse de retour>`
- `MISTRAL_API_KEY=<clé OCR>`
- `MAIL_POLL_SECONDS=30`
- `MAIL_REPLY_SUBJECT_PREFIX=[AUTOMATISATIONS OCR]`
- `MARK_PROCESSED_SEEN=true`
- `OCR_MOCK_MODE=false`

## Lancement

```bash
python scripts/init_db.py
python -m apps.workers.cli mail-worker --once
python -m apps.workers.cli mail-worker
```

## Comportement

- Le worker ne traite que les emails `UNSEEN`.
- Il ignore ses propres réponses automatiques grâce à un header interne et au préfixe de sujet.
- Il marque les emails traités comme lus pour éviter les redoublements.
- Il garde un état persistant dans SQLite via `processed_emails`.
- Les pièces jointes et résultats OCR sont archivés dans `data/`.

## Limites du POC

- Pas d'intégration à l'app chantier.
- Pas de validation UI.
- Pas de classification chantier avancée.
- Pas de traitement des formats bureautiques non supportés par le worker.
