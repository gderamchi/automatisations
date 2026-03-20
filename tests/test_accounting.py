from __future__ import annotations

from apps.workers.accounting.entries import generate_entries_for_document
from apps.workers.common.database import get_connection
from apps.workers.documents.ingest import ingest_document
from apps.workers.documents.ocr_service import run_document_ocr


def test_generate_entries_balanced(tmp_path, test_settings, sample_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(sample_invoice_text, encoding="utf-8")
    ingested = ingest_document(str(invoice), "manual", settings=test_settings)
    run_document_ocr(ingested["document_id"], settings=test_settings)

    result = generate_entries_for_document(ingested["document_id"], settings=test_settings)

    assert result["debit_total"] == result["credit_total"]
    with get_connection(test_settings) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM accounting_entries WHERE document_id = ?",
            (ingested["document_id"],),
        ).fetchone()[0]
    assert count == 3
