# Runbook go-live

1. Verifier `.env` et les secrets montes.
2. Verifier la presence des classeurs Excel et mappings.
3. Basculer `OCR_MOCK_MODE=false`.
4. Lancer `docker compose ... up -d --build`.
5. Importer les workflows n8n.
6. Envoyer un email de test avec pièce jointe et hints éventuels (`chantier: ...`, `fourniture: ...`).
7. Verifier:
   - `documents` cree
   - `ocr_extractions` alimente
   - tache de validation OCR creee ou auto-validation
   - tache de routage creee
   - copies NAS presentes
   - ecritures Excel fonctionnelles
   - tentative InterFast tracee dans `dispatch_attempts`
8. Lancer un import bancaire test.
9. Lancer un export Inexweb test.
10. Lancer `POST /internal/weekly-accounting` ou `python -m apps.workers.cli weekly-accounting`.
11. Archiver les preuves de validation dans le dossier projet.
