from __future__ import annotations

import json
import secrets
import shutil
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.hashing import compute_sha256, slugify
from apps.workers.common.schemas import RoutingDecision, RoutingProposal
from apps.workers.common.settings import Settings, get_settings
from apps.workers.documents.excel import build_excel_review_payload, get_excel_form_options, has_nas_excel_targets, write_document_bundle
from apps.workers.notifications.service import queue_notification, send_telegram_message_if_configured
from apps.workers.routing.interfast_writer import build_interfast_adapter


DOCUMENT_KIND_KEYWORDS = {
    "purchase_order": ["bon de commande", "purchase order", "commande"],
    "receipt": ["reçu", "receipt", "ticket", "cb ", "carte bleue"],
    "quotation": ["devis", "quotation", "quote"],
    "invoice": ["facture", "invoice"],
    "credit_note": ["avoir", "credit note"],
}

SUPPLY_TYPE_KEYWORDS = {
    "carburant": ["carburant", "diesel", "gazole", "gasoil", "essence", "station service"],
    "materiel": ["materiel", "matériel", "outillage", "equipement", "équipement"],
    "hotel": ["hotel", "hôtel", "hébergement"],
    "repas": ["repas", "restaurant", "restauration"],
    "peage": ["péage", "peage", "autoroute", "toll"],
    "consommable": ["consommable", "fourniture", "epi", "epi ", "visserie"],
}

HINT_ALIASES = {
    "chantier": "chantier",
    "project": "chantier",
    "projet": "chantier",
    "client": "client",
    "type": "type",
    "document": "type",
    "fourniture": "fourniture",
    "supply": "fourniture",
    "interfast_type": "interfast_type",
    "interfast_id": "interfast_id",
}

EXPENSE_LABEL_PREFIXES = {
    "carburant": "Achat de carburant",
    "materiel": "Achat de matériel",
    "hotel": "Hébergement",
    "repas": "Repas au restaurant",
    "peage": "Billet de péage",
    "consommable": "Achats divers",
    "unknown": "Dépense",
}


def parse_manual_hints(subject: str | None, body: str | None) -> dict[str, str]:
    hints: dict[str, str] = {}
    for chunk in (subject or "", body or ""):
        for raw_line in chunk.splitlines():
            if ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            normalized = HINT_ALIASES.get(key.strip().lower())
            if not normalized:
                continue
            cleaned_value = value.strip()
            if cleaned_value:
                hints[normalized] = cleaned_value
    return hints


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return None


def _load_document_context(document_id: int, settings: Settings) -> dict[str, Any]:
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
    if not row:
        raise KeyError(f"Document not found: {document_id}")
    document = dict(row)
    payload_json = document.get("validated_payload_json") or document.get("normalized_payload_json")
    payload = json.loads(payload_json) if payload_json else {}
    for field in (
        "document_kind",
        "supply_type",
        "supplier_name",
        "supplier_siret",
        "invoice_number",
        "invoice_date",
        "due_date",
        "currency",
        "net_amount",
        "vat_amount",
        "gross_amount",
        "project_ref",
    ):
        if document.get(field) not in (None, ""):
            payload[field] = document[field]
    metadata = json.loads(document.get("metadata_json") or "{}")
    hints = parse_manual_hints(document.get("source_subject"), document.get("source_body"))
    if isinstance(payload.get("manual_hints"), dict):
        hints = {**payload["manual_hints"], **hints}
    if isinstance(metadata.get("manual_hints"), dict):
        hints = {**metadata["manual_hints"], **hints}
    context = {
        "document": document,
        "payload": payload,
        "metadata": metadata,
        "hints": hints,
    }
    return context


def _normalize_supplier_for_expense_label(raw_value: str | None) -> str:
    supplier = str(raw_value or "").strip().lstrip("#").strip()
    if not supplier:
        return "fournisseur"
    lowered = supplier.lower()
    if "amazon" in lowered:
        return "Amazon"
    replacements = [
        "succursale française",
        "succursale francaise",
        "s.à r.l.",
        "s.a.r.l.",
        "sarl",
        "sas",
        "sasu",
        "eurl",
        "llc",
        "ltd",
        "inc",
    ]
    cleaned = supplier
    for token in replacements:
        cleaned = cleaned.replace(token, "").replace(token.title(), "").strip(" ,-")
    return cleaned or supplier


