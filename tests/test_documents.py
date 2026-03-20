from __future__ import annotations

from apps.workers.documents.excel import write_document_to_excel
from apps.workers.documents.ingest import ingest_document
from apps.workers.documents.ocr_service import run_document_ocr
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


def test_ocr_creates_validation_task_when_missing_fields(tmp_path, test_settings, incomplete_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(incomplete_invoice_text, encoding="utf-8")
    ingested = ingest_document(str(invoice), "manual", settings=test_settings)

    result = run_document_ocr(ingested["document_id"], settings=test_settings)

    assert result["validation_required"] is True
    assert result["validation_token"]


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
