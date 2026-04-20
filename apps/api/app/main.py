from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from apps.api.app.auth import require_internal_token, require_validation_user
from apps.workers.banking.importer import import_bank_csv
from apps.workers.banking.matching import match_bank_transactions
from apps.workers.common.database import get_connection, init_db
from apps.workers.common.schemas import BankImportRequest, ExportRequest, IngestRequest, InterfastSyncRequest, OcrRunResponse, OCRNormalized, RoutingDecision, RoutingProposal, ValidationDecision
from apps.workers.common.settings import Settings, ensure_runtime_directories, get_settings
from apps.workers.documents.ingest import ingest_document
from apps.workers.documents.ocr_service import run_document_ocr
from apps.workers.documents.excel import write_document_to_excel
from apps.workers.documents.validation import apply_validation, get_document_file_path, get_validation_task, list_pending_validation_tasks
from apps.workers.doe.service import rebuild_project_tree
from apps.workers.exports.inexweb import export_inexweb
from apps.workers.exports.weekly_accounting import send_weekly_accounting_email
from apps.workers.sync.interfast import sync_interfast
from apps.workers.accounting.entries import generate_entries_for_document
from apps.workers.routing.service import (
    apply_routing,
    dispatch_document,
    ensure_routing_task,
    get_routing_task,
    hydrate_routing_proposal,
    list_pending_routing_tasks,
    revert_routing_to_pending,
    update_document_payload_from_routing,
)


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        settings = get_settings()
    except Exception as exc:
        raise RuntimeError(f"Invalid application settings: {exc}") from exc
    ensure_runtime_directories(settings)
    init_db(settings)
    yield


