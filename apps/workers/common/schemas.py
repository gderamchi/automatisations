from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class OCRLineItem(BaseModel):
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    total: Decimal | None = None


class OCRNormalized(BaseModel):
    document_type: Literal["purchase_invoice", "sales_invoice", "credit_note", "unknown"] = "purchase_invoice"
    supplier_name: str | None = None
    supplier_siret: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str = "EUR"
    net_amount: Decimal | None = None
    vat_amount: Decimal | None = None
    gross_amount: Decimal | None = None
    line_items: list[OCRLineItem] = Field(default_factory=list)
    project_ref: str | None = None
    confidence: float = 0.0
    source_file_id: int | None = None
    raw_text: str | None = None
    missing_fields: list[str] = Field(default_factory=list)


class ValidationDecision(BaseModel):
    decision: Literal["approve", "reject", "request-fix"]
    validator_name: str
    notes: str | None = None
    corrected_data: OCRNormalized | None = None


class IngestRequest(BaseModel):
    source_path: str
    source_kind: Literal["email", "manual", "sync"] = "manual"
    source_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OcrRunResponse(BaseModel):
    document_id: int
    confidence: float
    status: str
    validation_required: bool
    validation_token: str | None = None


class BankImportRequest(BaseModel):
    csv_path: str
    source_label: str | None = None


class InterfastSyncRequest(BaseModel):
    force_full: bool = False
    entity_types: list[str] | None = None


class ExportRequest(BaseModel):
    output_path: str | None = None


class AccountingRule(BaseModel):
    rule_key: str
    supplier_match: str
    compte_charge: str
    compte_tva: str
    compte_tiers: str
    journal: str
    confidence_threshold: float = 0.75
    metadata: dict[str, Any] = Field(default_factory=dict)


class AccountingLine(BaseModel):
    side: Literal["debit", "credit"]
    account_code: str
    amount: Decimal
    label: str


class AccountingTemplate(BaseModel):
    template_id: str
    journal: str
    document_type: str
    lines: list[dict[str, Any]]


class BankTransactionInput(BaseModel):
    booking_date: date
    value_date: date | None = None
    label: str
    amount: Decimal
    reference: str | None = None
    currency: str = "EUR"
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class BankMatchResult(BaseModel):
    document_id: int | None = None
    score: float
    outcome: Literal["certain_match", "probable_match", "no_match"]
    rationale: dict[str, Any] = Field(default_factory=dict)
