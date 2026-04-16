from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.schemas import OCRNormalized, ValidationDecision
from apps.workers.common.settings import Settings, get_settings
from apps.workers.routing.service import ensure_routing_task


def get_validation_task(token: str, settings: Settings | None = None) -> dict[str, Any] | None:
    current = settings or get_settings()
    init_db(current)
    with get_connection(current) as connection:
        row = connection.execute(
            """
            SELECT vt.id AS task_id, vt.token, vt.status, vt.extracted_payload_json, vt.corrected_payload_json,
                   d.id AS document_id, d.source_name, d.archived_path, d.current_stage, d.validation_status,
                   d.document_kind, d.supply_type, d.normalized_payload_json, d.validated_payload_json
            FROM validation_tasks vt
            JOIN documents d ON d.id = vt.document_id
            WHERE vt.token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        payload_json = row["extracted_payload_json"]
        corrected_payload_json = row["corrected_payload_json"]
        status = row["status"]
        if row["validation_status"] == "approved":
            payload_json = row["validated_payload_json"] or row["normalized_payload_json"] or payload_json
            corrected_payload_json = corrected_payload_json or payload_json
            status = "approve"
        return {
            "task_id": row["task_id"],
            "token": row["token"],
            "status": status,
            "document_id": row["document_id"],
            "source_name": row["source_name"],
            "archived_path": row["archived_path"],
            "current_stage": row["current_stage"],
            "validation_status": row["validation_status"],
            "document_kind": row["document_kind"],
            "supply_type": row["supply_type"],
            "extracted_payload": json.loads(payload_json),
            "corrected_payload": json.loads(corrected_payload_json) if corrected_payload_json else None,
        }


def list_pending_validation_tasks(settings: Settings | None = None) -> list[dict[str, Any]]:
    current = settings or get_settings()
    init_db(current)
    with get_connection(current) as connection:
        rows = connection.execute(
            """
            SELECT vt.token, vt.created_at, d.id AS document_id, d.source_name, d.supplier_name, d.invoice_number, d.confidence
            FROM validation_tasks vt
            JOIN documents d ON d.id = vt.document_id
            WHERE vt.status = 'pending'
            ORDER BY vt.created_at ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def apply_validation(token: str, decision: ValidationDecision, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    with get_connection(current) as connection:
        task = connection.execute(
            """
            SELECT id, document_id, extracted_payload_json
            FROM validation_tasks
            WHERE token = ?
            """,
            (token,),
        ).fetchone()
        if not task:
            raise KeyError(f"Validation token not found: {token}")

        extracted = OCRNormalized.model_validate_json(task["extracted_payload_json"])
        corrected = decision.corrected_data or extracted

        if decision.decision == "approve":
            connection.execute(
                """
                UPDATE documents
                SET document_kind = ?, supply_type = ?, supplier_name = ?, supplier_siret = ?, invoice_number = ?, invoice_date = ?, due_date = ?,
                    currency = ?, net_amount = ?, vat_amount = ?, gross_amount = ?, project_ref = ?, document_type = ?,
                    validation_status = 'approved', current_stage = 'validated', validated_payload_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    corrected.document_kind,
                    corrected.supply_type,
                    corrected.supplier_name,
                    corrected.supplier_siret,
                    corrected.invoice_number,
                    corrected.invoice_date.isoformat() if corrected.invoice_date else None,
                    corrected.due_date.isoformat() if corrected.due_date else None,
                    corrected.currency,
                    str(corrected.net_amount) if corrected.net_amount is not None else None,
                    str(corrected.vat_amount) if corrected.vat_amount is not None else None,
                    str(corrected.gross_amount) if corrected.gross_amount is not None else None,
                    corrected.project_ref,
                    corrected.document_type,
                    corrected.model_dump_json(),
                    task["document_id"],
                ),
            )
        elif decision.decision == "reject":
            connection.execute(
                """
                UPDATE documents
                SET validation_status = 'rejected', current_stage = 'rejected', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task["document_id"],),
            )
        else:
            connection.execute(
                """
                UPDATE documents
                SET validation_status = 'pending', current_stage = 'needs_fix', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task["document_id"],),
            )

        connection.execute(
            """
            UPDATE validation_tasks
            SET status = ?, corrected_payload_json = ?, validator_name = ?, validation_notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                decision.decision,
                corrected.model_dump_json() if decision.decision != "reject" else None,
                decision.validator_name,
                decision.notes,
                task["id"],
            ),
        )
        connection.commit()
    if decision.decision == "approve":
        ensure_routing_task(task["document_id"], force_refresh=True, settings=current)

    return {
        "document_id": task["document_id"],
        "decision": decision.decision,
    }


def get_document_file_path(document_id: int, settings: Settings | None = None) -> Path:
    current = settings or get_settings()
    with get_connection(current) as connection:
        row = connection.execute(
            """
            SELECT archived_path
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
    if not row:
        raise KeyError(f"Document not found: {document_id}")
    return Path(row["archived_path"])