def _build_expense_label(context: dict[str, Any], proposal: RoutingProposal) -> str:
    payload = context["payload"]
    supplier = _normalize_supplier_for_expense_label(payload.get("supplier_name"))
    prefix = EXPENSE_LABEL_PREFIXES.get(proposal.supply_type or "unknown", EXPENSE_LABEL_PREFIXES["unknown"])
    if proposal.supply_type == "unknown" and proposal.document_kind == "purchase_order":
        prefix = "Commande"
    return f"{prefix} {supplier}".strip()


def list_worksite_options(settings: Settings | None = None) -> list[dict[str, str | None]]:
    current = settings or get_settings()
    init_db(current)
    with get_connection(current) as connection:
        rows = connection.execute(
            """
            SELECT external_project_id, project_code, project_name, metadata_json
            FROM doe_projects
            ORDER BY project_code ASC, project_name ASC
            """
        ).fetchall()
    options: list[dict[str, str | None]] = []
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        client = metadata.get("client") or {}
        label = " ".join(filter(None, [row["project_code"], row["project_name"]])).strip() or row["project_name"]
        options.append(
            {
                "external_project_id": str(row["external_project_id"]) if row["external_project_id"] else None,
                "project_code": row["project_code"],
                "project_name": row["project_name"],
                "label": label,
                "client_external_id": str(client.get("id") or metadata.get("clientId") or "") or None,
                "client_name": client.get("name") or None,
                "status": metadata.get("status") or None,
            }
        )
    return options


def _apply_worksite_selection(proposal: RoutingProposal, settings: Settings) -> RoutingProposal:
    if not proposal.worksite_external_id:
        return proposal
    options = {
        option["external_project_id"]: option
        for option in list_worksite_options(settings)
        if option["external_project_id"]
    }
    selected = options.get(str(proposal.worksite_external_id))
    if not selected:
        return proposal
    proposal.worksite_external_id = str(selected["external_project_id"])
    proposal.target_label = selected["project_name"] or selected["label"]
    proposal.client_external_id = selected["client_external_id"] or proposal.client_external_id
    return proposal


def _normalize_kind(payload: dict[str, Any], hints: dict[str, str]) -> str:
    hinted = hints.get("type")
    haystack = " ".join(
        filter(
            None,
            [
                hinted,
                payload.get("document_kind"),
                payload.get("document_type"),
                payload.get("raw_text"),
            ],
        )
    ).lower()
    if hinted:
        normalized = slugify(hinted).replace("-", "_")
        if normalized:
            return normalized
    for kind, keywords in DOCUMENT_KIND_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return kind
    if payload.get("document_type") == "credit_note":
        return "credit_note"
    if payload.get("document_type") == "sales_invoice":
        return "sales_invoice"
    if payload.get("document_type") == "purchase_invoice":
        return "invoice"
    return "unknown"


def _normalize_supply_type(payload: dict[str, Any], hints: dict[str, str]) -> str:
    hinted = hints.get("fourniture")
    haystack = " ".join(
        filter(
            None,
            [
                hinted,
                payload.get("supply_type"),
                payload.get("supplier_name"),
                payload.get("raw_text"),
            ],
        )
    ).lower()
    if hinted:
        normalized = slugify(hinted).replace("-", "_")
        if normalized:
            return normalized
    for supply_type, keywords in SUPPLY_TYPE_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return supply_type
    return "consommable" if "fourniture" in haystack else "unknown"


def _project_query_terms(context: dict[str, Any]) -> list[str]:
    payload = context["payload"]
    document = context["document"]
    metadata = context["metadata"]
    hints = context["hints"]
    terms = [
        hints.get("chantier"),
        hints.get("client"),
        payload.get("project_ref"),
        payload.get("supplier_name"),
        document.get("source_name"),
        metadata.get("subject"),
        metadata.get("sender_email"),
    ]
    return [str(term).strip() for term in terms if term]


def _extract_nested_value(data: Any, keys: set[str]) -> str | None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and value not in (None, ""):
                return str(value)
            nested = _extract_nested_value(value, keys)
            if nested:
                return nested
    if isinstance(data, list):
        for item in data:
            nested = _extract_nested_value(item, keys)
            if nested:
                return nested
    return None


