from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from apps.workers.common.database import get_connection, init_db, job_run
from apps.workers.common.jsonio import dump_json
from apps.workers.common.schemas import OCRNormalized
from apps.workers.common.settings import Settings, get_settings
from apps.workers.connectors.mistral_client import MistralOCRClient


DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d")


def parse_french_decimal(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    normalized = raw.replace("\xa0", " ").replace(" ", "").replace("EUR", "").replace("€", "")
    normalized = normalized.replace(",", ".")
    normalized = re.sub(r"[^0-9.\-]", "", normalized)
    if not normalized:
        return None
    try:
        return Decimal(normalized).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_date_value(raw: str | None) -> date | None:
    if not raw:
        return None
    cleaned = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def first_pattern(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return None


def extract_supplier_name(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(keyword in lowered for keyword in ("facture", "invoice", "page", "tva", "siret")):
            continue
        return stripped[:150]
    return None


def compute_confidence(payload: OCRNormalized) -> float:
    score = 0.0
    if payload.supplier_name:
        score += 0.2
    if payload.invoice_number:
        score += 0.2
    if payload.invoice_date:
        score += 0.2
    if payload.gross_amount is not None:
        score += 0.2
    if payload.net_amount is not None:
        score += 0.1
    if payload.vat_amount is not None:
        score += 0.05
    if payload.supplier_siret:
        score += 0.05
    return round(min(score, 0.99), 2)


def normalize_ocr_payload(raw_payload: dict[str, Any], source_file_id: int | None = None) -> OCRNormalized:
    pages = raw_payload.get("pages", [])
    markdown_parts = [page.get("markdown", "") for page in pages if isinstance(page, dict)]
    if not markdown_parts and raw_payload.get("raw_text"):
        markdown_parts = [str(raw_payload["raw_text"])]
    text = "\n".join(markdown_parts).strip()

    supplier_name = extract_supplier_name(text)
    siret = first_pattern(text, [r"\b(\d{3}\s?\d{3}\s?\d{3}\s?\d{5})\b"])
    invoice_number = first_pattern(
        text,
        [
            r"^(?:facture|invoice)\s*(?:n(?:[°o])?|num(?:ero)?)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9\/_-]{2,})\s*$",
            r"(?:ref(?:erence)?|pi[eè]ce)\s*[:#\s-]*([A-Z0-9][A-Z0-9\/_-]{2,})",
        ],
    )
    invoice_date = parse_date_value(
        first_pattern(
            text,
            [
                r"(?:date(?: de)? facture|date d['’]emission|date)\s*[:#-]?\s*([0-9]{2}[/-][0-9]{2}[/-][0-9]{4})",
            ],
        )
    )
    due_date = parse_date_value(
        first_pattern(
            text,
            [
                r"(?:echeance|[ée]ch[ée]ance|due date)\s*[:#-]?\s*([0-9]{2}[/-][0-9]{2}[/-][0-9]{4})",
            ],
        )
    )
    gross_amount = parse_french_decimal(
        first_pattern(
            text,
            [
                r"^(?:total\s+ttc|montant\s+ttc|net\s+a\s+payer)\s*[:#-]?\s*([0-9\s.,]+)\s*$",
                r"^total\s*[:#-]?\s*([0-9\s.,]+)\s*$",
            ],
        )
    )
    net_amount = parse_french_decimal(
        first_pattern(
            text,
            [
                r"^(?:total\s+ht|montant\s+ht|net\s+ht)\s*[:#-]?\s*([0-9\s.,]+)\s*$",
            ],
        )
    )
    vat_amount = parse_french_decimal(
        first_pattern(
            text,
            [
                r"^(?:tva(?:\s+\d+(?:[.,]\d+)?%)?)\s*[:#-]?\s*([0-9\s.,]+)\s*$",
            ],
        )
    )
    project_ref = first_pattern(
        text,
        [
            r"(?:chantier|affaire|project)\s*[:#-]?\s*([^\n]+)",
        ],
    )

    payload = OCRNormalized(
        document_type="purchase_invoice",
        supplier_name=supplier_name,
        supplier_siret=siret.replace(" ", "") if siret else None,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        net_amount=net_amount,
        vat_amount=vat_amount,
        gross_amount=gross_amount,
        project_ref=project_ref,
        source_file_id=source_file_id,
        raw_text=text[:20000],
    )
    missing_fields = [
        field
        for field in ("supplier_name", "invoice_number", "invoice_date", "gross_amount")
        if getattr(payload, field) in (None, "")
    ]
    payload.missing_fields = missing_fields
    payload.confidence = compute_confidence(payload)
    return payload


def _mock_ocr(file_path: Path) -> dict[str, Any]:
    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw_text = file_path.read_text(encoding="latin-1", errors="ignore")
    return {
        "provider": "mock",
        "pages": [{"index": 0, "markdown": raw_text}],
    }


def run_document_ocr(document_id: int, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)

    with get_connection(current) as connection, job_run(connection, f"ocr:{document_id}"):
        record = connection.execute(
            """
            SELECT d.id, df.id AS file_id, df.stored_path
            FROM documents d
            JOIN document_files df ON df.document_id = d.id
            WHERE d.id = ? AND df.file_role = 'original'
            """,
            (document_id,),
        ).fetchone()
        if not record:
            raise KeyError(f"Document not found: {document_id}")

        file_path = Path(record["stored_path"])
        raw_payload = _mock_ocr(file_path) if current.ocr_mock_mode or not current.mistral_api_key else MistralOCRClient(current).process_file(file_path)
        normalized = normalize_ocr_payload(raw_payload, source_file_id=int(record["file_id"]))

        dump_json(current.archive_normalized_dir / f"document_{document_id}.json", normalized.model_dump(mode="json"))

        status = "validated" if normalized.confidence >= current.ocr_confidence_threshold and not normalized.missing_fields else "needs_review"
        connection.execute(
            """
            INSERT INTO ocr_extractions(document_id, provider, raw_payload_json, normalized_payload_json, confidence, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                raw_payload.get("provider", "mistral"),
                json.dumps(raw_payload, ensure_ascii=False),
                normalized.model_dump_json(),
                normalized.confidence,
                status,
            ),
        )
        connection.execute(
            """
            UPDATE documents
            SET document_type = ?, supplier_name = ?, supplier_siret = ?, invoice_number = ?,
                invoice_date = ?, due_date = ?, currency = ?, net_amount = ?, vat_amount = ?,
                gross_amount = ?, project_ref = ?, confidence = ?, normalized_payload_json = ?,
                validation_status = ?, current_stage = ?, validated_payload_json = CASE WHEN ? = 'validated' THEN ? ELSE validated_payload_json END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                normalized.document_type,
                normalized.supplier_name,
                normalized.supplier_siret,
                normalized.invoice_number,
                normalized.invoice_date.isoformat() if normalized.invoice_date else None,
                normalized.due_date.isoformat() if normalized.due_date else None,
                normalized.currency,
                str(normalized.net_amount) if normalized.net_amount is not None else None,
                str(normalized.vat_amount) if normalized.vat_amount is not None else None,
                str(normalized.gross_amount) if normalized.gross_amount is not None else None,
                normalized.project_ref,
                normalized.confidence,
                normalized.model_dump_json(),
                "approved" if status == "validated" else "pending",
                "ocr_validated" if status == "validated" else "needs_validation",
                status,
                normalized.model_dump_json(),
                document_id,
            ),
        )

        validation_required = status != "validated"
        token = None
        if validation_required:
            existing = connection.execute(
                """
                SELECT token FROM validation_tasks
                WHERE document_id = ? AND status = 'pending'
                ORDER BY id DESC
                LIMIT 1
                """,
                (document_id,),
            ).fetchone()
            token = existing["token"] if existing else secrets.token_urlsafe(18)
            if not existing:
                connection.execute(
                    """
                    INSERT INTO validation_tasks(document_id, token, extracted_payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (document_id, token, normalized.model_dump_json()),
                )

        connection.commit()
        return {
            "document_id": document_id,
            "confidence": normalized.confidence,
            "status": status,
            "validation_required": validation_required,
            "validation_token": token,
        }
