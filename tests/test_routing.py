from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from apps.workers.common.database import get_connection
from apps.workers.documents.ingest import ingest_document
from apps.workers.documents.ocr_service import run_document_ocr
from apps.workers.exports.weekly_accounting import build_weekly_accounting_zip
from apps.workers.routing.service import apply_routing, get_routing_task, parse_manual_hints
from apps.workers.common.schemas import RoutingDecision, RoutingProposal


def test_parse_manual_hints_extracts_supported_keys():
    hints = parse_manual_hints(
        "chantier: Residence Soleil",
        "fourniture: carburant\nclient: ACME\ninterfast_type: intervention\ninterfast_id: 42",
    )

    assert hints == {
        "chantier": "Residence Soleil",
        "fourniture": "carburant",
        "client": "ACME",
        "interfast_type": "intervention",
        "interfast_id": "42",
    }


def test_routing_task_keeps_manual_hints_and_dispatch_audit(tmp_path, test_settings, sample_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(sample_invoice_text, encoding="utf-8")
    ingested = ingest_document(
        str(invoice),
        "manual",
        metadata={
            "subject": "chantier: Residence Soleil",
            "body": "fourniture: materiel\ninterfast_type: intervention\ninterfast_id: 77",
        },
        settings=test_settings,
    )
    run_document_ocr(ingested["document_id"], settings=test_settings)

    with get_connection(test_settings) as connection:
        token = connection.execute(
            "SELECT token, proposed_payload_json FROM routing_tasks WHERE document_id = ?",
            (ingested["document_id"],),
        ).fetchone()
    proposed = json.loads(token["proposed_payload_json"])
    assert proposed["manual_hints"]["chantier"] == "Residence Soleil"
    assert proposed["manual_hints"]["fourniture"] == "materiel"
    assert proposed["interfast_target_type"] == "intervention"
    assert proposed["interfast_target_id"] == "77"

    task = get_routing_task(token["token"], settings=test_settings)
    corrected = RoutingProposal.model_validate(task["proposed_payload"])
    corrected.standard_path = str(test_settings.classified_standard_dir / corrected.final_filename)
    corrected.accounting_path = str(test_settings.classified_accounting_dir / corrected.final_filename)
    corrected.worksite_path = str(test_settings.classified_worksites_dir / "residence-soleil" / corrected.final_filename)

    result = apply_routing(
        token["token"],
        RoutingDecision(decision="approve", validator_name="ops", corrected_data=corrected),
        settings=test_settings,
    )

    assert result["decision"] == "approve"


def test_weekly_accounting_zip_uses_previous_week_documents(tmp_path, test_settings, sample_invoice_text):
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(sample_invoice_text, encoding="utf-8")
    ingested = ingest_document(str(invoice), "manual", settings=test_settings)
    run_document_ocr(ingested["document_id"], settings=test_settings)
    with get_connection(test_settings) as connection:
        connection.execute(
            """
            UPDATE documents
            SET created_at = '2026-03-18 09:00:00',
                final_filename = '2026-03-10_TEST.pdf'
            WHERE id = ?
            """,
            (ingested["document_id"],),
        )
        connection.commit()
    accounting_copy = test_settings.classified_accounting_dir / "2026-03-10_TEST.pdf"
    accounting_copy.parent.mkdir(parents=True, exist_ok=True)
    accounting_copy.write_text("dummy", encoding="utf-8")

    result = build_weekly_accounting_zip(reference_date=date(2026, 3, 29), settings=test_settings)

    zip_path = Path(str(result["zip_path"]))
    assert zip_path.exists()
    assert result["iso_week"] == 12
    assert ingested["document_id"] in result["document_ids"]
