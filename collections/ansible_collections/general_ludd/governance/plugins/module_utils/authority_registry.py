"""Authority registry — issuing authorities mapped to instrument types for
the governance collection.

Maps sovereign and administrative authorities to the documents and
instruments they issue: passports, identity documents, driving licenses,
professional licenses, business registrations, export permits, building
permits, and treaty-related instruments.

Public surface::

    AUTHORITY_INSTRUMENTS  dict of authority_code -> {name, instruments: [...], jurisdiction}
    PASSPORT_AUTHORITIES   dict of country_code -> issuing authority dict
    LICENSE_AUTHORITIES    dict of license_type -> {countries: {code: authority dict}}
    TREATY_DEPOSITARIES    dict of depositary institutions
    EXPORT_CONTROL_AUTHORITIES  dict of country_code -> authority dict

    get_authority(code)          -> authority dict or None
    get_passport_authority(country_code) -> authority dict or None
    get_license_authority(license_type, country_code) -> authority dict or None
    get_treaty_depositary(depositary_name) -> depositary dict or None
    get_export_control_authority(country_code) -> authority dict or None
    authorities_by_instrument(instrument_type) -> list[authority dict]
"""

from __future__ import annotations

from typing import Any

# ── Core authority registry ──────────────────────────────────────────────

