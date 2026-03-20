from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from apps.api.app.main import app
from apps.workers.common.database import init_db
from apps.workers.common.settings import get_settings


@pytest.fixture()
def test_settings(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("DB_PATH", str(data_root / "state/sqlite/test.db"))
    monkeypatch.setenv("OCR_MOCK_MODE", "true")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-token")
    monkeypatch.setenv("VALIDATION_USERNAME", "validator")
    monkeypatch.setenv("VALIDATION_PASSWORD", "secret")
    get_settings.cache_clear()
    settings = get_settings()
    init_db(settings)

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Achats"
    worksheet["A1"] = "Date"
    worksheet["B1"] = "Fournisseur"
    worksheet["C1"] = "Facture"
    worksheet["D1"] = "HT"
    worksheet["E1"] = "TVA"
    worksheet["F1"] = "TTC"
    worksheet["G1"] = "Chantier"
    workbook.save(data_root / "state/cache/purchases.xlsx")

    yield settings
    get_settings.cache_clear()


@pytest.fixture()
def client(test_settings):
    return TestClient(app)


@pytest.fixture()
def sample_invoice_text() -> str:
    return "\n".join(
        [
            "ACME BTP SAS",
            "SIRET 123 456 789 12345",
            "FACTURE N FAC-2026-001",
            "Date facture: 10/03/2026",
            "Echeance: 25/03/2026",
            "Chantier: Residence Soleil",
            "Total HT: 1000,00",
            "TVA 20%: 200,00",
            "Total TTC: 1200,00",
        ]
    )


@pytest.fixture()
def incomplete_invoice_text() -> str:
    return "\n".join(
        [
            "Fournisseur Test",
            "FACTURE",
            "Merci pour votre confiance",
        ]
    )
