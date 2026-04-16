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
from apps.workers.routing.service import ensure_routing_task, parse_manual_hints


DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d")

FRENCH_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}

_FRENCH_MONTH_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(FRENCH_MONTHS) + r")\s+(\d{4})",
    flags=re.IGNORECASE,
)

NOISY_SUPPLIER_LINES = {
    "# payé",
    "payé",
    "# paid",
    "paid",
    "facture",
    "invoice",
}

LEGAL_ENTITY_MARKERS = (
    "succursale",
    "s.à r.l",
    "s.a.r.l",
    "sarl",
    "sas",
    "sasu",
    "eurl",
    "llc",
    "ltd",
    "inc",
    "gmbh",
)


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
    match = _FRENCH_MONTH_RE.search(cleaned)
    if match:
        day, month_name, year = int(match.group(1)), match.group(2).lower(), int(match.group(3))
        month = FRENCH_MONTHS.get(month_name)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    return None


def normalize_space(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", " ", value).strip()


def first_pattern(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return None


def _looks_like_legal_supplier_line(line: str) -> bool:
    lowered = normalize_space(line.lower()) or ""
    if not lowered:
        return False
    if lowered.startswith(("adresse de facturation", "adresse de livraison")):
        return False
    return any(marker in lowered for marker in LEGAL_ENTITY_MARKERS)


def extract_supplier_name(text: str) -> str | None:
    priority_patterns = [
        r"TVA déclarée par\s+([^\n]+)",
        r"(?:émise par|emise par|issued by|facturé par|facture émise par)\s*([^\n]+)",
        r"Vendu par\s+([^\n]+)",
    ]
    for pattern in priority_patterns:
        candidate = normalize_space(first_pattern(text, [pattern]))
        if candidate:
            candidate = candidate.lstrip("#").strip()
        if candidate and candidate.lower() not in NOISY_SUPPLIER_LINES:
            return candidate[:150]

    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped and _looks_like_legal_supplier_line(stripped):
            return stripped[:150]

    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered in NOISY_SUPPLIER_LINES:
            continue
        if any(keyword in lowered for keyword in ("facture", "invoice", "page", "tva", "siret", "date de", "numéro de", "numero de")):
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
    if payload.project_ref:
        score += 0.03
    return round(min(score, 0.99), 2)


def extract_document_insights(text: str) -> dict[str, str]:
    insights: dict[str, str] = {}
    patterns = {
        "payment_status": [
            r"#\s*(Payé|Paid|Impayé|Unpaid)\b",
            r"\b(Payé|Paid|Impayé|Unpaid)\b",
        ],
        "payment_reference": [
            r"Référence de paiement\s+([A-Z0-9-]+)",
            r"Payment reference\s+([A-Z0-9-]+)",
        ],
        "order_number": [
            r"Numéro de la commande\s+([A-Z0-9-]+)",
            r"Order number\s+([A-Z0-9-]+)",
        ],
        "seller_name": [
            r"Vendu par\s+([^\n]+)",
        ],
        "issuer_name": [
            r"TVA déclarée par\s+([^\n]+)",
            r"([A-Z][A-Za-z0-9 .,'àâçéèêëîïôûùüÿñæœ&/-]{3,}S(?:\.| )?a(?:\.| )?r(?:\.| )?l\.?)",
        ],
    }
    for key, key_patterns in patterns.items():
        candidate = normalize_space(first_pattern(text, key_patterns))
        if candidate:
            insights[key] = candidate
    return insights


def extract_table_totals(text: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Extract gross, net, and vat amounts from markdown table cells.

    Looks for cells where a label (Total HT, Total TVA, TTC, etc.) is
    followed by an amount in the same or next cell.
    """
    amount_re = re.compile(r"([0-9][0-9\s.,]*[0-9])\s*€")

    gross: Decimal | None = None
    net: Decimal | None = None
    vat: Decimal | None = None
    rows: list[list[str]] = []

    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if cells and not cells[0]:
            cells = cells[1:]
        if cells and not cells[-1]:
            cells = cells[:-1]
        if not cells:
            continue
        rows.append(cells)
        for i, cell in enumerate(cells):
            lowered = normalize_space(cell.lower()) or ""
            is_ht = any(k in lowered for k in ("hors tva total", "total ht", "montant ht", "net ht"))
            is_ttc = any(k in lowered for k in ("total ttc", "montant ttc", "net à payer", "net a payer"))
            is_tva = any(k in lowered for k in ("total tva", "montant tva")) or (
                "tva" in lowered and "%" in lowered
            )
            is_total_line = lowered.rstrip(":") == "total"
            if not (is_ht or is_ttc or is_tva or is_total_line):
                continue
            # Amount is in subsequent cells (not the label cell — avoids catching percentages)
            for candidate in cells[i + 1 :]:
                match = amount_re.search(candidate)
                if not match:
                    match = re.search(r"([0-9][0-9\s.,]*[0-9])", candidate)
                if match:
                    value = parse_french_decimal(match.group(1))
                    if value and value > 0:
                        if is_total_line and gross is None:
                            gross = value
                        elif is_ttc and gross is None:
                            gross = value
                        elif is_ht and net is None:
                            net = value
                        elif is_tva and vat is None:
                            vat = value
                        break

    for index, row in enumerate(rows):
        label_positions: dict[str, int] = {}
        for column, cell in enumerate(row):
            lowered = normalize_space(cell.lower()) or ""
            if "hors tva total" in lowered or "total ht" in lowered or "montant ht" in lowered or "net ht" in lowered:
                label_positions.setdefault("net", column)
            elif "total ttc" in lowered or "montant ttc" in lowered or "net à payer" in lowered or "net a payer" in lowered:
                label_positions.setdefault("gross", column)
            elif "tva total" in lowered or "total tva" in lowered or "montant tva" in lowered:
                label_positions.setdefault("vat", column)

        if not label_positions:
            continue

        for next_row in rows[index + 1 :]:
            amount_positions: dict[int, Decimal] = {}
            for column, cell in enumerate(next_row):
                if "%" in cell and "€" not in cell:
                    continue
                match = amount_re.search(cell)
                if match:
                    value = parse_french_decimal(match.group(1))
                    if value is not None:
                        amount_positions[column] = value
            if not amount_positions:
                continue

            if net is None and "net" in label_positions:
                net = amount_positions.get(label_positions["net"], net)
            if vat is None and "vat" in label_positions:
                vat = amount_positions.get(label_positions["vat"], vat)
            if gross is None and "gross" in label_positions:
                gross = amount_positions.get(label_positions["gross"], gross)
            break

    return gross, net, vat


def normalize_ocr_payload(
    raw_payload: dict[str, Any],
    source_file_id: int | None = None,
    manual_hints: dict[str, Any] | None = None,
) -> OCRNormalized:
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
            r"(?:num[eé]ro(?:\s+de)?(?:\s+la)?\s+facture|invoice number|facture number)\s*[:#-]?\s*([A-Z0-9][A-Z0-9\/_.-]{2,})",
            r"^(?:facture|invoice)\s*(?:n(?:[°o])?|num(?:ero)?)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9\/_-]{2,})\s*$",
            r"(?:ref(?:erence)?|pi[eè]ce)\s*[:#\s-]*([A-Z0-9][A-Z0-9\/_-]{2,})",
        ],
    )
    _french_months_alt = "|".join(FRENCH_MONTHS)
    invoice_date = parse_date_value(
        first_pattern(
            text,
            [
                r"date de la facture(?:\s*/\s*date de(?:\s+la)?\s+[^\n:]+)?\s*[:#-]?\s*([0-9]{2}[./-][0-9]{2}[./-][0-9]{4})",
                r"(?:date(?: de)? facture|date d[‘’]emission|date de la facture/date de la livraison|date de la facture|date de livraison|date)\s*[:#-]?\s*([0-9]{2}[./-][0-9]{2}[./-][0-9]{4})",
                r"(?:le\s+)?(\d{1,2}\s+(?:" + _french_months_alt + r")\s+\d{4})",
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
                r"(?:net\s+[àa]\s+payer\s+ttc|total\s+ttc|montant\s+ttc)\s*[:#|]?\s*([0-9][0-9\s.,]*[0-9])\s*€?",
                r"(?:total\s+[àa]\s+payer|facture\s+total)\s*[:#|]?\s*([0-9][0-9\s.,]*[0-9])\s*€?",
                r"(?:net\s+[àa]\s+payer)\s*[:#|]?\s*([0-9][0-9\s.,]*[0-9])\s*€?",
                r"^\|\s*total\s*:?\s*\|\s*([0-9][0-9\s.,]*[0-9])\s*€?",
            ],
        )
    )
    net_amount = parse_french_decimal(
        first_pattern(
            text,
            [
                r"(?:total\s+ht(?:\s+net)?|montant\s+ht|net\s+ht)\s*[:#|]?\s*([0-9][0-9\s.,]*[0-9])\s*€?",
            ],
        )
    )
    vat_amount = parse_french_decimal(
        first_pattern(
            text,
            [
                r"(?:total\s+tva|montant\s+tva)\s*(?:\(?\s*\d+[\s.,]*\d*\s*%\s*\)?)?\s*[:#|]?\s*([0-9][0-9\s.,]*[0-9])\s*€?",
                r"(?:^|\|)\s*tva\s*(?:\(?\s*\d+[\s.,]*\d*\s*%\s*\)?)?\s*[:#|]?\s*([0-9][0-9\s.,]*[0-9])\s*€?",
            ],
        )
    )
    if gross_amount is None or net_amount is None or vat_amount is None:
        table_gross, table_net, table_vat = extract_table_totals(text)
        gross_amount = gross_amount or table_gross
        net_amount = net_amount or table_net
        vat_amount = vat_amount or table_vat
    project_ref = first_pattern(
        text,
        [
            r"(?:chantier|affaire|project)\s*[:#-]?\s*([^\n]+)",
        ],
    )

    payload = OCRNormalized(
        document_type="purchase_invoice",
        document_kind="invoice" if invoice_number or "facture" in text.lower() or "invoice" in text.lower() else "unknown",
        supply_type=str((manual_hints or {}).get("fourniture") or "") or None,
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
        manual_hints=manual_hints or {},
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
            SELECT d.id, df.id AS file_id, df.stored_path, d.metadata_json
            FROM documents d
            JOIN document_files df ON df.document_id = d.id
            WHERE d.id = ? AND df.file_role = 'original'
            """,
            (document_id,),
        ).fetchone()
        if not record:
            raise KeyError(f"Document not found: {document_id}")

        file_path = Path(record["stored_path"])
        metadata = json.loads(record["metadata_json"] or "{}")
        manual_hints = parse_manual_hints(metadata.get("subject"), metadata.get("body"))
        raw_payload = _mock_ocr(file_path) if current.ocr_mock_mode or not current.mistral_api_key else MistralOCRClient(current).process_file(file_path)
        normalized = normalize_ocr_payload(raw_payload, source_file_id=int(record["file_id"]), manual_hints=manual_hints)

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
            SET document_type = ?, document_kind = ?, supply_type = ?, supplier_name = ?, supplier_siret = ?, invoice_number = ?,
                invoice_date = ?, due_date = ?, currency = ?, net_amount = ?, vat_amount = ?,
                gross_amount = ?, project_ref = ?, confidence = ?, normalized_payload_json = ?,
                validation_status = ?, current_stage = ?, validated_payload_json = CASE WHEN ? = 'validated' THEN ? ELSE validated_payload_json END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                normalized.document_type,
                normalized.document_kind,
                normalized.supply_type,
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
        if not validation_required:
            ensure_routing_task(document_id, force_refresh=True, settings=current)
        return {
            "document_id": document_id,
            "confidence": normalized.confidence,
            "status": status,
            "validation_required": validation_required,
            "validation_token": token,
        }