def _find_project_match(context: dict[str, Any], settings: Settings) -> tuple[dict[str, Any] | None, float, list[str], bool]:
    terms = _project_query_terms(context)
    if not terms:
        return None, 0.0, ["Aucun indice chantier exploitable"], False

    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT id, external_project_id, project_code, project_name, metadata_json
            FROM doe_projects
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
    if not rows:
        return None, 0.0, ["Aucun cache chantier local disponible"], False

    ranked: list[tuple[float, dict[str, Any]]] = []
    project_ref = str(context["payload"].get("project_ref") or context["hints"].get("chantier") or "").strip().lower()
    for row in rows:
        candidate = dict(row)
        metadata = json.loads(candidate.get("metadata_json") or "{}")
        haystack = " ".join(
            filter(
                None,
                [
                    candidate.get("project_code"),
                    candidate.get("project_name"),
                    json.dumps(metadata, ensure_ascii=False),
                ],
            )
        ).lower()
        best_term_score = max((fuzz.token_set_ratio(term.lower(), haystack) for term in terms), default=0) / 100
        exact_bonus = 0.25 if project_ref and project_ref in haystack else 0.0
        ranked.append((round(min(best_term_score + exact_bonus, 1.0), 4), candidate))

    ranked.sort(key=lambda item: item[0], reverse=True)
    top_score, top_candidate = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    notes = [
        f"Meilleur match chantier: {top_candidate.get('project_code') or '-'} {top_candidate.get('project_name') or '-'} ({top_score:.2f})",
        f"Deuxième score: {second_score:.2f}",
    ]
    if top_score < settings.routing_match_threshold:
        notes.append("Score chantier insuffisant")
        return None, top_score, notes, False
    if top_score - second_score < settings.routing_match_gap:
        notes.append("Écart entre candidats insuffisant — meilleur candidat pré-sélectionné, à confirmer")
        return top_candidate, top_score, notes, True
    return top_candidate, top_score, notes, False


def _build_final_filename(context: dict[str, Any], proposal: RoutingProposal) -> str:
    payload = context["payload"]
    document = context["document"]
    invoice_date = payload.get("invoice_date") or document.get("invoice_date")
    try:
        date_part = str(invoice_date)[:10] if invoice_date else datetime.utcnow().date().isoformat()
    except Exception:
        date_part = datetime.utcnow().date().isoformat()
    label = slugify(proposal.expense_label or payload.get("supplier_name") or "document")[:80] or "document"
    chantier = slugify(proposal.target_label or payload.get("project_ref") or "sans-chantier")[:40]
    amount = _to_decimal(payload.get("gross_amount"))
    amount_part = f"{amount:.2f}" if amount is not None else "0.00"
    return f"{date_part}_{label}_{chantier}_{amount_part}.pdf"


