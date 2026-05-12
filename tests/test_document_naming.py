from __future__ import annotations

from apps.workers.documents.naming import (
    NamingValidationError,
    build_official_filename,
    legacy_kind_to_ccm_type,
    legacy_supply_to_ccm_category,
    load_ccm_catalog,
    validate_ccm_fields,
)


def test_ccm_catalog_accepts_only_versioned_values(test_settings):
    catalog = load_ccm_catalog(test_settings)

    assert "TICKET" in catalog.types
    assert "FACTURE" in catalog.types
    assert catalog.subcategories_for("CARBURANT") == ["GAZOLE", "ESSENCE", "ADBLUE", "GNR"]

    fields = validate_ccm_fields(
        {
            "ccm_type": "TICKET",
            "ccm_category": "CARBURANT",
            "ccm_subcategory": "GAZOLE",
            "ccm_nom": "Station Total",
            "ccm_refdoc": "",
            "invoice_date": "2026-05-02",
        },
        catalog,
    )

    assert fields["ccm_refdoc"] == "NR"

    try:
        validate_ccm_fields(
            {
                "ccm_type": "BIDON",
                "ccm_category": "DIVERS",
                "ccm_subcategory": "INVENTE",
                "ccm_nom": "Station Total",
                "ccm_refdoc": "A1",
                "invoice_date": "2026-05-02",
            },
            catalog,
        )
    except NamingValidationError as exc:
        assert "ccm_type" in exc.errors
        assert "ccm_category" in exc.errors
    else:
        raise AssertionError("Invented CCM values must be rejected")


def test_legacy_ticket_values_map_to_locked_ccm_catalog():
    assert legacy_kind_to_ccm_type("receipt", source_name="ticket-total.pdf") == "TICKET"
    assert legacy_kind_to_ccm_type("invoice", source_name="facture.pdf") == "FACTURE"
    assert legacy_supply_to_ccm_category("carburant") == ("CARBURANT", "GAZOLE")
    assert legacy_supply_to_ccm_category("peage") == ("DEPLACEMENT", "PEAGE_AUTOROUTE")
    assert legacy_supply_to_ccm_category("repas") == ("DEPLACEMENT", "REPAS")
    assert legacy_supply_to_ccm_category("hotel") == ("DEPLACEMENT", "HOTEL_GITE")


def test_build_official_filename_for_ticket_carburant(test_settings):
    catalog = load_ccm_catalog(test_settings)
    fields = validate_ccm_fields(
        {
            "ccm_type": "TICKET",
            "ccm_category": "CARBURANT",
            "ccm_subcategory": "GAZOLE",
            "ccm_nom": "Station Total Energies",
            "ccm_refdoc": "047481 C1 P3",
            "ccm_chantier": "CCM026",
            "invoice_date": "2026-05-02",
        },
        catalog,
    )

    assert build_official_filename(fields, ".pdf") == (
        "2026-05-02_TICKET_STATION_TOTAL_ENERGIES_047481_C1_P3-GAZOLE_CCM026.pdf"
    )
