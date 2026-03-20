# Runbook go-live

1. Verifier `.env` et les secrets montes.
2. Verifier la presence des classeurs Excel et mappings.
3. Basculer `OCR_MOCK_MODE=false`.
4. Lancer `docker compose ... up -d --build`.
5. Importer les workflows n8n.
6. Envoyer un email de test avec facture jointe.
7. Verifier:
   - `documents` cree
   - `ocr_extractions` alimente
   - tache de validation creee ou auto-validation
   - ecriture Excel fonctionnelle
8. Lancer un import bancaire test.
9. Lancer un export Inexweb test.
10. Archiver les preuves de validation dans le dossier projet.