def hydrate_routing_proposal(
    document_id: int,
    proposal: RoutingProposal,
    settings: Settings | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> RoutingProposal:
    current = settings or get_settings()
    ctx = context or _load_document_context(document_id, current)
    hydrated = proposal.model_copy(deep=True)
    hydrated = _apply_worksite_selection(hydrated, current)
    if not hydrated.target_label:
        hydrated.target_label = ctx["payload"].get("project_ref")
    if not hydrated.expense_label:
        hydrated.expense_label = _build_expense_label(ctx, hydrated)
    if not hydrated.final_filename:
        hydrated.final_filename = _build_final_filename(ctx, hydrated)
    hydrated.standard_path, hydrated.accounting_path, hydrated.worksite_path = _build_storage_paths(current, hydrated)
    return hydrated


def _build_storage_paths(settings: Settings, proposal: RoutingProposal) -> tuple[str, str, str]:
    final_filename = proposal.final_filename or "document.pdf"
    standard_path = settings.classified_standard_dir / final_filename
    accounting_path = settings.classified_accounting_dir / final_filename
    worksite_folder_name = slugify(proposal.target_label or proposal.worksite_external_id or "sans-chantier")
    worksite_path = settings.classified_worksites_dir / worksite_folder_name / final_filename
    return str(standard_path), str(accounting_path), str(worksite_path)


def _find_interfast_target(context: dict[str, Any], document_kind: str, worksite_external_id: str | None, settings: Settings) -> tuple[str | None, str | None]:
    """Check if an existing InterFast expense already matches this document (dedup).

    If no match is found, returns (None, None) — the writer will create a new expense.
    """
    payload = context["payload"]
    supplier_name = (payload.get("supplier_name") or "").lower().strip().lstrip("#").strip()
    gross_amount = payload.get("gross_amount")
    invoice_number = (payload.get("invoice_number") or "").strip()

    if not supplier_name:
        return None, None

    with get_connection(settings) as connection:
        rows = connection.execute(
            "SELECT external_id, payload_json FROM interfast_entities WHERE entity_type = 'expenses' ORDER BY updated_at_remote DESC",
        ).fetchall()

    for row in rows:
        entity = json.loads(row["payload_json"])
        expense_supplier = ((entity.get("supplier") or {}).get("name") or "").lower()
        expense_amount = entity.get("amountTTC")
        expense_name = (entity.get("name") or "").lower()
        if supplier_name not in expense_supplier and expense_supplier not in supplier_name:
            continue
        if invoice_number and invoice_number.lower() in expense_name:
            return "expense", row["external_id"]
        if gross_amount and expense_amount and abs(float(gross_amount) - float(expense_amount)) < 0.02:
            return "expense", row["external_id"]

    return None, None


def build_routing_proposal(document_id: int, settings: Settings | None = None) -> RoutingProposal:
    current = settings or get_settings()
    context = _load_document_context(document_id, current)
    payload = context["payload"]
    document_kind = _normalize_kind(payload, context["hints"])
    supply_type = _normalize_supply_type(payload, context["hints"])
    base_proposal = RoutingProposal(document_kind=document_kind, supply_type=supply_type)

    proposal = RoutingProposal(
        document_kind=document_kind,
        supply_type=supply_type,
        expense_label=_build_expense_label(context, base_proposal),
        interfast_write_mode=current.interfast_write_mode,
        interfast_target_type=context["hints"].get("interfast_type"),
        interfast_target_id=context["hints"].get("interfast_id"),
        manual_hints=context["hints"],
    )
    project_match, routing_score, notes, ambiguous = _find_project_match(context, current)
    proposal.routing_confidence = routing_score
    proposal.matching_notes = notes
    proposal.ambiguous_match = ambiguous
    if project_match:
        proposal.worksite_external_id = str(project_match.get("external_project_id") or "")
        proposal.target_label = " ".join(
            filter(None, [project_match.get("project_code"), project_match.get("project_name")])
        ).strip() or project_match.get("project_name")
        project_metadata = json.loads(project_match.get("metadata_json") or "{}")
        proposal.client_external_id = _extract_nested_value(project_metadata, {"clientId", "client_id", "customerId", "customer_id"})
    if not proposal.interfast_target_type and current.interfast_write_mode != "disabled":
        proposal.interfast_target_type, proposal.interfast_target_id = _find_interfast_target(
            context, document_kind, proposal.worksite_external_id, current,
        )
    return hydrate_routing_proposal(document_id, proposal, current, context=context)


def _notify_telegram(document_id: int, body: str, settings: Settings) -> None:
    recipient = settings.telegram_chat_id or "telegram"
    queue_notification(
        channel="telegram",
        recipient=recipient,
        body=body,
        related_type="document",
        related_id=str(document_id),
        settings=settings,
    )
    try:
        send_telegram_message_if_configured(body, settings=settings)
    except Exception:
        return


def _can_auto_approve(proposal: RoutingProposal, settings: Settings) -> bool:
    return (
        not has_nas_excel_targets(settings)
        and
        proposal.routing_confidence >= settings.routing_auto_approve_threshold
        and bool(proposal.worksite_external_id)
        and not proposal.ambiguous_match
    )


def ensure_routing_task(
    document_id: int,
    *,
    force_refresh: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    proposal = build_routing_proposal(document_id, current)

    if _can_auto_approve(proposal, current):
        return _auto_approve_and_dispatch(document_id, proposal, current)

    created = False
    with get_connection(current) as connection:
        existing = connection.execute(
            """
            SELECT token
            FROM routing_tasks
            WHERE document_id = ? AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
            """,
            (document_id,),
        ).fetchone()
        token = existing["token"] if existing else secrets.token_urlsafe(18)
        if existing:
            if force_refresh:
                connection.execute(
                    """
                    UPDATE routing_tasks
                    SET proposed_payload_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE token = ?
                    """,
                    (proposal.model_dump_json(), token),
                )
        else:
            connection.execute(
                """
                INSERT INTO routing_tasks(document_id, token, proposed_payload_json)
                VALUES (?, ?, ?)
                """,
                (document_id, token, proposal.model_dump_json()),
            )
            created = True
        connection.execute(
            """
            UPDATE documents
            SET document_kind = ?, supply_type = ?, final_filename = ?, routing_confidence = ?,
                worksite_external_id = ?, client_external_id = ?, interfast_target_type = ?, interfast_target_id = ?,
                current_stage = 'needs_routing', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                proposal.document_kind,
                proposal.supply_type,
                proposal.final_filename,
                proposal.routing_confidence,
                proposal.worksite_external_id,
                proposal.client_external_id,
                proposal.interfast_target_type,
                proposal.interfast_target_id,
                document_id,
            ),
        )
        connection.commit()
    if created:
        supplier = _load_document_context(document_id, current)["payload"].get("supplier_name") or "document"
        _notify_telegram(
            document_id,
            f"Réception d'un document {supplier}, validation de routage requise.",
            current,
        )
    return {"document_id": document_id, "routing_token": token, "created": created}


def _auto_approve_and_dispatch(document_id: int, proposal: RoutingProposal, settings: Settings) -> dict[str, Any]:
    token = secrets.token_urlsafe(18)
    sync_status = "pending" if proposal.interfast_write_mode != "disabled" else "disabled"
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO routing_tasks(document_id, token, proposed_payload_json, corrected_payload_json, status, validator_name, routing_notes)
            VALUES (?, ?, ?, ?, 'auto_approved', 'system', 'Auto-approuvé (confiance >= seuil)')
            """,
            (document_id, token, proposal.model_dump_json(), proposal.model_dump_json()),
        )
        connection.execute(
            """
            UPDATE documents
            SET document_kind = ?, supply_type = ?, final_filename = ?, routing_confidence = ?,
                worksite_external_id = ?, client_external_id = ?, interfast_target_type = ?, interfast_target_id = ?,
                interfast_sync_status = ?, current_stage = 'routed', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                proposal.document_kind,
                proposal.supply_type,
                proposal.final_filename,
                proposal.routing_confidence,
                proposal.worksite_external_id,
                proposal.client_external_id,
                proposal.interfast_target_type,
                proposal.interfast_target_id,
                sync_status,
                document_id,
            ),
        )
        connection.commit()

    dispatch_result = None
    if settings.routing_auto_dispatch:
        try:
            dispatch_result = dispatch_document(document_id, settings=settings)
        except Exception:
            pass

    supplier = _load_document_context(document_id, settings)["payload"].get("supplier_name") or "document"
    stage = dispatch_result["stage"] if dispatch_result else "routed"
    _notify_telegram(
        document_id,
        f"Document {supplier} auto-dispatché ({proposal.routing_confidence:.0%} confiance) → {proposal.target_label or 'sans chantier'}. Statut: {stage}.",
        settings,
    )
    return {"document_id": document_id, "routing_token": token, "created": True, "auto_approved": True}


def list_pending_routing_tasks(settings: Settings | None = None) -> list[dict[str, Any]]:
    current = settings or get_settings()
    init_db(current)
    with get_connection(current) as connection:
        rows = connection.execute(
            """
            SELECT rt.token, rt.created_at, d.id AS document_id, d.source_name, d.document_kind, d.supply_type,
                   d.project_ref, d.final_filename, d.routing_confidence
            FROM routing_tasks rt
            JOIN documents d ON d.id = rt.document_id
            WHERE rt.status = 'pending'
            ORDER BY rt.created_at ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_routing_task(token: str, settings: Settings | None = None) -> dict[str, Any] | None:
    current = settings or get_settings()
    init_db(current)
    with get_connection(current) as connection:
        row = connection.execute(
            """
            SELECT rt.id AS task_id, rt.token, rt.status, rt.proposed_payload_json, rt.corrected_payload_json,
                   d.id AS document_id, d.source_name, d.archived_path, d.current_stage, d.validation_status,
                   d.document_kind, d.supply_type, d.project_ref, d.routing_confidence, d.final_filename,
                   d.validated_payload_json, d.normalized_payload_json, d.supplier_name, d.invoice_number,
                   d.invoice_date, d.due_date, d.currency, d.net_amount, d.vat_amount, d.gross_amount
            FROM routing_tasks rt
            JOIN documents d ON d.id = rt.document_id
            WHERE rt.token = ?
            """,
            (token,),
        ).fetchone()
    if not row:
        return None
    document_payload = json.loads(row["validated_payload_json"] or row["normalized_payload_json"] or "{}")
    for field in ("supplier_name", "invoice_number", "invoice_date", "due_date", "currency", "net_amount", "vat_amount", "gross_amount", "project_ref"):
        if row[field] not in (None, ""):
            document_payload[field] = row[field]
    proposed = hydrate_routing_proposal(
        row["document_id"],
        RoutingProposal.model_validate_json(row["proposed_payload_json"]),
        current,
        context={"document": dict(row), "payload": document_payload, "metadata": {}, "hints": {}},
    )
    corrected = (
        hydrate_routing_proposal(
            row["document_id"],
            RoutingProposal.model_validate_json(row["corrected_payload_json"]),
            current,
            context={"document": dict(row), "payload": document_payload, "metadata": {}, "hints": {}},
        )
        if row["corrected_payload_json"]
        else None
    )
    current_payload = corrected.model_dump(mode="json") if corrected else proposed.model_dump(mode="json")
    return {
        "task_id": row["task_id"],
        "token": row["token"],
        "status": row["status"],
        "document_id": row["document_id"],
        "source_name": row["source_name"],
        "archived_path": row["archived_path"],
        "current_stage": row["current_stage"],
        "validation_status": row["validation_status"],
        "document_kind": row["document_kind"],
        "supply_type": row["supply_type"],
        "project_ref": row["project_ref"],
        "routing_confidence": row["routing_confidence"],
        "final_filename": row["final_filename"],
        "document_payload": document_payload,
        "worksite_options": list_worksite_options(current),
        "excel_form_options": get_excel_form_options(),
        "excel_review": build_excel_review_payload(
            row["document_id"],
            settings=current,
            document_payload_override=document_payload,
            routing_payload_override=current_payload,
        ),
        "proposed_payload": proposed.model_dump(mode="json"),
        "corrected_payload": corrected.model_dump(mode="json") if corrected else None,
    }


def update_document_payload_from_routing(document_id: int, payload: dict[str, Any], settings: Settings | None = None) -> None:
    current = settings or get_settings()
    init_db(current)
    with get_connection(current) as connection:
        row = connection.execute(
            """
            SELECT validated_payload_json, normalized_payload_json
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Document not found: {document_id}")
        base_payload = json.loads(row["validated_payload_json"] or row["normalized_payload_json"] or "{}")
        merged_payload = {**base_payload, **payload}
        payload_json = json.dumps(merged_payload, ensure_ascii=False)
        connection.execute(
            """
            UPDATE documents
            SET supplier_name = ?, invoice_number = ?, invoice_date = ?, due_date = ?, currency = ?,
                net_amount = ?, vat_amount = ?, gross_amount = ?, project_ref = ?, normalized_payload_json = ?,
                validated_payload_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                merged_payload.get("supplier_name"),
                merged_payload.get("invoice_number"),
                merged_payload.get("invoice_date"),
                merged_payload.get("due_date"),
                merged_payload.get("currency") or "EUR",
                str(merged_payload.get("net_amount")) if merged_payload.get("net_amount") not in (None, "") else None,
                str(merged_payload.get("vat_amount")) if merged_payload.get("vat_amount") not in (None, "") else None,
                str(merged_payload.get("gross_amount")) if merged_payload.get("gross_amount") not in (None, "") else None,
                merged_payload.get("project_ref"),
                payload_json,
                payload_json,
                document_id,
            ),
        )
        connection.commit()


def apply_routing(token: str, decision: RoutingDecision, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    with get_connection(current) as connection:
        task = connection.execute(
            """
            SELECT id, document_id, proposed_payload_json
            FROM routing_tasks
            WHERE token = ?
            """,
            (token,),
        ).fetchone()
        if not task:
            raise KeyError(f"Routing token not found: {token}")
        proposed = RoutingProposal.model_validate_json(task["proposed_payload_json"])
        corrected = hydrate_routing_proposal(task["document_id"], decision.corrected_data or proposed, current)

        if decision.decision == "approve":
            sync_status = "pending" if corrected.interfast_write_mode != "disabled" else "disabled"
            connection.execute(
                """
                UPDATE documents
                SET document_kind = ?, supply_type = ?, final_filename = ?, routing_confidence = ?,
                    worksite_external_id = ?, client_external_id = ?, interfast_target_type = ?, interfast_target_id = ?,
                    project_ref = ?, interfast_sync_status = ?, current_stage = 'routed', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    corrected.document_kind,
                    corrected.supply_type,
                    corrected.final_filename,
                    corrected.routing_confidence,
                    corrected.worksite_external_id,
                    corrected.client_external_id,
                    corrected.interfast_target_type,
                    corrected.interfast_target_id,
                    corrected.target_label,
                    sync_status,
                    task["document_id"],
                ),
            )
        elif decision.decision == "reject":
            connection.execute(
                """
                UPDATE documents
                SET current_stage = 'routing_rejected', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (task["document_id"],),
            )
        else:
            connection.execute(
                """
                UPDATE documents
                SET project_ref = ?, current_stage = 'needs_routing_fix', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (corrected.target_label, task["document_id"]),
            )

        connection.execute(
            """
            UPDATE routing_tasks
            SET status = ?, corrected_payload_json = ?, validator_name = ?, routing_notes = ?, updated_at = CURRENT_TIMESTAMP
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
    return {
        "document_id": task["document_id"],
        "decision": decision.decision,
        "proposal": corrected.model_dump(mode="json"),
    }


def revert_routing_to_pending(
    token: str,
    corrected_payload: RoutingProposal | None,
    error_text: str,
    settings: Settings | None = None,
) -> None:
    current = settings or get_settings()
    init_db(current)
    with get_connection(current) as connection:
        task = connection.execute(
            """
            SELECT id, document_id
            FROM routing_tasks
            WHERE token = ?
            """,
            (token,),
        ).fetchone()
        if not task:
            raise KeyError(f"Routing token not found: {token}")
        connection.execute(
            """
            UPDATE routing_tasks
            SET status = 'pending', corrected_payload_json = ?, routing_notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                corrected_payload.model_dump_json() if corrected_payload else None,
                error_text,
                task["id"],
            ),
        )
        connection.execute(
            """
            UPDATE documents
            SET current_stage = 'needs_routing_fix', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (task["document_id"],),
        )
        connection.commit()


def _record_dispatch_attempt(
    document_id: int,
    target_system: str,
    *,
    request_payload: dict[str, Any],
    status: str,
    response_payload: dict[str, Any] | None = None,
    external_id: str | None = None,
    retryable: bool = False,
    error_text: str | None = None,
    settings: Settings,
) -> int:
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO dispatch_attempts(document_id, target_system, request_payload_json, response_payload_json, external_id, status, retryable, error_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                target_system,
                json.dumps(request_payload, ensure_ascii=False),
                json.dumps(response_payload or {}, ensure_ascii=False),
                external_id,
                status,
                1 if retryable else 0,
                error_text,
            ),
        )
        connection.commit()
    return int(cursor.lastrowid)


def _copy_if_needed(source_path: Path, target_path: Path) -> dict[str, Any]:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and compute_sha256(target_path) == compute_sha256(source_path):
        return {"path": str(target_path), "copied": False}
    shutil.copy2(source_path, target_path)
    return {"path": str(target_path), "copied": True}


def dispatch_document(document_id: int, settings: Settings | None = None, *, strict_excel: bool = False) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    context = _load_document_context(document_id, current)
    document = context["document"]
    if document.get("validation_status") != "approved":
        raise RuntimeError(f"Document {document_id} is not approved for dispatch")
    if document.get("current_stage") not in {"routed", "dispatch_blocked", "dispatch_failed", "excel_written"}:
        raise RuntimeError(f"Document {document_id} is not routed")
    archived_path = Path(document["archived_path"])
    if not archived_path.exists():
        raise FileNotFoundError(f"Archived file not found: {archived_path}")

    with get_connection(current) as connection:
        routing_row = connection.execute(
            """
            SELECT corrected_payload_json, proposed_payload_json
            FROM routing_tasks
            WHERE document_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (document_id,),
        ).fetchone()
    if routing_row and (routing_row["corrected_payload_json"] or routing_row["proposed_payload_json"]):
        proposal = RoutingProposal.model_validate_json(routing_row["corrected_payload_json"] or routing_row["proposed_payload_json"])
    else:
        proposal = RoutingProposal(
            document_kind=document.get("document_kind") or "unknown",
            supply_type=document.get("supply_type") or "unknown",
            routing_confidence=float(document.get("routing_confidence") or 0),
            client_external_id=document.get("client_external_id"),
            worksite_external_id=document.get("worksite_external_id"),
            interfast_target_type=document.get("interfast_target_type"),
            interfast_target_id=document.get("interfast_target_id"),
            interfast_write_mode=current.interfast_write_mode,
            target_label=document.get("project_ref") or document.get("worksite_external_id"),
        )
    proposal = hydrate_routing_proposal(document_id, proposal, current, context=context)

    excel_result = write_document_bundle(
        document_id,
        settings=current,
        strict=strict_excel and has_nas_excel_targets(current),
    )
    for mapping_result in excel_result["mappings"]:
        _record_dispatch_attempt(
            document_id,
            f"excel-{mapping_result['mapping']}",
            request_payload={"mapping": mapping_result["mapping"]},
            status="success" if mapping_result.get("status") == "success" else "error",
            response_payload=mapping_result,
            retryable=False,
            error_text=mapping_result.get("error"),
            settings=current,
        )

    local_results = {
        "standard": _copy_if_needed(archived_path, Path(proposal.standard_path)),
        "accounting": _copy_if_needed(archived_path, Path(proposal.accounting_path)),
        "worksite": _copy_if_needed(archived_path, Path(proposal.worksite_path)),
    }
    for target_name, result in local_results.items():
        _record_dispatch_attempt(
            document_id,
            f"nas-{target_name}",
            request_payload={"source": str(archived_path), "target": result["path"]},
            status="success",
            response_payload=result,
            settings=current,
        )

    adapter = build_interfast_adapter(current)
    interfast_result = adapter.dispatch(
        {
            "id": document_id,
            "interfast_target_type": proposal.interfast_target_type,
            "interfast_target_id": proposal.interfast_target_id,
            "worksite_external_id": proposal.worksite_external_id,
            "expense_label": proposal.expense_label,
        },
        archived_path,
    )
    _record_dispatch_attempt(
        document_id,
        interfast_result.get("target_system", "interfast"),
        request_payload=interfast_result.get("request_payload", {}),
        status=interfast_result.get("status", "error"),
        response_payload=interfast_result.get("response_payload"),
        external_id=interfast_result.get("external_id"),
        retryable=bool(interfast_result.get("retryable")),
        error_text=interfast_result.get("error_text"),
        settings=current,
    )

    if interfast_result["status"] == "error":
        next_stage = "dispatch_failed"
        interfast_sync_status = "failed"
    elif interfast_result["status"] == "success":
        next_stage = "dispatched"
        interfast_sync_status = "synced"
    elif interfast_result["status"] == "blocked":
        next_stage = "dispatch_blocked"
        interfast_sync_status = "blocked"
    else:
        next_stage = "dispatched"
        interfast_sync_status = interfast_result["status"]

    with get_connection(current) as connection:
        connection.execute(
            """
            UPDATE documents
            SET current_stage = ?, interfast_sync_status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (next_stage, interfast_sync_status, document_id),
        )
        connection.commit()

    _notify_telegram(
        document_id,
        f"Document {proposal.final_filename} dispatch {next_stage}.",
        current,
    )
    return {
        "document_id": document_id,
        "stage": next_stage,
        "local_targets": local_results,
        "excel": excel_result,
        "interfast": interfast_result,
    }
