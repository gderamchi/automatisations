from __future__ import annotations

from pathlib import Path

from apps.workers.common.database import get_connection
from apps.workers.common.settings import get_settings
from apps.workers.doe.service import upsert_project


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
    upsert_project(
        external_project_id="53032",
        project_code="CCM-002",
        project_name="12576-6-RONDEAU - CCM2026-002",
        metadata={"client": {"id": 1950319, "name": "FRAMPAS"}},
        settings=test_settings,
    )

    routing_page = client.get(f"/route/{routing_token}", auth=("validator", "secret"))
    assert routing_page.status_code == 200
    assert "Routage documentaire" in routing_page.text
    assert 'name="worksite_external_id"' in routing_page.text
    assert 'name="expense_label"' in routing_page.text
    assert 'name="gross_amount"' in routing_page.text

    routing_submit = client.post(
        f"/route/{routing_token}",
        auth=("validator", "secret"),
        data={
            "decision": "approve",
            "validator_name": "ops",
            "document_kind": "invoice",
            "supply_type": "materiel",
            "expense_label": "Achat de matériel Fournisseur Test",
            "final_filename": "2026-03-10_INVOICE_FOURNISSEUR_TEST_MATERIEL_PROJET_X_1200.00.pdf",
            "routing_confidence": "0.94",
            "supplier_name": "Fournisseur Test",
            "invoice_number": "FAC-2026-999",
            "invoice_date": "2026-03-10",
            "currency": "EUR",
            "net_amount": "1000.00",
            "vat_amount": "200.00",
            "gross_amount": "1200.00",
            "worksite_external_id": "53032",
            "standard_path": str(test_settings.classified_standard_dir / "2026-03-10_INVOICE_FOURNISSEUR_TEST_MATERIEL_PROJET_X_1200.00.pdf"),
            "accounting_path": str(test_settings.classified_accounting_dir / "2026-03-10_INVOICE_FOURNISSEUR_TEST_MATERIEL_PROJET_X_1200.00.pdf"),
            "worksite_path": str(test_settings.classified_worksites_dir / "projet-x" / "2026-03-10_INVOICE_FOURNISSEUR_TEST_MATERIEL_PROJET_X_1200.00.pdf"),
        },
    )
    assert routing_submit.status_code == 200
    assert "Dispatch: dispatched" in routing_submit.text
    assert "Excel: 1 erreur(s) non bloquante(s)" in routing_submit.text
    assert (test_settings.classified_standard_dir / "2026-03-10_INVOICE_FOURNISSEUR_TEST_MATERIEL_PROJET_X_1200.00.pdf").exists()
    with get_connection(test_settings) as connection:
        updated = connection.execute(
            "SELECT project_ref, client_external_id, worksite_external_id, gross_amount FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
    assert updated["project_ref"] == "12576-6-RONDEAU - CCM2026-002"
    assert updated["client_external_id"] == "1950319"
    assert updated["worksite_external_id"] == "53032"
    assert updated["gross_amount"] == "1200.00"


def test_dashboard_requires_basic_auth(client):
    response = client.get("/dashboard")
    assert response.status_code == 401


def test_ui_links_support_public_base_path_prefix(client, monkeypatch, test_settings, incomplete_invoice_text, tmp_path):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test/automatisations")
    get_settings.cache_clear()

    invoice = tmp_path / "invoice-prefix.txt"
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

    root_redirect = client.get("/", follow_redirects=False)
    assert root_redirect.status_code == 307
    assert root_redirect.headers["location"] == "/automatisations/dashboard"

    dashboard_page = client.get("/dashboard", auth=("validator", "secret"))
    assert dashboard_page.status_code == 200
    assert '/automatisations/static/styles.css' in dashboard_page.text
    assert f'/automatisations/validate/{validation_token}' in dashboard_page.text

    prefixed_dashboard_page = client.get("/automatisations/dashboard", auth=("validator", "secret"))
    assert prefixed_dashboard_page.status_code == 200
    assert '/automatisations/static/styles.css' in prefixed_dashboard_page.text
    assert f'/automatisations/validate/{validation_token}' in prefixed_dashboard_page.text

    validation_page = client.get(f"/validate/{validation_token}", auth=("validator", "secret"))
    assert validation_page.status_code == 200
    assert '/automatisations/static/styles.css' in validation_page.text
    assert f'/automatisations/files/{document_id}' in validation_page.text

    prefixed_validation_page = client.get(f"/automatisations/validate/{validation_token}", auth=("validator", "secret"))
    assert prefixed_validation_page.status_code == 200
    assert '/automatisations/static/styles.css' in prefixed_validation_page.text
    assert f'/automatisations/files/{document_id}' in prefixed_validation_page.text


def test_ui_links_remain_root_when_public_base_url_has_no_path(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    get_settings.cache_clear()

    root_redirect = client.get("/", follow_redirects=False)
    assert root_redirect.status_code == 307
    assert root_redirect.headers["location"] == "/dashboard"

    dashboard = client.get("/dashboard", auth=("validator", "secret"))
    assert dashboard.status_code == 200
    assert '/static/styles.css' in dashboard.text
