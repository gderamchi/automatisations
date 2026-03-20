from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.hashing import slugify
from apps.workers.common.settings import Settings, get_settings


HEADER_ALIASES = {
    "booking_date": ["date", "date operation", "date opération", "booking_date"],
    "value_date": ["date valeur", "value_date"],
    "label": ["libelle", "libellé", "label", "description"],
    "reference": ["reference", "référence", "ref"],
    "amount": ["montant", "amount", "debit", "credit"],
}


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_amount(value: str | None) -> Decimal:
    if not value:
        return Decimal("0.00")
    normalized = value.replace("\xa0", " ").replace(" ", "").replace("€", "").replace(",", ".")
    normalized = normalized.replace("(", "-").replace(")", "")
    try:
        return Decimal(normalized).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"Unable to parse amount: {value}") from exc


def _detect_delimiter(path: Path) -> str:
    sample = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,|\t").delimiter
    except csv.Error:
        return ";"


def _match_header(fieldnames: list[str], aliases: list[str]) -> str | None:
    lowered = {field.lower().strip(): field for field in fieldnames}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    return None


def import_bank_csv(csv_path: str, source_label: str | None = None, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Bank CSV not found: {csv_path}")

    delimiter = _detect_delimiter(path)
    imported = 0
    skipped = 0
    source_file = source_label or path.name

    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle, get_connection(current) as connection:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            raise RuntimeError("CSV file is missing headers")
        fields = {
            key: _match_header(reader.fieldnames, aliases)
            for key, aliases in HEADER_ALIASES.items()
        }
        if not fields["booking_date"] or not fields["label"] or not fields["amount"]:
            raise RuntimeError("CSV columns do not match expected booking_date/label/amount headers")

        for index, row in enumerate(reader, start=1):
            booking_date = parse_date(row.get(fields["booking_date"]))
            if not booking_date:
                skipped += 1
                continue
            value_date = parse_date(row.get(fields["value_date"])) if fields["value_date"] else None
            label = (row.get(fields["label"]) or "").strip()
            amount = parse_amount(row.get(fields["amount"]))
            reference = (row.get(fields["reference"]) or "").strip() if fields["reference"] else None
            external_id = f"{slugify(source_file)}-{index}-{abs(hash((booking_date, label, str(amount))))}"

            connection.execute(
                """
                INSERT OR IGNORE INTO bank_transactions(source_file, external_id, booking_date, value_date, label, reference, amount, raw_payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_file,
                    external_id,
                    booking_date,
                    value_date,
                    label,
                    reference,
                    str(amount),
                    str(dict(row)),
                ),
            )
            imported += 1
        connection.commit()

    return {
        "source_file": source_file,
        "imported": imported,
        "skipped": skipped,
        "delimiter": delimiter,
    }