app = FastAPI(title="Automatisations Platform", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _template_base_path(settings: Settings) -> str:
    parsed = urlparse((settings.public_base_url or "").strip())
    path = (parsed.path or "").rstrip("/")
    if not path:
        return ""
    return path if path.startswith("/") else f"/{path}"


def _document_file_media_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _document_preview_kind(media_type: str) -> str:
    if media_type == "application/pdf":
        return "pdf"
    if media_type.startswith("image/"):
        return "image"
    if media_type.startswith("text/"):
        return "text"
    return "unsupported"


def _build_document_preview(document_id: int, settings: Settings) -> dict[str, str]:
    base_path = _template_base_path(settings)
    path = get_document_file_path(document_id, settings)
    media_type = _document_file_media_type(path)
    return {
        "preview_url": f"{base_path}/files/{document_id}/preview" if base_path else f"/files/{document_id}/preview",
        "download_url": f"{base_path}/files/{document_id}" if base_path else f"/files/{document_id}",
        "filename": path.name,
        "mime_type": media_type,
        "preview_kind": _document_preview_kind(media_type),
    }


def _task_template_context(task: dict, settings: Settings, **extra: object) -> dict[str, object]:
    context: dict[str, object] = {
        "task": task,
        "preview": _build_document_preview(task["document_id"], settings),
        "base_path": _template_base_path(settings),
    }
    context.update(extra)
    return context


def _build_corrected_payload(
    extracted_payload: dict,
    document_type: str,
    document_kind: str | None,
    supply_type: str | None,
    supplier_name: str | None,
    supplier_siret: str | None,
    invoice_number: str | None,
    invoice_date: str | None,
    due_date: str | None,
    currency: str,
    net_amount: str | None,
    vat_amount: str | None,
    gross_amount: str | None,
    project_ref: str | None,
) -> OCRNormalized:
    payload = dict(extracted_payload)
    payload.update(
        {
            "document_type": document_type,
            "document_kind": document_kind or payload.get("document_kind"),
            "supply_type": supply_type or payload.get("supply_type"),
            "supplier_name": supplier_name,
            "supplier_siret": supplier_siret,
            "invoice_number": invoice_number,
            "invoice_date": invoice_date or None,
            "due_date": due_date or None,
            "currency": currency,
            "net_amount": Decimal(net_amount) if net_amount else None,
            "vat_amount": Decimal(vat_amount) if vat_amount else None,
            "gross_amount": Decimal(gross_amount) if gross_amount else None,
            "project_ref": project_ref or None,
        }
    )
    return OCRNormalized.model_validate(payload)


def _build_routing_payload(
    proposed_payload: dict,
    document_kind: str | None,
    supply_type: str | None,
    expense_label: str | None,
    final_filename: str | None,
    routing_confidence: str | None,
    client_external_id: str | None,
    worksite_external_id: str | None,
    interfast_target_type: str | None,
    interfast_target_id: str | None,
    target_label: str | None,
    standard_path: str | None,
    accounting_path: str | None,
    worksite_path: str | None,
    operation_type: str | None,
    ledger_label: str | None,
    ledger_sub_label: str | None,
    payment_method: str | None,
    payment_status: str | None,
    vat_bucket: str | None,
    treasury_workbook_path: str | None,
    client_ledger_path: str | None,
    supplier_ledger_path: str | None,
    force_type: str | None,
    received_transfer_amount: str | None,
) -> RoutingProposal:
    payload = dict(proposed_payload)
    payload.update(
        {
            "document_kind": document_kind or payload.get("document_kind") or "unknown",
            "supply_type": supply_type or payload.get("supply_type") or "unknown",
            "expense_label": expense_label or payload.get("expense_label"),
            "final_filename": final_filename or payload.get("final_filename"),
            "routing_confidence": float(routing_confidence) if routing_confidence else float(payload.get("routing_confidence") or 0),
            "client_external_id": client_external_id or payload.get("client_external_id"),
            "worksite_external_id": worksite_external_id or payload.get("worksite_external_id"),
            "interfast_target_type": interfast_target_type or payload.get("interfast_target_type"),
            "interfast_target_id": interfast_target_id or payload.get("interfast_target_id"),
            "target_label": target_label or payload.get("target_label"),
            "standard_path": standard_path or payload.get("standard_path"),
            "accounting_path": accounting_path or payload.get("accounting_path"),
            "worksite_path": worksite_path or payload.get("worksite_path"),
            "operation_type": operation_type or payload.get("operation_type"),
            "ledger_label": ledger_label or payload.get("ledger_label"),
            "ledger_sub_label": ledger_sub_label or payload.get("ledger_sub_label"),
            "payment_method": payment_method or payload.get("payment_method"),
            "payment_status": payment_status or payload.get("payment_status"),
            "vat_bucket": vat_bucket or payload.get("vat_bucket"),
            "treasury_workbook_path": treasury_workbook_path or payload.get("treasury_workbook_path"),
            "client_ledger_path": client_ledger_path or payload.get("client_ledger_path"),
            "supplier_ledger_path": supplier_ledger_path or payload.get("supplier_ledger_path"),
            "force_type": force_type or payload.get("force_type"),
            "received_transfer_amount": received_transfer_amount or payload.get("received_transfer_amount"),
        }
    )
    return RoutingProposal.model_validate(payload)


def _build_routing_document_payload(
    document_payload: dict,
    supplier_name: str | None,
    invoice_number: str | None,
    invoice_date: str | None,
    due_date: str | None,
    currency: str | None,
    net_amount: str | None,
    vat_amount: str | None,
    gross_amount: str | None,
    project_ref: str | None,
) -> OCRNormalized:
    payload = dict(document_payload)
    payload.update(
        {
            "supplier_name": supplier_name or payload.get("supplier_name"),
            "invoice_number": invoice_number or payload.get("invoice_number"),
            "invoice_date": invoice_date or payload.get("invoice_date"),
            "due_date": due_date or payload.get("due_date"),
            "currency": currency or payload.get("currency") or "EUR",
            "net_amount": Decimal(net_amount) if net_amount else payload.get("net_amount"),
            "vat_amount": Decimal(vat_amount) if vat_amount else payload.get("vat_amount"),
            "gross_amount": Decimal(gross_amount) if gross_amount else payload.get("gross_amount"),
            "project_ref": project_ref if project_ref is not None else payload.get("project_ref"),
        }
    )
    return OCRNormalized.model_validate(payload)


@app.get("/", include_in_schema=False)
async def root(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    base_path = _template_base_path(settings)
    return RedirectResponse(url=f"{base_path}/dashboard" if base_path else "/dashboard")


@app.get("/healthz")
async def healthz(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    _: str = Depends(require_validation_user),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    with get_connection(settings) as connection:
        metrics = {
            "documents_total": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "documents_pending_validation": connection.execute(
                "SELECT COUNT(*) FROM documents WHERE validation_status = 'pending'"
            ).fetchone()[0],
            "documents_pending_routing": connection.execute(
                "SELECT COUNT(*) FROM routing_tasks WHERE status = 'pending'"
            ).fetchone()[0],
            "documents_rejected": connection.execute(
                "SELECT COUNT(*) FROM documents WHERE validation_status = 'rejected'"
            ).fetchone()[0],
            "exports_pending": connection.execute(
                "SELECT COUNT(*) FROM accounting_entries WHERE export_status = 'pending'"
            ).fetchone()[0],
            "bank_anomalies": connection.execute(
                "SELECT COUNT(*) FROM bank_transactions WHERE status IN ('probable_match', 'no_match')"
            ).fetchone()[0],
            "doe_incomplete": connection.execute(
                "SELECT COUNT(*) FROM doe_projects WHERE completeness_status = 'incomplete'"
            ).fetchone()[0],
        }
        recent_documents = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, source_name, supplier_name, gross_amount, document_kind, supply_type,
                       project_ref, worksite_external_id, current_stage, routing_confidence, updated_at
                FROM documents
                WHERE current_stage IN ('dispatched', 'dispatch_blocked', 'dispatch_failed', 'routed')
                ORDER BY updated_at DESC
                LIMIT 10
                """
            ).fetchall()
        ]
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "metrics": metrics,
            "pending_tasks": list_pending_validation_tasks(settings),
            "pending_routing_tasks": list_pending_routing_tasks(settings),
            "recent_documents": recent_documents,
            "refresh_seconds": settings.dashboard_refresh_seconds,
            "base_path": _template_base_path(settings),
        },
    )


@app.get("/files/{document_id}")
async def file_download(
    document_id: int,
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    path = get_document_file_path(document_id, settings)
    return FileResponse(
        path,
        media_type=_document_file_media_type(path),
        filename=path.name,
        content_disposition_type="attachment",
    )


@app.get("/files/{document_id}/preview")
async def file_preview(
    document_id: int,
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    path = get_document_file_path(document_id, settings)
    return FileResponse(
        path,
        media_type=_document_file_media_type(path),
        filename=path.name,
        content_disposition_type="inline",
    )


@app.get("/review/{batch_token}", response_class=HTMLResponse)
async def review_batch(
    batch_token: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    with get_connection(settings) as connection:
        documents = [
            dict(row)
            for row in connection.execute(
                """
                SELECT d.id, d.source_name, d.supplier_name, d.invoice_number, d.invoice_date,
                       d.gross_amount, d.project_ref, d.document_kind, d.current_stage,
                       d.validation_status, d.confidence, d.routing_confidence,
                       d.interfast_target_type, d.interfast_target_id,
                       vt.token AS validation_token, vt.status AS validation_task_status,
                       rt.token AS routing_token, rt.status AS routing_task_status
                FROM documents d
                LEFT JOIN validation_tasks vt ON vt.document_id = d.id AND vt.status = 'pending'
                LEFT JOIN routing_tasks rt ON rt.document_id = d.id AND rt.status = 'pending'
                WHERE d.batch_token = ?
                ORDER BY d.id
                """,
                (batch_token,),
            ).fetchall()
        ]
    if not documents:
        raise HTTPException(status_code=404, detail="Aucun document trouvé pour ce lien")
    interfast_base = (settings.interfast_base_url or "https://app.inter-fast.fr").rstrip("/")
    interfast_ui_paths = {"bill": "dashboard/billing/bills", "quotation": "dashboard/billing/quotations", "credit": "dashboard/billing/credits", "expense": "dashboard/expenses?expenses=%7B%22pageIndex%22%3A0%7D"}
    for doc in documents:
        target_type = doc.get("interfast_target_type") or ""
        ui_path = interfast_ui_paths.get(target_type)
        target_id = doc.get("interfast_target_id")
        if ui_path and target_id:
            doc["interfast_link"] = f"{interfast_base}/{ui_path}" if target_type == "expense" else f"{interfast_base}/{ui_path}/{target_id}"
        else:
            doc["interfast_link"] = None
    return templates.TemplateResponse(
        request=request,
        name="review_batch.html",
        context={
            "documents": documents,
            "batch_token": batch_token,
            "base_path": _template_base_path(settings),
        },
    )


@app.get("/validate/{token}", response_class=HTMLResponse)
async def validation_page(
    token: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    task = get_validation_task(token, settings)
    if not task:
        raise HTTPException(status_code=404, detail="Validation task not found")
    return templates.TemplateResponse(
        request=request,
        name="validation_detail.html",
        context=_task_template_context(task, settings),
    )


@app.post("/validate/{token}", response_class=HTMLResponse)
async def submit_validation(
    token: str,
    request: Request,
    document_type: str = Form(default="purchase_invoice"),
    document_kind: str | None = Form(default=None),
    decision: str = Form(...),
    validator_name: str = Form(default=""),
    notes: str = Form(default=""),
    supply_type: str | None = Form(default=None),
    supplier_name: str | None = Form(default=None),
    supplier_siret: str | None = Form(default=None),
    invoice_number: str | None = Form(default=None),
    invoice_date: str | None = Form(default=None),
    due_date: str | None = Form(default=None),
    currency: str = Form(default="EUR"),
    net_amount: str | None = Form(default=None),
    vat_amount: str | None = Form(default=None),
    gross_amount: str | None = Form(default=None),
    project_ref: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    task = get_validation_task(token, settings)
    if not task:
        raise HTTPException(status_code=404, detail="Validation task not found")
    corrected = None
    if decision != "reject":
        corrected = _build_corrected_payload(
            task["corrected_payload"] or task["extracted_payload"],
            document_type,
            document_kind,
            supply_type,
            supplier_name,
            supplier_siret,
            invoice_number,
            invoice_date,
            due_date,
            currency,
            net_amount,
            vat_amount,
            gross_amount,
            project_ref,
        )
    result = apply_validation(
        token,
        ValidationDecision(
            decision=decision,
            validator_name=validator_name or "mail-user",
            notes=notes or None,
            corrected_data=corrected,
        ),
        settings,
    )
    task = get_validation_task(token, settings)
    return templates.TemplateResponse(
        request=request,
        name="validation_detail.html",
        context=_task_template_context(task, settings, result=result),
    )


@app.get("/route/{token}", response_class=HTMLResponse)
async def routing_page(
    token: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    task = get_routing_task(token, settings)
    if not task:
        raise HTTPException(status_code=404, detail="Routing task not found")
    return templates.TemplateResponse(
        request=request,
        name="routing_detail.html",
        context=_task_template_context(task, settings),
    )


@app.post("/route/{token}", response_class=HTMLResponse)
async def submit_routing(
    token: str,
    request: Request,
    decision: str = Form(...),
    validator_name: str = Form(default=""),
    notes: str = Form(default=""),
    document_kind: str | None = Form(default=None),
    supply_type: str | None = Form(default=None),
    expense_label: str | None = Form(default=None),
    final_filename: str | None = Form(default=None),
    routing_confidence: str | None = Form(default=None),
    supplier_name: str | None = Form(default=None),
    invoice_number: str | None = Form(default=None),
    invoice_date: str | None = Form(default=None),
    due_date: str | None = Form(default=None),
    currency: str | None = Form(default=None),
    net_amount: str | None = Form(default=None),
    vat_amount: str | None = Form(default=None),
    gross_amount: str | None = Form(default=None),
    client_external_id: str | None = Form(default=None),
    worksite_external_id: str | None = Form(default=None),
    interfast_target_type: str | None = Form(default=None),
    interfast_target_id: str | None = Form(default=None),
    target_label: str | None = Form(default=None),
    standard_path: str | None = Form(default=None),
    accounting_path: str | None = Form(default=None),
    worksite_path: str | None = Form(default=None),
    operation_type: str | None = Form(default=None),
    ledger_label: str | None = Form(default=None),
    ledger_sub_label: str | None = Form(default=None),
    payment_method: str | None = Form(default=None),
    payment_status: str | None = Form(default=None),
    vat_bucket: str | None = Form(default=None),
    treasury_workbook_path: str | None = Form(default=None),
    client_ledger_path_selected: str | None = Form(default=None),
    client_ledger_path_manual: str | None = Form(default=None),
    supplier_ledger_path_selected: str | None = Form(default=None),
    supplier_ledger_path_manual: str | None = Form(default=None),
    force_type: str | None = Form(default=None),
    received_transfer_amount: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    task = get_routing_task(token, settings)
    if not task:
        raise HTTPException(status_code=404, detail="Routing task not found")
    corrected = None
    corrected_document = None
    dispatch_result = None
    dispatch_error = None
    if decision != "reject":
        corrected_document = _build_routing_document_payload(
            task["document_payload"],
            supplier_name,
            invoice_number,
            invoice_date,
            due_date,
            currency,
            net_amount,
            vat_amount,
            gross_amount,
            task["document_payload"].get("project_ref"),
        )
        corrected = _build_routing_payload(
            task["corrected_payload"] or task["proposed_payload"],
            document_kind,
            supply_type,
            expense_label,
            final_filename,
            routing_confidence,
            client_external_id,
            worksite_external_id,
            interfast_target_type,
            interfast_target_id,
            target_label,
            standard_path,
            accounting_path,
            worksite_path,
            operation_type,
            ledger_label,
            ledger_sub_label,
            payment_method,
            payment_status,
            vat_bucket,
            treasury_workbook_path,
            (client_ledger_path_manual or "").strip() or (client_ledger_path_selected or "").strip() or None,
            (supplier_ledger_path_manual or "").strip() or (supplier_ledger_path_selected or "").strip() or None,
            force_type,
            received_transfer_amount,
        )
        corrected = hydrate_routing_proposal(
            task["document_id"],
            corrected,
            settings,
            context={
                "document": {
                    "id": task["document_id"],
                    "invoice_date": corrected_document.invoice_date.isoformat() if corrected_document.invoice_date else None,
                },
                "payload": {
                    **task["document_payload"],
                    "supplier_name": corrected_document.supplier_name,
                    "invoice_number": corrected_document.invoice_number,
                    "invoice_date": corrected_document.invoice_date.isoformat() if corrected_document.invoice_date else None,
                    "due_date": corrected_document.due_date.isoformat() if corrected_document.due_date else None,
                    "currency": corrected_document.currency,
                    "net_amount": str(corrected_document.net_amount) if corrected_document.net_amount is not None else None,
                    "vat_amount": str(corrected_document.vat_amount) if corrected_document.vat_amount is not None else None,
                    "gross_amount": str(corrected_document.gross_amount) if corrected_document.gross_amount is not None else None,
                    "project_ref": corrected.target_label or task["document_payload"].get("project_ref"),
                },
                "metadata": {},
                "hints": {},
            },
        )
    result = apply_routing(
        token,
        RoutingDecision(
            decision=decision,
            validator_name=validator_name or "mail-user",
            notes=notes or None,
            corrected_data=corrected,
        ),
        settings,
    )
    if corrected_document:
        project_ref = corrected.target_label if corrected else task["document_payload"].get("project_ref")
        corrected_document_payload = corrected_document.model_dump(mode="json") | {"project_ref": project_ref}
        update_document_payload_from_routing(
            task["document_id"],
            corrected_document_payload,
            settings,
        )
    else:
        corrected_document_payload = None
    if decision == "approve" and settings.routing_auto_dispatch:
        try:
            dispatch_result = dispatch_document(
                task["document_id"],
                settings=settings,
                strict_excel=True,
                document_payload_override=corrected_document_payload,
                routing_payload_override=corrected.model_dump(mode="json") if corrected else None,
            )
        except Exception as exc:
            dispatch_error = str(exc)
            revert_routing_to_pending(
                token,
                corrected,
                f"Validation Excel bloquée: {dispatch_error}",
                settings=settings,
            )
    task = get_routing_task(token, settings)
    return templates.TemplateResponse(
        request=request,
        name="routing_detail.html",
        context=_task_template_context(
            task,
            settings,
            result=result,
            dispatch_result=dispatch_result,
            dispatch_error=dispatch_error,
        ),
    )


@app.post("/internal/documents/ingest")
async def ingest_document_endpoint(
    payload: IngestRequest,
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    return ingest_document(
        payload.source_path,
        payload.source_kind,
        source_name=payload.source_name,
        metadata=payload.metadata,
        settings=settings,
    )


@app.post("/internal/documents/{document_id}/ocr", response_model=OcrRunResponse)
async def document_ocr_endpoint(
    document_id: int,
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    return run_document_ocr(document_id, settings)


@app.post("/internal/documents/{document_id}/route")
async def route_document_endpoint(
    document_id: int,
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    return ensure_routing_task(document_id, force_refresh=True, settings=settings)


@app.post("/internal/documents/{document_id}/dispatch")
async def dispatch_document_endpoint(
    document_id: int,
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    return dispatch_document(document_id, settings=settings)


@app.post("/internal/bank/import")
async def bank_import_endpoint(
    payload: BankImportRequest,
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    result = import_bank_csv(payload.csv_path, payload.source_label, settings=settings)
    result["matching"] = match_bank_transactions(settings=settings)
    return result


@app.post("/internal/documents/{document_id}/excel")
async def write_excel_endpoint(
    document_id: int,
    mapping: str = "purchases",
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    return write_document_to_excel(document_id, mapping, settings=settings)


@app.post("/internal/documents/{document_id}/entries")
async def generate_entries_endpoint(
    document_id: int,
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    return generate_entries_for_document(document_id, settings=settings)


@app.post("/internal/interfast/sync")
async def interfast_sync_endpoint(
    payload: InterfastSyncRequest,
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    return sync_interfast(force_full=payload.force_full, entity_types=payload.entity_types, settings=settings)


@app.post("/internal/exports/inexweb")
async def export_inexweb_endpoint(
    payload: ExportRequest,
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    return export_inexweb(payload.output_path, settings=settings)


@app.post("/internal/doe/rebuild/{project_id}")
async def rebuild_doe_endpoint(
    project_id: int,
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    return rebuild_project_tree(project_id, settings=settings)


@app.post("/internal/weekly-accounting")
async def weekly_accounting_endpoint(
    _: None = Depends(require_internal_token),
    settings: Settings = Depends(get_settings),
) -> dict:
    return send_weekly_accounting_email(settings=settings)
