from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request

from apps.api.app.auth import require_internal_token, require_validation_user
from apps.workers.banking.importer import import_bank_csv
from apps.workers.banking.matching import match_bank_transactions
from apps.workers.common.database import get_connection, init_db
from apps.workers.common.schemas import BankImportRequest, ExportRequest, IngestRequest, InterfastSyncRequest, OcrRunResponse, OCRNormalized, ValidationDecision
from apps.workers.common.settings import Settings, ensure_runtime_directories, get_settings
from apps.workers.documents.ingest import ingest_document
from apps.workers.documents.ocr_service import run_document_ocr
from apps.workers.documents.excel import write_document_to_excel
from apps.workers.documents.validation import apply_validation, get_document_file_path, get_validation_task, list_pending_validation_tasks
from apps.workers.doe.service import rebuild_project_tree
from apps.workers.exports.inexweb import export_inexweb
from apps.workers.sync.interfast import sync_interfast
from apps.workers.accounting.entries import generate_entries_for_document


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    ensure_runtime_directories(settings)
    init_db(settings)
    yield


app = FastAPI(title="Automatisations Platform", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _build_corrected_payload(
    extracted_payload: dict,
    document_type: str,
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


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


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
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "metrics": metrics,
            "pending_tasks": list_pending_validation_tasks(settings),
            "refresh_seconds": settings.dashboard_refresh_seconds,
        },
    )


@app.get("/files/{document_id}")
async def file_preview(
    document_id: int,
    _: str = Depends(require_validation_user),
) -> FileResponse:
    path = get_document_file_path(document_id)
    return FileResponse(path)


@app.get("/validate/{token}", response_class=HTMLResponse)
async def validation_page(
    token: str,
    request: Request,
    _: str = Depends(require_validation_user),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    task = get_validation_task(token, settings)
    if not task:
        raise HTTPException(status_code=404, detail="Validation task not found")
    return templates.TemplateResponse(request=request, name="validation_detail.html", context={"task": task})


@app.post("/validate/{token}", response_class=HTMLResponse)
async def submit_validation(
    token: str,
    request: Request,
    auth_username: str = Depends(require_validation_user),
    document_type: str = Form(default="purchase_invoice"),
    decision: str = Form(...),
    validator_name: str = Form(default=""),
    notes: str = Form(default=""),
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
            validator_name=validator_name or auth_username,
            notes=notes or None,
            corrected_data=corrected,
        ),
        settings,
    )
    task = get_validation_task(token, settings)
    return templates.TemplateResponse(
        request=request,
        name="validation_detail.html",
        context={"task": task, "result": result},
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
