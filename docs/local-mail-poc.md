# POC local Gmail -> OCR -> review -> dispatch

## Objectif

Faire tourner localement un worker Python qui:

- lit les nouveaux emails Gmail via IMAP
- récupère les pièces jointes
- OCRise les fichiers via Mistral
- extrait les champs utiles
- ouvre une validation humaine des données OCR si besoin
- ouvre une validation humaine de routage
- classe et dispatch les documents validés

## Variables minimales

- `IMAP_HOST=imap.gmail.com`
- `IMAP_PORT=993`
- `IMAP_USERNAME=<compte gmail>`
- `IMAP_PASSWORD=<mot de passe applicatif>`
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
- Les documents auto-validés ou validés manuellement créent ensuite une tâche de routage.
- Le dispatch final écrit sur le NAS, dans les classeurs Excel configurés et vers InterFast selon le mode actif.

## Limites du POC

- Le mode `expense` InterFast reste bloqué tant que l'endpoint privé exact n'est pas prouvé.
- Le mode `attachment` InterFast suppose un objet cible existant et un upload compatible avec l'API publique.
- Pas de traitement des formats bureautiques non supportés par le worker.
