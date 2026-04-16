from __future__ import annotations

from pathlib import Path

from apps.workers.common.database import get_connection
from apps.workers.common.schemas import ValidationDecision
from apps.workers.documents.excel import write_document_to_excel
from apps.workers.documents.ingest import ingest_document
from apps.workers.documents.ocr_service import run_document_ocr
from apps.workers.documents.validation import apply_validation, get_validation_task
from openpyxl import load_workbook


def test_ingest_document_deduplicates(tmp_path, test_settings, sample_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(sample_invoice_text, encoding="utf-8")

    first = ingest_document(str(invoice), "manual", settings=test_settings)
    second = ingest_document(str(invoice), "manual", settings=test_settings)

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["document_id"] == first["document_id"]


def test_ocr_extracts_fields_and_auto_validates(tmp_path, test_settings, sample_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(sample_invoice_text, encoding="utf-8")
    ingested = ingest_document(str(invoice), "manual", settings=test_settings)

    result = run_document_ocr(ingested["document_id"], settings=test_settings)

    assert result["validation_required"] is False
    assert result["confidence"] >= test_settings.ocr_confidence_threshold
    with get_connection(test_settings) as connection:
        routing_count = connection.execute("SELECT COUNT(*) FROM routing_tasks").fetchone()[0]
    assert routing_count == 1


def test_ocr_creates_validation_task_when_missing_fields(tmp_path, test_settings, incomplete_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(incomplete_invoice_text, encoding="utf-8")
    ingested = ingest_document(str(invoice), "manual", settings=test_settings)

    result = run_document_ocr(ingested["document_id"], settings=test_settings)

    assert result["validation_required"] is True
    assert result["validation_token"]


def test_ocr_rerun_refreshes_pending_validation_task_payload(tmp_path, test_settings, incomplete_invoice_text, sample_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(incomplete_invoice_text, encoding="utf-8")
    ingested = ingest_document(str(invoice), "manual", settings=test_settings)

    first = run_document_ocr(ingested["document_id"], settings=test_settings)
    assert first["validation_required"] is True

    Path(ingested["stored_path"]).write_text(sample_invoice_text, encoding="utf-8")
    second = run_document_ocr(ingested["document_id"], settings=test_settings)

    assert second["validation_required"] is False
    with get_connection(test_settings) as connection:
        task = connection.execute(
            """
            SELECT status, extracted_payload_json, corrected_payload_json
            FROM validation_tasks
            WHERE document_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (ingested["document_id"],),
        ).fetchone()

    assert task["status"] == "approve"
    assert "FAC-2026-001" in task["extracted_payload_json"]
    assert "1200.00" in task["corrected_payload_json"]


def test_get_validation_task_returns_current_payload_after_rerun_approval(tmp_path, test_settings, incomplete_invoice_text, sample_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(incomplete_invoice_text, encoding="utf-8")
    ingested = ingest_document(str(invoice), "manual", settings=test_settings)

    first = run_document_ocr(ingested["document_id"], settings=test_settings)
    apply_validation(
        first["validation_token"],
        ValidationDecision(decision="reject", validator_name="qa"),
        settings=test_settings,
    )

    Path(ingested["stored_path"]).write_text(sample_invoice_text, encoding="utf-8")
    rerun = run_document_ocr(ingested["document_id"], settings=test_settings)
    assert rerun["validation_required"] is False

    task = get_validation_task(first["validation_token"], settings=test_settings)

    assert task is not None
    assert task["status"] == "approve"
    assert task["validation_status"] == "approved"
    assert task["extracted_payload"]["invoice_number"] == "FAC-2026-001"
    assert str(task["extracted_payload"]["gross_amount"]) == "1200.00"
    assert str(task["corrected_payload"]["gross_amount"]) == "1200.00"


def test_write_excel_appends_validated_document(tmp_path, test_settings, sample_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(sample_invoice_text, encoding="utf-8")
    ingested = ingest_document(str(invoice), "manual", settings=test_settings)
    run_document_ocr(ingested["document_id"], settings=test_settings)

    result = write_document_to_excel(ingested["document_id"], "purchases", settings=test_settings)

    workbook = load_workbook(result["workbook_path"])
    worksheet = workbook["Achats"]
    assert worksheet["B2"].value == "ACME BTP SAS"
    assert worksheet["F2"].value in ("1200.00", 1200, 1200.0)
