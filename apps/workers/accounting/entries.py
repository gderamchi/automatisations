from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from apps.workers.accounting.rules import match_supplier_rule
from apps.workers.common.database import get_connection, init_db
from apps.workers.common.jsonio import load_json
from apps.workers.common.settings import Settings, get_settings
from apps.workers.common.templating import render_value_template


def _to_decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _fetch_document_payload(document_id: int, settings: Settings) -> dict[str, Any]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT validation_status, validated_payload_json, normalized_payload_json, invoice_date
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
    if not row:
        raise KeyError(f"Document not found: {document_id}")
    if row["validation_status"] not in {"approved", "auto-approved"}:
        raise RuntimeError(f"Document {document_id} is not approved")
    payload_json = row["validated_payload_json"] or row["normalized_payload_json"]
    return json.loads(payload_json)


def _load_template(document_type: str, settings: Settings) -> dict[str, Any]:
    direct = settings.templates_dir / f"{document_type}.json"
    example = settings.templates_dir / f"{document_type}.example.json"
    if direct.exists():
        return load_json(direct)
    if example.exists():
        return load_json(example)
    raise FileNotFoundError(f"Accounting template not found for {document_type}")


def generate_entries_for_document(document_id: int, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    payload = _fetch_document_payload(document_id, current)
    template = _load_template(payload.get("document_type", "purchase_invoice"), current)
    rule = match_supplier_rule(payload, current)
    if not rule:
        raise RuntimeError(f"No supplier rule matched document {document_id}")

    context = dict(payload)
    context.update(
        {
            "compte_charge": rule.compte_charge,
            "compte_tva": rule.compte_tva,
            "compte_tiers": rule.compte_tiers,
            "journal": rule.journal,
            **rule.metadata,
        }
    )

    entry_group_id = str(uuid.uuid4())
    debit_total = Decimal("0.00")
    credit_total = Decimal("0.00")

    with get_connection(current) as connection:
        connection.execute("DELETE FROM accounting_entries WHERE document_id = ?", (document_id,))
        for line_no, line in enumerate(template["lines"], start=1):
            amount = _to_decimal(context.get(line["amount_field"]))
            if amount == Decimal("0.00"):
                continue
            account_code = render_value_template(line["account_code"], context)
            label = render_value_template(line["label"], context)
            journal = render_value_template(template["journal"], context)
            debit = amount if line["side"] == "debit" else Decimal("0.00")
            credit = amount if line["side"] == "credit" else Decimal("0.00")
            debit_total += debit
            credit_total += credit
            connection.execute(
                """
                INSERT INTO accounting_entries(document_id, entry_group_id, line_no, journal, account_code, debit, credit, label, reference, entry_date, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    entry_group_id,
                    line_no,
                    journal,
                    account_code,
                    str(debit),
                    str(credit),
                    label,
                    payload.get("invoice_number"),
                    payload.get("invoice_date"),
                    json.dumps({"template_id": template["template_id"]}, ensure_ascii=False),
                ),
            )

        if debit_total != credit_total:
            connection.rollback()
            raise RuntimeError(f"Unbalanced entries for document {document_id}: debit={debit_total} credit={credit_total}")

        connection.execute(
            """
            UPDATE documents
            SET current_stage = 'accounted', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (document_id,),
        )
        connection.commit()

    return {
        "document_id": document_id,
        "entry_group_id": entry_group_id,
        "debit_total": str(debit_total),
        "credit_total": str(credit_total),
    }
