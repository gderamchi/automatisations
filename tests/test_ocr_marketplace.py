from __future__ import annotations

from apps.workers.documents.ocr_service import extract_document_insights, normalize_ocr_payload


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
