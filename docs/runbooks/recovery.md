# Runbook recovery

## Panne applicative

1. Verifier `GET /healthz`.
2. Consulter `state/logs` et `job_runs`.
3. Relancer `docker compose ... restart`.

## Restauration

1. Restaurer `state/sqlite/automation.db`.
2. Restaurer `archive`, `exports`, `doe`.
3. Relancer la stack.
4. Rejouer les workflows n8n en echec de maniere idempotente.

## Cas critiques

- OCR indisponible: repasser temporairement en validation humaine stricte.
- Interfast indisponible: suspendre les workflows de sync, conserver le dernier cache SQLite.
- CSV bancaire invalide: corriger le mapping banque avant relance.