AUTHORITY_INSTRUMENTS: dict[str, dict[str, Any]] = {
    "US-DOS": {
        "name": "U.S. Department of State",
        "jurisdiction": "US",
        "instruments": ["passport", "visa", "consular_report", "diplomatic_credential"],
        "url": "https://travel.state.gov",
        "contact": "Bureau of Consular Affairs",
    },
    "US-DHS": {
        "name": "U.S. Department of Homeland Security",
        "jurisdiction": "US",
        "instruments": [
            "visa_watchlist",
            "entry_exit_record",
            "asylum_decision",
            "naturalization_certificate",
            "temporary_protected_status",
        ],
        "url": "https://www.dhs.gov",
        "contact": "Office of the Secretary",
    },
    "US-DOT-FMCSA": {
        "name": "U.S. Federal Motor Carrier Safety Administration",
        "jurisdiction": "US",
        "instruments": ["commercial_drivers_license", "motor_carrier_permit"],
        "url": "https://www.fmcsa.dot.gov",
        "contact": None,
    },
    "US-DOJ-DEA": {
        "name": "U.S. Drug Enforcement Administration",
        "jurisdiction": "US",
        "instruments": ["controlled_substance_license", "prescriber_registration"],
        "url": "https://www.dea.gov",
        "contact": "DEA Registration",
    },
    "US-COMMERCE-BIS": {
        "name": "U.S. Bureau of Industry and Security",
        "jurisdiction": "US",
        "instruments": ["export_license", "deemed_export_authorization", "technology_transfer_license"],
        "url": "https://www.bis.doc.gov",
        "contact": "BIS Office of Exporter Services",
    },
    "US-STATE-DDTC": {
        "name": "U.S. Directorate of Defense Trade Controls",
        "jurisdiction": "US",
        "instruments": ["itar_export_license", "munitions_export_authorization", "brokering_license"],
        "url": "https://www.pmddtc.state.gov",
        "contact": "DDTC Response Team",
    },
    "US-TREAS-OFAC": {
        "name": "U.S. Office of Foreign Assets Control",
        "jurisdiction": "US",
        "instruments": ["sanctions_license", "blocked_property_authorization", "specific_license"],
        "url": "https://home.treasury.gov/policy-issues/office-of-foreign-assets-control-sanctions",
        "contact": "OFAC Licensing Division",
    },
    "UK-HMPO": {
        "name": "HM Passport Office",
        "jurisdiction": "GB",
        "instruments": ["passport", "emergency_travel_document"],
        "url": "https://www.gov.uk/government/organisations/hm-passport-office",
        "contact": "Passport Adviceline",
    },
    "UK-HOME": {
        "name": "UK Home Office",
        "jurisdiction": "GB",
        "instruments": ["visa", "settlement_permit", "naturalization_certificate", "biometric_residence_permit"],
        "url": "https://www.gov.uk/government/organisations/home-office",
        "contact": "UK Visas and Immigration",
    },
    "UK-DVLA": {
        "name": "Driver and Vehicle Licensing Agency",
        "jurisdiction": "GB",
        "instruments": ["driving_license", "vehicle_registration"],
        "url": "https://www.gov.uk/government/organisations/driver-and-vehicle-licensing-agency",
        "contact": "DVLA Contact Centre",
    },
    "UK-HSE": {
        "name": "UK Health and Safety Executive",
        "jurisdiction": "GB",
        "instruments": ["construction_permit", "hazardous_substance_license", "workplace_safety_certificate"],
        "url": "https://www.hse.gov.uk",
        "contact": "HSE Infoline",
    },
    "UK-ECJU": {
        "name": "UK Export Control Joint Unit",
        "jurisdiction": "GB",
        "instruments": ["export_license", "trade_control_license", "military_goods_export_license"],
        "url": "https://www.gov.uk/government/organisations/export-control-organisation",
        "contact": "ECJU Helpline",
    },
    "CA-IRCC": {
        "name": "Immigration, Refugees and Citizenship Canada",
        "jurisdiction": "CA",
        "instruments": ["passport", "visa", "permanent_resident_card", "citizenship_certificate"],
        "url": "https://www.canada.ca/en/immigration-refugees-citizenship.html",
        "contact": "IRCC Client Support Centre",
    },
    "CA-GAC": {
        "name": "Global Affairs Canada",
        "jurisdiction": "CA",
        "instruments": ["export_permit", "import_permit", "diplomatic_credential"],
        "url": "https://www.international.gc.ca",
        "contact": "Export Controls Division",
    },
    "AU-DFAT": {
        "name": "Department of Foreign Affairs and Trade (Australia)",
        "jurisdiction": "AU",
        "instruments": ["passport", "visa", "consular_document", "diplomatic_credential", "export_permit"],
        "url": "https://www.dfat.gov.au",
        "contact": "Australian Passport Office",
    },
    "AU-DEF": {
        "name": "Department of Defence (Australia)",
        "jurisdiction": "AU",
        "instruments": ["defence_export_permit", "security_clearance", "controlled_goods_license"],
        "url": "https://www.defence.gov.au",
        "contact": "Defence Export Controls",
    },
    "FR-ANTS": {
        "name": "Agence Nationale des Titres Securises (France)",
        "jurisdiction": "FR",
        "instruments": ["passport", "national_id_card", "driving_license", "vehicle_registration"],
        "url": "https://ants.gouv.fr",
        "contact": "Service d'assistance ANTS",
    },
    "FR-MEAE": {
        "name": "Ministere de l'Europe et des Affaires etrangeres (France)",
        "jurisdiction": "FR",
        "instruments": ["visa", "diplomatic_credential", "export_license"],
        "url": "https://www.diplomatie.gouv.fr",
        "contact": "Direction des Francais a l'etranger",
    },
    "DE-BVA": {
        "name": "Bundesverwaltungsamt (Germany)",
        "jurisdiction": "DE",
        "instruments": ["passport", "national_id_card", "naturalization_certificate"],
        "url": "https://www.bva.bund.de",
        "contact": "Buergerservice",
    },
    "DE-BAFA": {
        "name": "Bundesamt fuer Wirtschaft und Ausfuhrkontrolle (Germany)",
        "jurisdiction": "DE",
        "instruments": ["export_license", "import_permit", "embargo_exception_authorization"],
        "url": "https://www.bafa.de",
        "contact": "Ausfuhrkontrolle",
    },
    "EU-COM": {
        "name": "European Commission",
        "jurisdiction": "EU",
        "instruments": ["competition_clearance", "merger_approval", "state_aid_authorization", "trade_agreement"],
        "url": "https://ec.europa.eu",
        "contact": "Directorate-General for Trade",
    },
    "UN-TREATY": {
        "name": "United Nations Treaty Section",
        "jurisdiction": "international",
        "instruments": ["treaty_depositary", "multilateral_convention_registration", "treaty_map_status"],
        "url": "https://treaties.un.org",
        "contact": "Treaty Section, Office of Legal Affairs",
    },
    "WIPO": {
        "name": "World Intellectual Property Organization",
        "jurisdiction": "international",
        "instruments": ["patent_registration", "trademark_registration", "design_registration", "arbitration_decision"],
        "url": "https://www.wipo.int",
        "contact": "WIPO Arbitration and Mediation Center",
    },
    "ICAO": {
        "name": "International Civil Aviation Organization",
        "jurisdiction": "international",
        "instruments": ["passport_standard", "aircraft_registration", "air_operator_certificate"],
        "url": "https://www.icao.int",
        "contact": "ICAO Secretariat",
    },
}

