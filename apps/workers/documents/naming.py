from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

from apps.workers.common.settings import Settings, get_settings


LEGACY_DOCUMENT_KINDS = {
    "invoice",
    "sales_invoice",
    "purchase_invoice",
    "quotation",
    "receipt",
    "purchase_order",
    "credit_note",
    "unknown",
}

LEGACY_SUPPLY_TYPES = {
    "carburant",
    "materiel",
    "hotel",
    "repas",
    "peage",
    "consommable",
    "unknown",
}

LEGACY_KIND_TO_CCM_TYPE = {
    "invoice": "FACTURE",
    "purchase_invoice": "FACTURE",
    "sales_invoice": "FACTURE",
    "quotation": "DEVIS",
    "receipt": "TICKET",
    "purchase_order": "BONCOMMANDE",
    "credit_note": "AVOIR",
}

CCM_TYPE_TO_LEGACY_KIND = {
    "FACTURE": "invoice",
    "DEVIS": "quotation",
    "TICKET": "receipt",
    "BONCOMMANDE": "purchase_order",
    "AVOIR": "credit_note",
}

LEGACY_SUPPLY_TO_CCM = {
    "carburant": ("CARBURANT", "GAZOLE"),
    "materiel": ("FOURNITURES", "MATERIEL"),
    "consommable": ("FOURNITURES", "CONSUMMABLE"),
    "hotel": ("DEPLACEMENT", "HOTEL_GITE"),
    "repas": ("DEPLACEMENT", "REPAS"),
    "peage": ("DEPLACEMENT", "PEAGE_AUTOROUTE"),
}

CCM_TO_LEGACY_SUPPLY = {
    ("CARBURANT", "GAZOLE"): "carburant",
    ("CARBURANT", "ESSENCE"): "carburant",
    ("CARBURANT", "ADBLUE"): "carburant",
    ("CARBURANT", "GNR"): "carburant",
    ("DEPLACEMENT", "HOTEL_GITE"): "hotel",
    ("DEPLACEMENT", "REPAS"): "repas",
    ("DEPLACEMENT", "PEAGE_AUTOROUTE"): "peage",
    ("FOURNITURES", "MATERIEL"): "materiel",
    ("FOURNITURES", "MATERIAUX"): "materiel",
    ("FOURNITURES", "PETIT_OUTILLAGE"): "materiel",
    ("FOURNITURES", "CONSOMMABLE"): "consommable",
    ("FOURNITURES", "EPI"): "consommable",
}


@dataclass(frozen=True)
class CcmCatalog:
    version: str
    format: str
    types: list[str]
    categories: dict[str, list[str]]
    forbidden_types: set[str]

    def subcategories_for(self, category: str | None) -> list[str]:
        return self.categories.get(normalize_code(category), [])

    def is_type_allowed(self, value: str | None) -> bool:
        return normalize_code(value) in self.types

    def is_category_allowed(self, value: str | None) -> bool:
        return normalize_code(value) in self.categories

    def is_subcategory_allowed(self, category: str | None, value: str | None) -> bool:
        return normalize_code(value) in self.subcategories_for(category)


class NamingValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{key}: {value}" for key, value in errors.items()))


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


def normalize_code(value: Any) -> str:
    if value in (None, ""):
        return ""
    raw = _strip_accents(str(value)).upper()
    raw = raw.replace("&", " ET ")
    raw = re.sub(r"[^A-Z0-9]+", "_", raw)
    return re.sub(r"_+", "_", raw).strip("_")


def normalize_filename_token(value: Any, *, default: str | None = None) -> str:
    normalized = normalize_code(value)
    if normalized:
        return normalized
    return normalize_code(default) if default else ""


def load_ccm_catalog(settings: Settings | None = None) -> CcmCatalog:
    current = settings or get_settings()
    path = current.config_dir / "document_naming" / "ccm_v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return CcmCatalog(
        version=str(data["version"]),
        format=str(data["format"]),
        types=[normalize_code(item) for item in data["types"]],
        categories={
            normalize_code(category): [normalize_code(item) for item in subcategories]
            for category, subcategories in data["categories"].items()
        },
        forbidden_types={normalize_code(item) for item in data.get("forbidden_types", [])},
    )


