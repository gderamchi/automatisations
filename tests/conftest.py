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
    monkeypatch.setenv("IMAP_USERNAME", "inbox@example.com")
    monkeypatch.setenv("IMAP_PASSWORD", "imap-secret")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USERNAME", "inbox@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-secret")
    monkeypatch.setenv("SMTP_FROM", "inbox@example.com")
    monkeypatch.setenv("REPLY_TO_EMAIL", "owner@example.com")
    monkeypatch.setenv("MAIL_REPLY_SUBJECT_PREFIX", "[AUTOMATISATIONS OCR]")
    monkeypatch.setenv("MAIL_BOOTSTRAP_CURRENT_UID", "false")
    monkeypatch.setenv("INTERFAST_WRITE_MODE", "disabled")
    monkeypatch.setenv("WEEKLY_ACCOUNTING_RECIPIENT", "compta@example.com")
    get_settings.cache_clear()
    settings = get_settings()
    init_db(settings)

    workbook_specs = [
        (
            "purchases.xlsx",
            "Achats",
            ["Date", "Fournisseur", "Facture", "HT", "TVA", "TTC", "Chantier"],
        ),
        (
            "grand_livre.xlsx",
            "GrandLivre",
            ["Date", "Fournisseur", "Facture", "HT", "TVA", "TTC", "Statut", "DatePaiement"],
        ),
        (
            "tresorerie.xlsx",
            "Tresorerie",
            ["Date", "Echeance", "Fournisseur", "TTC", "Statut", "DatePaiement", "ModePaiement"],
        ),
        (
            "chantiers.xlsx",
            "Chantiers",
            ["Date", "Chantier", "Fournisseur", "Nature", "Fourniture", "TTC", "NomFinal"],
        ),
        (
            "tva.xlsx",
            "TVA",
            ["Date", "Fournisseur", "Facture", "TVA", "TTC", "Statut"],
        ),
    ]
    for filename, sheet_name, headers in workbook_specs:
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = sheet_name
        for index, header in enumerate(headers, start=1):
            worksheet.cell(row=1, column=index, value=header)
        workbook.save(data_root / "state/cache" / filename)

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


@pytest.fixture()
def amazon_paid_invoice_text() -> str:
    return "\n".join(
        [
            "Facture",
            "# Payé",
            "Référence de paiement 13XOT2IZ9FQSE96V",
            "Vendu par shenzhenshijiataixingyekejiyouxiangongsi",
            "Date de la facture/Date de la livraison 12.02.2024",
            "Numéro de la facture DS-ASE-INV-FR-2024-22544185",
            "Total à payer 19,99 €",
            "TVA déclarée par Amazon Services Europe S.a.r.L.",
            "Numéro de la commande 407-6967530-0479500",
            "Facture Total 19,99 €",
        ]
    )


@pytest.fixture()
def amazon_webp_invoice_text() -> str:
    return "\n".join(
        [
            "amazon.fr",
            "",
            "FACTURE",
            "",
            "Adresse de facturation:",
            "pascal perez",
            "Lieu-dit Lapaul",
            "locoal mendon, 56550",
            "FR",
            "",
            "Amazon EU S.à r.l., Succursale Française",
            "67 Boulevard du General Leclerc",
            "Clichy 92110",
            "France",
            "TVA: FR12487773327",
            "",
            "Adresse de livraison:",
            "pascal perez",
            "Lieu-dit Lapaul",
            "locoal mendon, 56550",
            "FR",
            "",
            "Numéro de commande: 407-8996267-5607556",
            "Date de la commande: 28.12.2016",
            "Numéro de facture: EUVINS1-OFS-FR-305509734",
            "Date de la facture/Date de la provision: 28.12.2016",
            "",
            "| Qte | Description de l'article | Prix Unitaire (hors TVA) | Taux TVA% | Prix Unitaire (inclus TVA) | Prix Total (inclus TVA) |",
            "| --- | --- | --- | --- | --- | --- |",
            "| 1 | Spirit of Gamer Xgames Power Series Alimentation PC 750 W | B00VWXH8FO | 53,33 € | 20 % | 63,99 € | 63,99 € |",
            "| TOTAL: | 63,99 € |",
            "|  Hors TVA Total | TVA Total | TVA TOTAL  |",
            "| --- | --- | --- |",
            "|  20 % | 20 % |   |",
            "|  53,33 € | 10,66 € | 10,66 €  |",
            "",
            "Nos prix des équipements électriques et électroniques incluent l'éco-participation, conformément à l'article L. 541-10-2 du code de l'environnement.",
            "",
            "LU-BIO-04",
            "",
            "Amazon EU S.à r.l. - 5 Rue Plaete, L-2338 Luxembourg",
            "R.C.S. Luxembourg : B 101818",
            "",
            "Amazon EU S.à r.l., Succursale Française - 67 Boulevard du Général Leclerc, 92110 Clichy,",
            "France SIREN : 487773327 • RCS Nanterre • APE : 4791B • TVA : FR 12487773327",
        ]
    )
