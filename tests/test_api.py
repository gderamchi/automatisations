from __future__ import annotations

from pathlib import Path

from apps.workers.common.database import get_connection


def test_internal_ingest_and_validation_flow(client, tmp_path, test_settings, incomplete_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(incomplete_invoice_text, encoding="utf-8")

    ingest = client.post(
        "/internal/documents/ingest",
        headers={"X-Internal-Token": "test-token"},
        json={"source_path": str(invoice), "source_kind": "manual"},
    )
    assert ingest.status_code == 200
    document_id = ingest.json()["document_id"]

    ocr = client.post(
        f"/internal/documents/{document_id}/ocr",
        headers={"X-Internal-Token": "test-token"},
    )
    assert ocr.status_code == 200
    validation_token = ocr.json()["validation_token"]

    page = client.get(f"/validate/{validation_token}", auth=("validator", "secret"))
    assert page.status_code == 200
    assert "Validation humaine" in page.text

    submit = client.post(
        f"/validate/{validation_token}",
        auth=("validator", "secret"),
        data={
            "document_type": "purchase_invoice",
            "document_kind": "invoice",
            "decision": "approve",
            "validator_name": "ops",
            "supply_type": "materiel",
            "supplier_name": "Fournisseur Test",
            "supplier_siret": "12345678912345",
            "invoice_number": "FAC-2026-999",
            "invoice_date": "2026-03-10",
            "due_date": "2026-03-25",
            "currency": "EUR",
            "net_amount": "1000.00",
            "vat_amount": "200.00",
            "gross_amount": "1200.00",
            "project_ref": "PROJET-X"
        },
    )
    assert submit.status_code == 200
    assert "Décision enregistrée" in submit.text

    with get_connection(test_settings) as connection:
        routing_token = connection.execute(
            "SELECT token FROM routing_tasks WHERE document_id = ? ORDER BY id DESC LIMIT 1",
            (document_id,),
        ).fetchone()["token"]

    routing_page = client.get(f"/route/{routing_token}", auth=("validator", "secret"))
    assert routing_page.status_code == 200
    assert "Routage documentaire" in routing_page.text

    routing_submit = client.post(
        f"/route/{routing_token}",
        auth=("validator", "secret"),
        data={
            "decision": "approve",
            "validator_name": "ops",
            "document_kind": "invoice",
            "supply_type": "materiel",
            "final_filename": "2026-03-10_INVOICE_FOURNISSEUR_TEST_MATERIEL_PROJET_X_1200.00.pdf",
            "routing_confidence": "0.94",
            "target_label": "PROJET-X",
            "standard_path": str(test_settings.classified_standard_dir / "2026-03-10_INVOICE_FOURNISSEUR_TEST_MATERIEL_PROJET_X_1200.00.pdf"),
            "accounting_path": str(test_settings.classified_accounting_dir / "2026-03-10_INVOICE_FOURNISSEUR_TEST_MATERIEL_PROJET_X_1200.00.pdf"),
            "worksite_path": str(test_settings.classified_worksites_dir / "projet-x" / "2026-03-10_INVOICE_FOURNISSEUR_TEST_MATERIEL_PROJET_X_1200.00.pdf"),
        },
    )
    assert routing_submit.status_code == 200
    assert "Dispatch: dispatched" in routing_submit.text
    assert (test_settings.classified_standard_dir / "2026-03-10_INVOICE_FOURNISSEUR_TEST_MATERIEL_PROJET_X_1200.00.pdf").exists()


def test_dashboard_requires_basic_auth(client):
    response = client.get("/dashboard")
    assert response.status_code == 401
