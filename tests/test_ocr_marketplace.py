from __future__ import annotations

from datetime import date

from apps.workers.documents.ocr_service import extract_document_insights, extract_table_totals, normalize_ocr_payload


def test_marketplace_invoice_patterns_are_extracted(amazon_paid_invoice_text):
    payload = normalize_ocr_payload({"pages": [{"markdown": amazon_paid_invoice_text}]})
    insights = extract_document_insights(amazon_paid_invoice_text)

    assert payload.supplier_name == "Amazon Services Europe S.a.r.L."
    assert payload.invoice_number == "DS-ASE-INV-FR-2024-22544185"
    assert payload.invoice_date is not None
    assert str(payload.gross_amount) == "19.99"
    assert payload.confidence >= 0.8
    assert insights["payment_reference"] == "13XOT2IZ9FQSE96V"
    assert insights["order_number"] == "407-6967530-0479500"


def test_amazon_webp_invoice_patterns_are_extracted(amazon_webp_invoice_text):
    payload = normalize_ocr_payload({"pages": [{"markdown": amazon_webp_invoice_text}]})

    assert payload.supplier_name == "Amazon EU S.à r.l., Succursale Française"
    assert payload.invoice_number == "EUVINS1-OFS-FR-305509734"
    assert payload.invoice_date == date(2016, 12, 28)
    assert str(payload.net_amount) == "53.33"
    assert str(payload.vat_amount) == "10.66"
    assert str(payload.gross_amount) == "63.99"
    assert payload.missing_fields == []
    assert payload.confidence >= 0.82


def test_extract_table_totals_handles_multiline_summary_rows(amazon_webp_invoice_text):
    gross, net, vat = extract_table_totals(amazon_webp_invoice_text)

    assert str(gross) == "63.99"
    assert str(net) == "53.33"
    assert str(vat) == "10.66"
