from __future__ import annotations

from apps.workers.accounting.entries import generate_entries_for_document
from apps.workers.banking.importer import import_bank_csv
from apps.workers.banking.matching import match_bank_transactions
from apps.workers.documents.ingest import ingest_document
from apps.workers.documents.ocr_service import run_document_ocr
from apps.workers.exports.inexweb import export_inexweb


def _prepare_accounted_document(tmp_path, test_settings, sample_invoice_text) -> int:
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(sample_invoice_text, encoding="utf-8")
    ingested = ingest_document(str(invoice), "manual", settings=test_settings)
    run_document_ocr(ingested["document_id"], settings=test_settings)
    generate_entries_for_document(ingested["document_id"], settings=test_settings)
    return ingested["document_id"]


def test_bank_matching_finds_certain_match(tmp_path, test_settings, sample_invoice_text):
    _prepare_accounted_document(tmp_path, test_settings, sample_invoice_text)
    csv_file = tmp_path / "bank.csv"
    csv_file.write_text(
        "Date;Libelle;Reference;Montant\n10/03/2026;VIR ACME BTP FAC-2026-001;FAC-2026-001;1200,00\n",
        encoding="utf-8",
    )

    import_bank_csv(str(csv_file), settings=test_settings)
    result = match_bank_transactions(settings=test_settings)

    assert result["certain_match"] == 1


def test_export_inexweb_writes_csv(tmp_path, test_settings, sample_invoice_text):
    _prepare_accounted_document(tmp_path, test_settings, sample_invoice_text)
    export_path = tmp_path / "inexweb.csv"

    result = export_inexweb(str(export_path), settings=test_settings)

    assert result["lines"] == 3
    assert export_path.exists()
    assert "FAC-2026-001" in export_path.read_text(encoding=test_settings.export_encoding)
