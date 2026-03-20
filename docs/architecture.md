# Architecture

## Vue d'ensemble

- `FastAPI` expose l'UI de validation, le dashboard et les endpoints internes appeles par `n8n`.
- `SQLite` est la source de verite canonique pour tous les objets fonctionnels et journaux d'execution.
- `n8n` orchestre les triggers IMAP, les surveillances de dossiers NAS, les appels internes, les notifications et les schedules.
- Les workers Python gerent la logique metier: OCR, Excel, Interfast, matching bancaire, generation d'ecritures, DOE, export Inexweb.

## Flux principaux

1. Reception
   - IMAP ou dossier NAS detecte un nouveau fichier.
   - `POST /internal/documents/ingest` archive l'original, calcule le SHA256 et dedoublonne.
2. OCR
   - `POST /internal/documents/{id}/ocr` lance Mistral OCR ou le mode mock.
   - Le worker normalise vers le contrat `ocr_normalized` et archive le JSON.
   - Si confiance insuffisante, une tache de validation est creee.
3. Validation
   - L'utilisateur ouvre `/validate/{token}`.
   - La decision met a jour la source canonique dans `documents` et `validation_tasks`.
4. Comptabilisation
   - Excel: `write_document_to_excel`.
   - Ecritures: `generate_entries_for_document`.
   - Export: `POST /internal/exports/inexweb`.
5. Lots complementaires
   - Interfast sync -> cache SQLite -> DOE.
   - Import banque -> matching -> anomalies.

## Volumes NAS standardises

- `incoming/email`
- `incoming/manual`
- `processing`
- `archive/originals`
- `archive/normalized`
- `exports/inexweb`
- `doe`
- `state/sqlite`
- `state/cache`
- `state/logs`
