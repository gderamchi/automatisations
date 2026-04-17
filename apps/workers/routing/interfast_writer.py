from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from apps.workers.common.database import get_connection
from apps.workers.common.settings import Settings, get_settings


class InterfastWriteAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def dispatch(self, document: dict[str, Any], file_path: Path) -> dict[str, Any]:
        raise NotImplementedError


class DisabledInterfastAdapter(InterfastWriteAdapter):
    def dispatch(self, document: dict[str, Any], file_path: Path) -> dict[str, Any]:
        return {
            "status": "disabled",
            "target_system": "interfast",
            "request_payload": {
                "document_id": document["id"],
                "file_path": str(file_path),
            },
            "response_payload": {"mode": "disabled"},
        }


class ExpenseCreateAdapter(InterfastWriteAdapter):
    """Creates a new expense in InterFast and uploads the document as attachment."""

    FILE_ENDPOINTS = {
        "quotation": "/v1/billing/quotations/{target_id}/files",
        "bill": "/v1/billing/bills/{target_id}/files",
        "credit": "/v1/billing/credits/{target_id}/files",
        "amendment": "/v1/billing/amendments/{target_id}/files",
        "intervention": "/v1/interventions/{target_id}/report/files",
        "expense": "/v1/expenses/{target_id}/files",
    }

    def dispatch(self, document: dict[str, Any], file_path: Path) -> dict[str, Any]:
        if not self.settings.interfast_api_key:
            return {
                "status": "blocked",
                "target_system": "interfast-expense",
                "request_payload": {"document_id": document["id"]},
                "response_payload": {"reason": "missing-api-key"},
                "retryable": False,
            }

        target_type = str(document.get("interfast_target_type") or "").strip().lower()
        target_id = str(document.get("interfast_target_id") or "").strip()

        # If we already have a target (matched existing expense), just upload file
        if target_type and target_id:
            return self._upload_to_existing(document, file_path, target_type, target_id)

        # Otherwise create a new expense and upload the file
        return self._create_expense_and_upload(document, file_path)

    def _create_expense_and_upload(self, document: dict[str, Any], file_path: Path) -> dict[str, Any]:
        base_url = (self.settings.interfast_base_url or "https://app.inter-fast.fr").rstrip("/")
        headers = {"X-API-KEY": self.settings.interfast_api_key}

        # Build expense payload from document data
        doc_payload = self._load_document_payload(document["id"])
        supplier_interfast_id = self._find_supplier_id(doc_payload.get("supplier_name"))
        expense_body = {
            "name": self._build_expense_name(doc_payload, document.get("expense_label")),
            "amountTTC": float(doc_payload["gross_amount"]) if doc_payload.get("gross_amount") else 0,
            "amountHT": float(doc_payload["net_amount"]) if doc_payload.get("net_amount") else 0,
            "issuedAt": f"{doc_payload['invoice_date']}T00:00:00.000Z" if doc_payload.get("invoice_date") else None,
        }
        if supplier_interfast_id:
            expense_body["supplier"] = supplier_interfast_id
        expense_body = {k: v for k, v in expense_body.items() if v is not None}

        with httpx.Client(timeout=self.settings.interfast_timeout_seconds) as client:
            # Step 1: Create expense
            create_resp = client.post(
                f"{base_url}/v1/expenses/create",
                headers={**headers, "Content-Type": "application/json"},
                json=expense_body,
            )
            if not create_resp.is_success:
                return {
                    "status": "error",
                    "target_system": "interfast-expense-create",
                    "request_payload": expense_body,
                    "response_payload": _safe_json(create_resp),
                    "retryable": create_resp.status_code >= 500,
                    "error_text": create_resp.text,
                }

            created = create_resp.json()
            expense_id = created["id"]

            # Step 2: Upload file
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            with file_path.open("rb") as handle:
                upload_resp = client.post(
                    f"{base_url}/v1/expenses/{expense_id}/files",
                    headers=headers,
                    files={"file": (file_path.name, handle, content_type)},
                )

        # Update document with the new expense ID for future reference
        self._update_document_target(document["id"], "expense", expense_id)

        upload_ok = upload_resp.is_success
        return {
            "status": "success" if upload_ok else "error",
            "target_system": "interfast-expense-create",
            "request_payload": {
                **expense_body,
                "expense_id": expense_id,
                "filename": file_path.name,
            },
            "response_payload": {
                "expense": created,
                "file_upload": _safe_json(upload_resp),
            },
            "external_id": expense_id,
            "retryable": False,
            "error_text": None if upload_ok else upload_resp.text,
        }

    def _upload_to_existing(self, document: dict[str, Any], file_path: Path, target_type: str, target_id: str) -> dict[str, Any]:
        base_url = (self.settings.interfast_base_url or "https://app.inter-fast.fr").rstrip("/")
        endpoint_template = self.FILE_ENDPOINTS.get(target_type)
        if not endpoint_template:
            return {
                "status": "blocked",
                "target_system": "interfast-attachment",
                "request_payload": {"target_type": target_type, "target_id": target_id},
                "response_payload": {"reason": "unsupported-target-type"},
                "retryable": False,
            }

        url = urljoin(f"{base_url}/", endpoint_template.format(target_id=target_id).lstrip("/"))
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        with file_path.open("rb") as handle, httpx.Client(timeout=self.settings.interfast_timeout_seconds) as client:
            response = client.post(
                url,
                headers={"X-API-KEY": self.settings.interfast_api_key},
                files={"file": (file_path.name, handle, content_type)},
            )

        status = "success" if response.is_success else "error"
        return {
            "status": status,
            "target_system": "interfast-attachment",
            "request_payload": {"target_type": target_type, "target_id": target_id, "url": url, "filename": file_path.name},
            "response_payload": _safe_json(response),
            "external_id": target_id,
            "retryable": response.status_code >= 500,
            "error_text": None if response.is_success else response.text,
        }

    def _build_expense_name(self, payload: dict[str, Any], explicit_label: str | None = None) -> str:
        if explicit_label:
            return explicit_label
        supplier = (payload.get("supplier_name") or "Fournisseur").lstrip("#").strip()
        parts = [
            supplier,
            payload.get("invoice_number") or "",
        ]
        project = payload.get("project_ref")
        if project:
            parts.append(project)
        return " - ".join(p for p in parts if p)

    def _find_supplier_id(self, supplier_name: str | None) -> int | None:
        if not supplier_name:
            return None
        with get_connection(self.settings) as conn:
            rows = conn.execute(
                "SELECT payload_json FROM interfast_entities WHERE entity_type IN ('quotes', 'client_invoices', 'expenses')"
            ).fetchall()
        needle = supplier_name.lower().strip().lstrip("#").strip()
        for row in rows:
            entity = json.loads(row["payload_json"])
            client = entity.get("client") or entity.get("supplier") or {}
            name = (client.get("name") or "").lower()
            if needle and needle in name or name and name in needle:
                return client.get("id")
        return None

    def _load_document_payload(self, document_id: int) -> dict[str, Any]:
        with get_connection(self.settings) as conn:
            row = conn.execute(
                "SELECT validated_payload_json, normalized_payload_json, supplier_name, invoice_number, invoice_date, net_amount, gross_amount, project_ref FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        if not row:
            return {}
        payload = json.loads(row["validated_payload_json"] or row["normalized_payload_json"] or "{}")
        # Override with document-level fields (may have been corrected during routing)
        for field in ("supplier_name", "invoice_number", "invoice_date", "net_amount", "gross_amount", "project_ref"):
            if row[field] is not None:
                payload[field] = row[field]
        return payload

    def _update_document_target(self, document_id: int, target_type: str, target_id: str) -> None:
        with get_connection(self.settings) as conn:
            conn.execute(
                "UPDATE documents SET interfast_target_type = ?, interfast_target_id = ? WHERE id = ?",
                (target_type, target_id, document_id),
            )
            conn.commit()


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"text": response.text}


def build_interfast_adapter(settings: Settings | None = None) -> InterfastWriteAdapter:
    current = settings or get_settings()
    mode = current.interfast_write_mode.strip().lower()
    if mode in ("expense", "attachment"):
        return ExpenseCreateAdapter(current)
    return DisabledInterfastAdapter(current)