# ── Passport authorities by country ──────────────────────────────────────

PASSPORT_AUTHORITIES: dict[str, dict[str, Any]] = {
    "US": {
        "authority": "US-DOS",
        "name": "U.S. Department of State",
        "document": "US Passport",
        "biometric_since": 2007,
    },
    "GB": {
        "authority": "UK-HMPO",
        "name": "HM Passport Office",
        "document": "British Passport",
        "biometric_since": 2006,
    },
    "CA": {"authority": "CA-IRCC", "name": "IRCC", "document": "Canadian Passport", "biometric_since": 2013},
    "AU": {
        "authority": "AU-DFAT",
        "name": "DFAT Australian Passport Office",
        "document": "Australian Passport",
        "biometric_since": 2005,
    },
    "FR": {"authority": "FR-ANTS", "name": "ANTS", "document": "French Passport", "biometric_since": 2008},
    "DE": {
        "authority": "DE-BVA",
        "name": "Bundesverwaltungsamt",
        "document": "German Passport (Reisepass)",
        "biometric_since": 2005,
    },
}

# ── License authorities by type ──────────────────────────────────────────

LICENSE_AUTHORITIES: dict[str, dict[str, Any]] = {
    "driving": {
        "US": {
            "authority": "state_dmv",
            "name": "State DMV (varies by state)",
            "note": "Each US state issues its own driving license.",
        },
        "GB": {"authority": "UK-DVLA", "name": "DVLA", "note": "DVLA issues all GB driving licenses."},
        "FR": {"authority": "FR-ANTS", "name": "ANTS", "note": "ANTS processes French driving license applications."},
    },
    "export": {
        "US": {
            "authority": "US-COMMERCE-BIS",
            "name": "Bureau of Industry and Security",
            "note": "Dual-use items; ITAR items via DDTC.",
        },
        "GB": {
            "authority": "UK-ECJU",
            "name": "Export Control Joint Unit",
            "note": "Strategic export controls for military and dual-use goods.",
        },
        "DE": {"authority": "DE-BAFA", "name": "BAFA", "note": "Bundesamt fuer Wirtschaft und Ausfuhrkontrolle."},
    },
    "business": {
        "US": {
            "authority": "state_secretary",
            "name": "State Secretary of State",
            "note": "Business registrations are state-level in the US.",
        },
        "GB": {
            "authority": "companies_house",
            "name": "Companies House",
            "note": "Companies House registers all UK companies.",
        },
        "DE": {
            "authority": "handelsregister",
            "name": "Handelsregister",
            "note": "Commercial register maintained by local courts (Amtsgericht).",
        },
    },
    "building": {
        "US": {
            "authority": "local_building_dept",
            "name": "Local Building Department",
            "note": "Building permits are municipal/county-level in the US.",
        },
        "GB": {
            "authority": "local_authority",
            "name": "Local Planning Authority",
            "note": "Building control via local authority or approved inspector.",
        },
        "FR": {
            "authority": "mairie",
            "name": "Mairie / DDT",
            "note": "Permis de construire issued by the local mairie.",
        },
    },
}

# ── Treaty depositaries ─────────────────────────────────────────────────

TREATY_DEPOSITARIES: dict[str, dict[str, Any]] = {
    "UN Treaty Section": {
        "institution": "United Nations Treaty Section",
        "jurisdiction": "international",
        "role": "Depositary for multilateral treaties under the UN Charter.",
        "url": "https://treaties.un.org",
        "treaties_registered": 560,
        "certified_true_copies": True,
    },
    "ICJ Registry": {
        "institution": "International Court of Justice Registry",
        "jurisdiction": "international",
        "role": "Registrar for treaties designating the ICJ as depositary.",
        "url": "https://www.icj-cij.org",
        "treaties_registered": None,
        "certified_true_copies": True,
    },
    "Swiss Federal Council": {
        "institution": "Swiss Federal Department of Foreign Affairs",
        "jurisdiction": "CH",
        "role": "Depositary for the Geneva Conventions and many Hague Conventions.",
        "url": "https://www.eda.admin.ch",
        "treaties_registered": 80,
        "certified_true_copies": True,
    },
    "US Department of State": {
        "institution": "U.S. Department of State, Office of Treaty Affairs",
        "jurisdiction": "US",
        "role": "Depositary for certain bilateral and multilateral treaties to which the US is a party.",
        "url": "https://www.state.gov/treaty-affairs",
        "treaties_registered": None,
        "certified_true_copies": False,
    },
}

