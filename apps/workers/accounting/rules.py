from __future__ import annotations

import json
import re
from typing import Any

from apps.workers.common.database import get_connection
from apps.workers.common.jsonio import load_json
from apps.workers.common.schemas import AccountingRule
from apps.workers.common.settings import Settings, get_settings


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def load_supplier_rules(settings: Settings | None = None) -> list[AccountingRule]:
    current = settings or get_settings()
    rules: list[AccountingRule] = []
    with get_connection(current) as connection:
        rows = connection.execute(
            """
            SELECT rule_key, supplier_match, compte_charge, compte_tva, compte_tiers, journal, confidence_threshold, metadata_json
            FROM supplier_rules
            WHERE active = 1
            ORDER BY id ASC
            """
        ).fetchall()
    for row in rows:
        rules.append(
            AccountingRule(
                rule_key=row["rule_key"],
                supplier_match=row["supplier_match"],
                compte_charge=row["compte_charge"],
                compte_tva=row["compte_tva"],
                compte_tiers=row["compte_tiers"],
                journal=row["journal"],
                confidence_threshold=row["confidence_threshold"],
                metadata=json.loads(row["metadata_json"]),
            )
        )

    if rules:
        return rules

    for candidate in [
        current.rules_dir / "supplier_rules.json",
        current.rules_dir / "supplier_rules.example.json",
    ]:
        if candidate.exists():
            raw = load_json(candidate)
            return [AccountingRule.model_validate(item) for item in raw]
    return []


def match_supplier_rule(document_payload: dict[str, Any], settings: Settings | None = None) -> AccountingRule | None:
    supplier_name = normalize_name(document_payload.get("supplier_name"))
    for rule in load_supplier_rules(settings):
        if normalize_name(rule.supplier_match) in supplier_name or supplier_name in normalize_name(rule.supplier_match):
            return rule
    return None