def validate_legacy_document_kind(value: str | None) -> None:
    if value in (None, ""):
        return
    if normalize_code(value).lower() not in LEGACY_DOCUMENT_KINDS:
        raise NamingValidationError({"document_kind": "Valeur technique non autorisée"})


def validate_legacy_supply_type(value: str | None) -> None:
    if value in (None, ""):
        return
    if normalize_code(value).lower() not in LEGACY_SUPPLY_TYPES:
        raise NamingValidationError({"supply_type": "Valeur technique non autorisée"})


def legacy_kind_to_ccm_type(value: str | None, *, source_name: str | None = None) -> str | None:
    haystack = f"{source_name or ''} {value or ''}".lower()
    if "ticket" in haystack or "receipt" in haystack or "recu" in haystack or "reçu" in haystack:
        return "TICKET"
    normalized = normalize_code(value).lower()
    return LEGACY_KIND_TO_CCM_TYPE.get(normalized)


def ccm_type_to_legacy_kind(value: str | None) -> str:
    return CCM_TYPE_TO_LEGACY_KIND.get(normalize_code(value), "unknown")


def legacy_supply_to_ccm_category(value: str | None) -> tuple[str | None, str | None]:
    normalized = normalize_code(value).lower()
    category, subcategory = LEGACY_SUPPLY_TO_CCM.get(normalized, (None, None))
    if subcategory == "CONSUMMABLE":
        subcategory = "CONSOMMABLE"
    return category, subcategory


def ccm_category_to_legacy_supply(category: str | None, subcategory: str | None) -> str:
    normalized_category = normalize_code(category)
    normalized_subcategory = normalize_code(subcategory)
    return CCM_TO_LEGACY_SUPPLY.get((normalized_category, normalized_subcategory), "unknown")


def _coerce_invoice_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    if raw:
        try:
            return datetime.fromisoformat(raw[:10]).date().isoformat()
        except ValueError:
            return ""
    return ""


def infer_ccm_fields(
    payload: dict[str, Any],
    *,
    source_name: str | None = None,
    target_label: str | None = None,
) -> dict[str, Any]:
    ccm_type = normalize_code(payload.get("ccm_type")) or legacy_kind_to_ccm_type(
        payload.get("document_kind") or payload.get("document_type"),
        source_name=source_name,
    )
    inferred_category, inferred_subcategory = legacy_supply_to_ccm_category(payload.get("supply_type"))
    ccm_category = normalize_code(payload.get("ccm_category")) or inferred_category
    ccm_subcategory = normalize_code(payload.get("ccm_subcategory")) or inferred_subcategory
    supplier_name = payload.get("ccm_nom") or payload.get("supplier_name")
    refdoc = payload.get("ccm_refdoc") or payload.get("invoice_number") or payload.get("reference")
    chantier = payload.get("ccm_chantier") or payload.get("project_ref") or target_label
    return {
        "ccm_type": ccm_type,
        "ccm_category": ccm_category,
        "ccm_subcategory": ccm_subcategory,
        "ccm_nom": supplier_name,
        "ccm_refdoc": refdoc,
        "ccm_refclient": payload.get("ccm_refclient"),
        "ccm_chantier": chantier,
        "invoice_date": payload.get("invoice_date"),
    }


