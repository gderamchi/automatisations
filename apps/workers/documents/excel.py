from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.jsonio import load_json
from apps.workers.common.settings import Settings, get_settings
from apps.workers.common.templating import render_value_template, substitute_env


def _find_mapping(mapping_name: str, settings: Settings) -> Path:
    direct = settings.excel_mappings_dir / f"{mapping_name}.json"
    example = settings.excel_mappings_dir / f"{mapping_name}.example.json"
    if direct.exists():
        return direct
    if example.exists():
        return example
    raise FileNotFoundError(f"Excel mapping not found: {mapping_name}")


def _load_document_payload(document_id: int, settings: Settings) -> dict[str, Any]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT source_name, supplier_name, supplier_siret, invoice_number, invoice_date, due_date, currency,
                   net_amount, vat_amount, gross_amount, project_ref, document_type, document_kind, supply_type,
                   payment_status, payment_date, payment_method, final_filename, validated_payload_json, normalized_payload_json
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
    if not row:
        raise KeyError(f"Document not found: {document_id}")
    payload_json = row["validated_payload_json"] or row["normalized_payload_json"]
    payload = json.loads(payload_json) if payload_json else {}
    payload.setdefault("source_name", row["source_name"])
    payload.setdefault("supplier_name", row["supplier_name"])
    payload.setdefault("supplier_siret", row["supplier_siret"])
    payload.setdefault("invoice_number", row["invoice_number"])
    payload.setdefault("invoice_date", row["invoice_date"])
    payload.setdefault("due_date", row["due_date"])
    payload.setdefault("currency", row["currency"])
    payload.setdefault("net_amount", row["net_amount"])
    payload.setdefault("vat_amount", row["vat_amount"])
    payload.setdefault("gross_amount", row["gross_amount"])
    payload.setdefault("project_ref", row["project_ref"])
    payload.setdefault("document_type", row["document_type"])
    payload.setdefault("document_kind", row["document_kind"])
    payload.setdefault("supply_type", row["supply_type"])
    payload.setdefault("payment_status", row["payment_status"])
    payload.setdefault("payment_date", row["payment_date"])
    payload.setdefault("payment_method", row["payment_method"])
    payload.setdefault("final_filename", row["final_filename"])
    return payload


def _copy_row_style(worksheet, source_row: int, target_row: int, max_column: int) -> None:
    for column in range(1, max_column + 1):
        source_cell = worksheet.cell(row=source_row, column=column)
        target_cell = worksheet.cell(row=target_row, column=column)
        if source_cell.has_style:
            target_cell._style = copy.copy(source_cell._style)
        if source_cell.number_format:
            target_cell.number_format = source_cell.number_format
        if source_cell.font:
            target_cell.font = copy.copy(source_cell.font)
        if source_cell.fill:
            target_cell.fill = copy.copy(source_cell.fill)
        if source_cell.border:
            target_cell.border = copy.copy(source_cell.border)
        if source_cell.alignment:
            target_cell.alignment = copy.copy(source_cell.alignment)


def write_document_to_excel(document_id: int, mapping_name: str, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    mapping = load_json(_find_mapping(mapping_name, current))
    raw_path = mapping["workbook_path"].replace("${DATA_ROOT}", str(current.data_root))
    workbook_path = Path(substitute_env(raw_path))
    workbook_path.parent.mkdir(parents=True, exist_ok=True)
    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")

    payload = _load_document_payload(document_id, current)
    workbook = load_workbook(workbook_path)
    worksheet = workbook[mapping["sheet"]]
    key_column = mapping.get("key_column", "A")
    start_row = int(mapping.get("start_row", 2))
    current_row = start_row
    while worksheet[f"{key_column}{current_row}"].value not in (None, ""):
        current_row += 1

    template_row = int(mapping.get("template_row", max(start_row, current_row - 1)))
    if template_row and template_row != current_row:
        _copy_row_style(worksheet, template_row, current_row, worksheet.max_column)

    for field_name, column in mapping["columns"].items():
        rendered = payload.get(field_name)
        if rendered is None and field_name in mapping.get("constants", {}):
            rendered = render_value_template(mapping["constants"][field_name], payload)
        worksheet[f"{column}{current_row}"] = rendered

    workbook.save(workbook_path)

    with get_connection(current) as connection:
        connection.execute(
            """
            UPDATE documents
            SET current_stage = 'excel_written', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (document_id,),
        )
        connection.commit()

    return {
        "document_id": document_id,
        "workbook_path": str(workbook_path),
        "sheet": mapping["sheet"],
        "row": current_row,
    }


def write_document_bundle(
    document_id: int,
    mapping_names: list[str] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    current = settings or get_settings()
    names = mapping_names or list(current.default_excel_mappings)
    results = [write_document_to_excel(document_id, mapping_name, settings=current) for mapping_name in names]
    with get_connection(current) as connection:
        connection.execute(
            """
            UPDATE documents
            SET current_stage = 'excel_written', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (document_id,),
        )
        connection.commit()
    return {
        "document_id": document_id,
        "mappings": results,
    }
