# Architecture

## Vue d'ensemble

- `FastAPI` expose l'UI de validation, le dashboard et les endpoints internes appeles par `n8n`.
- `SQLite` est la source de verite canonique pour tous les objets fonctionnels et journaux d'execution.
- `n8n` orchestre les triggers IMAP, les surveillances de dossiers NAS, les appels internes, les notifications et les schedules.
- Les workers Python gerent la logique metier: OCR, Excel, Interfast, matching bancaire, generation d'ecritures, DOE, export Inexweb.

## Flux principaux

1. Reception
   - IMAP ou dossier NAS detecte un nouveau fichier.
   - `POST /internal/documents/ingest` stocke l'original en zone technique `processing/originals`, calcule le SHA256 et dedoublonne.
2. OCR
   - `POST /internal/documents/{id}/ocr` lance Mistral OCR ou le mode mock.
   - Le worker normalise vers le contrat `ocr_normalized` et archive le JSON.
   - Si confiance insuffisante, une tache de validation est creee.
3. Validation
   - L'utilisateur ouvre `/validate/{token}`.
   - Les champs de nommage CCM (`Type document`, `Categorie`, `Sous-categorie`) sont verrouilles par le catalogue `config/document_naming/ccm_v1.json`.
   - La decision met a jour la source canonique dans `documents` et `validation_tasks`.
4. Routage
   - `POST /internal/documents/{id}/route` construit la proposition chantier, classement et cible InterFast.
   - L'utilisateur ouvre `/route/{token}` puis valide le dispatch avec les listes CCM et le chantier.
5. Dispatch
   - L'original est copie vers `archive/originals` avec le nom officiel CCM.
   - Copies NAS: standard, compta, chantier, toutes avec le meme nom officiel.
   - Excel: `write_document_bundle`.
   - Le mapping client `client_grand_livre` s'active si `CLIENT_GRAND_LIVRE_WORKBOOK_PATH` est fourni; un echec Excel est journalise mais ne bloque pas le dispatch Interface / InterFast.
   - InterFast: adapter `disabled|attachment|expense`.
6. Lots complementaires
   - Interfast sync -> cache SQLite -> DOE.
   - Import banque -> matching -> anomalies.
   - Envoi hebdomadaire comptable -> ZIP + email + Telegram.

## Volumes NAS standardises

En production NAS, les dossiers documentaires (`archive/*` et `classified/*`)
sont exposes dans le partage DSM visible configure par `DOCUMENTS_SHARE_HOST_PATH`
afin que le client puisse les consulter dans File Station.

Les originaux entrants non valides restent d'abord dans le volume technique
`processing/originals`. Ils ne deviennent visibles dans `archive/originals` et
`classified/*` qu'apres validation du nommage CCM et du chantier requis.

- `incoming/email`
- `incoming/manual`
- `processing`
- `archive/originals`
- `archive/normalized`
- `classified/standard`
- `classified/accounting`
- `classified/worksites`
- `exports/inexweb`
- `doe`
- `state/sqlite`
- `state/cache`
- `state/logs`