def validate_ccm_fields(raw_fields: dict[str, Any], catalog: CcmCatalog | None = None) -> dict[str, str]:
    current_catalog = catalog or load_ccm_catalog()
    ccm_type = normalize_code(raw_fields.get("ccm_type"))
    ccm_category = normalize_code(raw_fields.get("ccm_category"))
    ccm_subcategory = normalize_code(raw_fields.get("ccm_subcategory"))
    invoice_date = _coerce_invoice_date(raw_fields.get("invoice_date"))
    ccm_nom = normalize_filename_token(raw_fields.get("ccm_nom"))
    ccm_refdoc = normalize_filename_token(raw_fields.get("ccm_refdoc"), default="NR") or "NR"
    ccm_refclient = normalize_filename_token(raw_fields.get("ccm_refclient"))
    ccm_chantier = normalize_filename_token(raw_fields.get("ccm_chantier"))

    errors: dict[str, str] = {}
    if not invoice_date:
        errors["invoice_date"] = "Date obligatoire au format AAAA-MM-JJ"
    if not ccm_type:
        errors["ccm_type"] = "Type document CCM obligatoire"
    elif ccm_type in current_catalog.forbidden_types or not current_catalog.is_type_allowed(ccm_type):
        errors["ccm_type"] = f"Type document CCM non autorisé: {ccm_type}"
    if not ccm_category:
        errors["ccm_category"] = "Catégorie CCM obligatoire"
    elif not current_catalog.is_category_allowed(ccm_category):
        errors["ccm_category"] = f"Catégorie CCM non autorisée: {ccm_category}"
    if not ccm_subcategory:
        errors["ccm_subcategory"] = "Sous-catégorie CCM obligatoire"
    elif ccm_category and not current_catalog.is_subcategory_allowed(ccm_category, ccm_subcategory):
        errors["ccm_subcategory"] = f"Sous-catégorie {ccm_subcategory} non autorisée pour {ccm_category}"
    if not ccm_nom:
        errors["ccm_nom"] = "Nom fournisseur/client obligatoire"

    if errors:
        raise NamingValidationError(errors)

    return {
        "invoice_date": invoice_date,
        "ccm_type": ccm_type,
        "ccm_category": ccm_category,
        "ccm_subcategory": ccm_subcategory,
        "ccm_nom": ccm_nom,
        "ccm_refdoc": ccm_refdoc,
        "ccm_refclient": ccm_refclient,
        "ccm_chantier": ccm_chantier,
    }


def build_official_filename(validated_fields: dict[str, Any], extension: str | None) -> str:
    suffix = extension or ".pdf"
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    parts = [
        str(validated_fields["invoice_date"]),
        normalize_code(validated_fields["ccm_type"]),
        normalize_filename_token(validated_fields["ccm_nom"]),
        f"{normalize_filename_token(validated_fields['ccm_refdoc'], default='NR')}-{normalize_code(validated_fields['ccm_subcategory'])}",
    ]
    if validated_fields.get("ccm_refclient"):
        parts.append(normalize_filename_token(validated_fields["ccm_refclient"]))
    if validated_fields.get("ccm_chantier"):
        parts.append(normalize_filename_token(validated_fields["ccm_chantier"]))
    return "_".join(parts) + suffix.lower()


def official_filename_from_payload(
    payload: dict[str, Any],
    *,
    source_name: str | None = None,
    target_label: str | None = None,
    extension: str | None = None,
    catalog: CcmCatalog | None = None,
) -> tuple[str | None, dict[str, str], dict[str, str]]:
    raw_fields = infer_ccm_fields(payload, source_name=source_name, target_label=target_label)
    try:
        fields = validate_ccm_fields(raw_fields, catalog)
    except NamingValidationError as exc:
        return None, {key: normalize_filename_token(value) for key, value in raw_fields.items() if value not in (None, "")}, exc.errors
    return build_official_filename(fields, extension), fields, {}


def audit_path_for_ccm_naming(root: Path, settings: Settings | None = None) -> dict[str, Any]:
    catalog = load_ccm_catalog(settings)
    allowed_suffixes = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".txt"}
    inspected = 0
    compliant = 0
    non_compliant: list[dict[str, str]] = []
    subcategories = sorted(
        {subcategory for values in catalog.categories.values() for subcategory in values},
        key=len,
        reverse=True,
    )
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
            continue
        inspected += 1
        match = re.match(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<type>[A-Z0-9]+)_(?P<body>.+)$", path.stem)
        if not match or "-" not in match.group("body"):
            non_compliant.append({"path": str(path), "reason": "Format CCM non reconnu"})
            continue
        doc_type = normalize_code(match.group("type"))
        body = match.group("body")
        subcategory = next(
            (
                candidate
                for candidate in subcategories
                if f"-{candidate}" in body and re.search(rf"-{re.escape(candidate)}(?:_|$)", body)
            ),
            "",
        )
        matching_categories = [
            category
            for category, subcategories in catalog.categories.items()
            if subcategory in subcategories
        ]
        if doc_type not in catalog.types or not matching_categories:
            non_compliant.append({"path": str(path), "reason": "Type ou sous-catégorie hors catalogue"})
            continue
        compliant += 1
    return {
        "root": str(root),
        "inspected": inspected,
        "compliant": compliant,
        "non_compliant": len(non_compliant),
        "items": non_compliant,
    }
