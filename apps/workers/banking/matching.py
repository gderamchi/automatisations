from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from rapidfuzz import fuzz

from apps.workers.common.database import get_connection, init_db
from apps.workers.common.settings import Settings, get_settings


def _date_score(booking_date: str, invoice_date: str | None) -> float:
    if not invoice_date:
        return 0.0
    delta = abs((date.fromisoformat(booking_date) - date.fromisoformat(invoice_date)).days)
    if delta == 0:
        return 1.0
    if delta <= 3:
        return 0.8
    if delta <= 7:
        return 0.4
    return 0.0


def _amount_score(tx_amount: Decimal, doc_amount: Decimal | None) -> float:
    if doc_amount is None:
        return 0.0
    difference = abs(tx_amount - doc_amount)
    if difference <= Decimal("0.01"):
        return 1.0
    if difference <= Decimal("1.00"):
        return 0.4
    return 0.0


def score_match(transaction: dict[str, Any], document: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    tx_amount = Decimal(str(transaction["amount"]))
    doc_amount = Decimal(str(document["gross_amount"])) if document["gross_amount"] else None
    amount_score = _amount_score(tx_amount, doc_amount)
    date_score = _date_score(transaction["booking_date"], document.get("invoice_date"))
    label_basis = " ".join(
        filter(None, [document.get("supplier_name"), document.get("invoice_number"), document.get("project_ref")])
    )
    label_score = fuzz.token_set_ratio(transaction["label"], label_basis) / 100 if label_basis else 0.0
    reference_bonus = 1.0 if document.get("invoice_number") and document["invoice_number"] in (transaction.get("reference") or transaction["label"]) else 0.0
    score = round((0.55 * amount_score) + (0.20 * date_score) + (0.20 * label_score) + (0.05 * reference_bonus), 4)
    rationale = {
        "amount_score": amount_score,
        "date_score": date_score,
        "label_score": round(label_score, 4),
        "reference_bonus": reference_bonus,
    }
    return score, rationale


def match_bank_transactions(settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    init_db(current)
    certain = probable = unmatched = 0

    with get_connection(current) as connection:
        transactions = connection.execute(
            """
            SELECT id, booking_date, label, reference, amount
            FROM bank_transactions
            WHERE status = 'pending'
            ORDER BY id ASC
            """
        ).fetchall()
        documents = connection.execute(
            """
            SELECT id, supplier_name, invoice_number, invoice_date, gross_amount, project_ref
            FROM documents
            WHERE validation_status = 'approved'
            """
        ).fetchall()

        for transaction in transactions:
            best_document = None
            best_score = 0.0
            best_rationale: dict[str, Any] = {}

            for document in documents:
                score, rationale = score_match(dict(transaction), dict(document))
                if score > best_score:
                    best_score = score
                    best_document = document
                    best_rationale = rationale

            if best_score >= current.bank_match_certain_threshold:
                outcome = "certain_match"
                certain += 1
            elif best_score >= current.bank_match_probable_threshold:
                outcome = "probable_match"
                probable += 1
            else:
                outcome = "no_match"
                unmatched += 1

            connection.execute("DELETE FROM bank_matches WHERE bank_transaction_id = ?", (transaction["id"],))
            connection.execute(
                """
                INSERT INTO bank_matches(bank_transaction_id, document_id, score, outcome, rationale_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    transaction["id"],
                    best_document["id"] if best_document and outcome != "no_match" else None,
                    best_score,
                    outcome,
                    json.dumps(best_rationale, ensure_ascii=False),
                ),
            )
            connection.execute(
                """
                UPDATE bank_transactions
                SET status = ?
                WHERE id = ?
                """,
                (outcome, transaction["id"]),
            )
        connection.commit()

    return {
        "certain_match": certain,
        "probable_match": probable,
        "no_match": unmatched,
    }
