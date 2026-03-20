from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.settings import Settings, get_settings


def _validate_balance(rows: list[dict[str, Any]]) -> None:
    totals: dict[str, Decimal] = {}
    for row in rows:
        group_id = row["entry_group_id"]
        balance = totals.setdefault(group_id, Decimal("0.00"))
        balance += Decimal(row["debit"]) - Decimal(row["credit"])
        totals[group_id] = balance
    unbalanced = {group_id: total for group_id, total in totals.items() if total != Decimal("0.00")}
    if unbalanced:
        raise RuntimeError(f"Unbalanced export batches: {unbalanced}")


def export_inexweb(output_path: str | None = None, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    if len(current.export_delimiter) != 1:
        raise ValueError("EXPORT_DELIMITER must be a single character")

    with get_connection(current) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, document_id, entry_group_id, journal, account_code, debit, credit, label, reference, entry_date
                FROM accounting_entries
                WHERE export_status = 'pending'
                ORDER BY entry_date ASC, entry_group_id ASC, line_no ASC
                """
            ).fetchall()
        ]
        if not rows:
            return {"output_path": None, "lines": 0}
        _validate_balance(rows)

        export_path = Path(output_path) if output_path else current.exports_inexweb_dir / f"inexweb_export_{datetime.utcnow():%Y%m%dT%H%M%SZ}.csv"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with export_path.open("w", encoding=current.export_encoding, newline="") as handle:
            writer = csv.writer(handle, delimiter=current.export_delimiter)
            writer.writerow(["DateEcriture", "Journal", "Compte", "Libelle", "PieceRef", "Debit", "Credit", "DocumentId"])
            for row in rows:
                writer.writerow(
                    [
                        row["entry_date"],
                        row["journal"],
                        row["account_code"],
                        row["label"],
                        row["reference"],
                        row["debit"],
                        row["credit"],
                        row["document_id"],
                    ]
                )

        connection.execute(
            """
            UPDATE accounting_entries
            SET export_status = 'exported'
            WHERE export_status = 'pending'
            """
        )
        connection.commit()

    return {
        "output_path": str(export_path),
        "lines": len(rows),
    }