# ── Export control authorities ───────────────────────────────────────────

EXPORT_CONTROL_AUTHORITIES: dict[str, dict[str, Any]] = {
    "US": {
        "authority": "US-COMMERCE-BIS",
        "name": "Bureau of Industry and Security",
        "regime": "EAR (Export Administration Regulations)",
        "url": "https://www.bis.doc.gov",
        "co_regulators": ["DDTC (ITAR)", "OFAC (sanctions)"],
    },
    "GB": {
        "authority": "UK-ECJU",
        "name": "Export Control Joint Unit",
        "regime": "UK Strategic Export Controls",
        "url": "https://www.gov.uk/guidance/export-controls",
        "co_regulators": [],
    },
    "DE": {
        "authority": "DE-BAFA",
        "name": "BAFA",
        "regime": "EU Dual-Use Regulation + German Foreign Trade Act (AWG)",
        "url": "https://www.bafa.de",
        "co_regulators": [],
    },
    "AU": {
        "authority": "AU-DEF",
        "name": "Defence Export Controls",
        "regime": "Defence Trade Controls Act 2012",
        "url": "https://www.defence.gov.au/export-controls",
        "co_regulators": ["DFAT (sanctions)"],
    },
    "CA": {
        "authority": "CA-GAC",
        "name": "Global Affairs Canada — Export Controls Division",
        "regime": "Export and Import Permits Act",
        "url": "https://www.international.gc.ca/controls-controles",
        "co_regulators": [],
    },
}

# ── Public functions ────────────────────────────────────────────────────


def get_authority(code: str) -> dict[str, Any] | None:
    """Look up an authority by its registry code."""
    return AUTHORITY_INSTRUMENTS.get(code.strip().upper())


def get_passport_authority(country_code: str) -> dict[str, Any] | None:
    """Return the passport issuing authority for a country code, or None."""
    return PASSPORT_AUTHORITIES.get(country_code.strip().upper())


def get_license_authority(license_type: str, country_code: str) -> dict[str, Any] | None:
    """Return the issuing authority for a license type in a country.

    Args:
        license_type: One of 'driving', 'export', 'business', 'building'.
        country_code: ISO alpha-2 country code.

    Returns the authority dict, or None if not found.
    """
    lt = license_type.strip().lower()
    cc = country_code.strip().upper()
    type_registry = LICENSE_AUTHORITIES.get(lt)
    if type_registry is None:
        return None
    return type_registry.get(cc)


def get_treaty_depositary(depositary_name: str) -> dict[str, Any] | None:
    """Look up a treaty depositary by name, or None."""
    if depositary_name in TREATY_DEPOSITARIES:
        return dict(TREATY_DEPOSITARIES[depositary_name])
    lowered = depositary_name.lower()
    for key, rec in TREATY_DEPOSITARIES.items():
        if key.lower() == lowered:
            return dict(rec)
    return None


def get_export_control_authority(country_code: str) -> dict[str, Any] | None:
    """Return the export control authority for a country code, or None."""
    return EXPORT_CONTROL_AUTHORITIES.get(country_code.strip().upper())


def authorities_by_instrument(instrument_type: str) -> list[dict[str, Any]]:
    """Return all authorities that issue a given instrument type.

    Args:
        instrument_type: keyword to search authority instrument lists
                         (e.g., 'passport', 'export_license', 'driving_license').

    Returns a list of matching authority dicts (possibly empty).
    """
    it = instrument_type.strip().lower().replace(" ", "_")
    results: list[dict[str, Any]] = []
    for code, auth in AUTHORITY_INSTRUMENTS.items():
        instruments = [i.lower().replace(" ", "_") for i in auth["instruments"]]
        if it in instruments:
            results.append({"code": code, **auth})
    if not results:
        for code, auth in AUTHORITY_INSTRUMENTS.items():
            instruments = [i.lower().replace(" ", "_") for i in auth["instruments"]]
            for inst in instruments:
                if it in inst or inst in it:
                    results.append({"code": code, **auth})
                    break
    return results


__all__ = [
    "AUTHORITY_INSTRUMENTS",
    "PASSPORT_AUTHORITIES",
    "LICENSE_AUTHORITIES",
    "TREATY_DEPOSITARIES",
    "EXPORT_CONTROL_AUTHORITIES",
    "get_authority",
    "get_passport_authority",
    "get_license_authority",
    "get_treaty_depositary",
    "get_export_control_authority",
    "authorities_by_instrument",
]
