"""
licenses_permits -- Professional licenses, export controls, business permits,
and license validity checking for the governance collection.

Data shape:

    LICENSE_TYPES: frozenset of license category tokens
    LICENSE_REGISTRIES[country] -> dict with registries keyed by license type
    EXPORT_LICENSE_REQUIREMENTS[country] -> dict with goods categories and controls
    LICENSE_VALIDITY_RULES[license_type] -> dict with duration, renewal, verification

Functions:
    get_license_info(license_type, country) -> dict | None
    get_export_license_requirements(country, goods_category) -> dict | None
    check_license_validity(license_type, issuing_body, license_id) -> dict
    list_professions_for_country(country) -> list[str]
    get_regulating_body(license_type, country) -> dict | None
"""

from __future__ import annotations

from typing import Any

# ── License type taxonomy ──────────────────────────────────────────────────

LICENSE_TYPES: frozenset[str] = frozenset(
    {
        "driving",
        "pilot",
        "maritime",
        "medical_practitioner",
        "nursing",
        "pharmacist",
        "lawyer",
        "notary",
        "engineer_professional",
        "architect",
        "electrician",
        "plumber",
        "teacher_educator",
        "accountant",
        "real_estate_agent",
        "security_guard",
        "firearm",
        "broadcasting",
        "business_operating",
        "liquor_alcohol",
        "food_service",
        "childcare",
        "export_controlled",
        "import_permit",
        "environmental_permit",
        "mining_extraction",
        "fishing",
        "hunting",
    }
)

# ── License registries by country ──────────────────────────────────────────

LICENSE_REGISTRIES: dict[str, dict[str, Any]] = {
    "US": {
        "driving": {
            "issuing_body": "State Department of Motor Vehicles (DMV)",
            "verification_url": "https://www.dmv.org/license-status-check",
            "typical_duration": "4-8 years (varies by state)",
            "renewal_requirements": ["vision test", "written test (if expired >1yr)"],
        },
        "pilot": {
            "issuing_body": "Federal Aviation Administration (FAA)",
            "verification_url": "https://amsrvs.registry.faa.gov/airmeninquiry/",
            "typical_duration": "24 months (medical certificate validity varies)",
            "renewal_requirements": ["flight review (BFR every 24 months)", "medical exam"],
        },
        "medical_practitioner": {
            "issuing_body": "State Medical Board",
            "verification_url": "https://www.fsmb.org/physician-data-center/",
            "typical_duration": "1-3 years (varies by state)",
            "renewal_requirements": ["CME credits", "license fee", "no disciplinary actions"],
        },
        "lawyer": {
            "issuing_body": "State Bar Association",
            "verification_url": "https://www.americanbar.org/groups/bar_services/resources/state-bar-directory/",
            "typical_duration": "Annual (varies by state)",
            "renewal_requirements": ["CLE credits", "bar dues", "good standing"],
        },
        "engineer_professional": {
            "issuing_body": "State Board of Professional Engineers",
            "verification_url": "https://www.nspe.org/resources/licensure/what-pe",
            "typical_duration": "1-2 years (varies by state)",
            "renewal_requirements": ["PDH credits", "license fee"],
        },
        "nursing": {
            "issuing_body": "State Board of Nursing (NCSBN)",
            "verification_url": "https://www.nursys.com",
            "typical_duration": "2-3 years (varies by state)",
            "renewal_requirements": ["continuing education", "license fee", "background check"],
        },
        "business_operating": {
            "issuing_body": "City/County Business License Office",
            "verification_url": "Secretary of State business search (varies by state)",
            "typical_duration": "Annual",
            "renewal_requirements": ["tax clearance", "zoning compliance", "license fee"],
        },
    },
    "GB": {
        "driving": {
            "issuing_body": "Driver and Vehicle Licensing Agency (DVLA)",
            "verification_url": "https://www.gov.uk/view-driving-licence",
            "typical_duration": "Until age 70 (photo renewal every 10 years)",
            "renewal_requirements": ["medical self-declaration at 70+"],
        },
        "medical_practitioner": {
            "issuing_body": "General Medical Council (GMC)",
            "verification_url": "https://www.gmc-uk.org/registration-and-licensing/the-medical-register",
            "typical_duration": "Annual revalidation (5-year cycle)",
            "renewal_requirements": ["appraisal", "CPD", "multi-source feedback"],
        },
        "lawyer": {
            "issuing_body": "Solicitors Regulation Authority (SRA) / Bar Standards Board",
            "verification_url": "https://www.sra.org.uk/solicitors/",
            "typical_duration": "Annual practising certificate",
            "renewal_requirements": ["CPD", "practising certificate fee", "insurance"],
        },
        "engineer_professional": {
            "issuing_body": "Engineering Council UK (via professional institutions like ICE, IMechE)",
            "verification_url": "https://www.engc.org.uk/check-an-engineer/",
            "typical_duration": "Annual CEng/IEng renewal",
            "renewal_requirements": ["CPD", "membership fee", "code of conduct"],
        },
        "business_operating": {
            "issuing_body": "Companies House / Local Authority",
            "verification_url": "https://find-and-update.company-information.service.gov.uk",
            "typical_duration": "Annual confirmation statement",
            "renewal_requirements": ["confirmation statement filing", "annual accounts"],
        },
    },
    "CA": {
        "driving": {
            "issuing_body": "Provincial Ministry of Transportation",
            "verification_url": "Province-specific (e.g., ServiceOntario, ICBC)",
            "typical_duration": "5 years (varies by province)",
            "renewal_requirements": ["vision test", "medical exam (seniors)"],
        },
        "medical_practitioner": {
            "issuing_body": "College of Physicians and Surgeons (provincial)",
            "verification_url": "Provincial college registries (e.g., CPSO.org)",
            "typical_duration": "Annual",
            "renewal_requirements": ["CME/CPD", "license fee", "good standing"],
        },
        "lawyer": {
            "issuing_body": "Provincial Law Society (e.g., Law Society of Ontario)",
            "verification_url": "Provincial law society directories",
            "typical_duration": "Annual",
            "renewal_requirements": ["CPD hours", "annual fee", "insurance"],
        },
        "engineer_professional": {
            "issuing_body": "Professional Engineers Ontario (PEO) / provincial equivalent",
            "verification_url": "https://www.peo.on.ca/directory",
            "typical_duration": "Annual P.Eng. licence",
            "renewal_requirements": ["CPD", "annual fee", "ethics compliance"],
        },
    },
    "DE": {
        "driving": {
            "issuing_body": "Fuehrerscheinstelle (local driving licence authority)",
            "verification_url": "Local Buergeramt/Stadtverwaltung",
            "typical_duration": "15 years (EU card format since 2013)",
            "renewal_requirements": ["none for renewal before expiry", "medical exam if 50+"],
        },
        "medical_practitioner": {
            "issuing_body": "Landesaerztekammer (State Medical Association)",
            "verification_url": "https://www.bundesaerztekammer.de/arztinfo/arztauskunft/",
            "typical_duration": "Lifetime (Approbation), with continuing education",
            "renewal_requirements": ["CME points (250 over 5 years)", "no disciplinary issues"],
        },
        "lawyer": {
            "issuing_body": "Rechtsanwaltskammer (Regional Bar Association)",
            "verification_url": "https://www.brak.de/fuer-buerger/anwaltssuche/",
            "typical_duration": "Zulassung (admission) - lifetime",
            "renewal_requirements": ["Fachanwaltslehrgaenge (CPD)", "membership fee"],
        },
        "engineer_professional": {
            "issuing_body": "Ingenieurkammer (State Chamber of Engineers)",
            "verification_url": "https://www.bingk.de/ingenieursuche/",
            "typical_duration": "Annual membership",
            "renewal_requirements": ["CPD", "membership fee", "professional liability insurance"],
        },
    },
    "AU": {
        "driving": {
            "issuing_body": "State Road and Maritime Authority (e.g., Service NSW, VicRoads)",
            "verification_url": "State-specific driver licence check portals",
            "typical_duration": "1-10 years (varies by state)",
            "renewal_requirements": ["vision test", "medical (for commercial)"],
        },
        "medical_practitioner": {
            "issuing_body": "AHPRA (Australian Health Practitioner Regulation Agency)",
            "verification_url": "https://www.ahpra.gov.au/registration/registers-of-practitioners.aspx",
            "typical_duration": "Annual registration (by 30 November)",
            "renewal_requirements": ["CPD", "recency of practice", "indemnity insurance"],
        },
        "lawyer": {
            "issuing_body": "State Law Society (e.g., Law Society of NSW)",
            "verification_url": "State law society directories",
            "typical_duration": "Annual practising certificate",
            "renewal_requirements": ["CPD units (10/year)", "practising certificate fee"],
        },
    },
}

# ── Export license requirements (by country and goods category) ────────────

EXPORT_LICENSE_REQUIREMENTS: dict[str, dict[str, dict[str, Any]]] = {
    "US": {
        "military_items": {
            "regime": "ITAR (International Traffic in Arms Regulations)",
            "administering_body": "DDTC (Directorate of Defense Trade Controls), State Department",
            "requires_license": True,
            "classification_list": "US Munitions List (USML, 22 CFR 121)",
            "penalties": "Civil: up to $1.2M per violation; Criminal: up to 20 years imprisonment",
            "registration_required": True,
            "typical_processing": "30-60 days",
        },
        "dual_use": {
            "regime": "EAR (Export Administration Regulations)",
            "administering_body": "BIS (Bureau of Industry and Security), Commerce Department",
            "requires_license": True,
            "classification_list": "Commerce Control List (CCL, 15 CFR 774)",
            "penalties": "Civil: up to $300K per violation; Criminal: up to 20 years imprisonment",
            "registration_required": False,
            "typical_processing": "30-45 days",
            "exceptions": ["de minimis foreign content", "publicly available technology/software"],
        },
        "nuclear": {
            "regime": "Atomic Energy Act / NRC Regulations",
            "administering_body": "NRC (Nuclear Regulatory Commission) / DOE",
            "requires_license": True,
            "classification_list": "10 CFR 110",
            "penalties": "Up to $1M and 10 years imprisonment",
            "registration_required": True,
            "typical_processing": "60-180 days",
        },
        "encryption": {
            "regime": "EAR Category 5 Part 2",
            "administering_body": "BIS (Bureau of Industry and Security)",
            "requires_license": False,
            "classification_list": "5A002, 5D002 (encryption items)",
            "notes": "Generally license-exception ENC for mass-market encryption; notification/self-classification required",
            "typical_processing": "notification within 30 days of export",
        },
    },
    "GB": {
        "military_items": {
            "regime": "UK Strategic Export Controls / Open General Export Licences (OGELs)",
            "administering_body": "Export Control Joint Unit (ECJU), DIT",
            "requires_license": True,
            "classification_list": "UK Military List (aligned with EU Common Military List)",
            "penalties": "Up to 10 years imprisonment; unlimited fine",
            "registration_required": True,
            "typical_processing": "20-60 working days (SIEL); 20 days (OGEL registration)",
        },
        "dual_use": {
            "regime": "Retained EU Dual-Use Regulation (Regulation 2021/821 for NI)",
            "administering_body": "ECJU (Export Control Joint Unit)",
            "requires_license": True,
            "classification_list": "UK Dual-Use List",
            "penalties": "Up to 10 years imprisonment; revocation of export privileges",
            "registration_required": False,
            "typical_processing": "20-60 working days",
        },
    },
    "DE": {
        "military_items": {
            "regime": "Kriegswaffenkontrollgesetz (KrWaffKontrG) + AWG/AWV",
            "administering_body": "BAFA (Bundesamt fuer Wirtschaft und Ausfuhrkontrolle)",
            "requires_license": True,
            "classification_list": "Kriegswaffenliste (War Weapons List) + Common Military List",
            "penalties": "1-5 years imprisonment; substantial fines",
            "registration_required": True,
            "typical_processing": "30-90 days",
        },
        "dual_use": {
            "regime": "EU Dual-Use Regulation 2021/821",
            "administering_body": "BAFA",
            "requires_license": True,
            "classification_list": "EU Dual-Use List (Annex I)",
            "penalties": "Fines and imprisonment under AWG",
            "registration_required": False,
            "typical_processing": "30-60 working days",
        },
    },
    "CA": {
        "military_items": {
            "regime": "Export and Import Permits Act (EIPA) / Defence Production Act",
            "administering_body": "Global Affairs Canada (Export Controls Division)",
            "requires_license": True,
            "classification_list": "Export Control List (ECL) Group 2 (Munitions)",
            "penalties": "Fines up to $250,000 and/or 5 years imprisonment",
            "registration_required": True,
            "typical_processing": "4-8 weeks",
        },
        "dual_use": {
            "regime": "Export and Import Permits Act (EIPA)",
            "administering_body": "Global Affairs Canada",
            "requires_license": True,
            "classification_list": "Export Control List (ECL) Groups 1, 3-7",
            "penalties": "Fines up to $250,000 and/or 5 years imprisonment",
            "registration_required": False,
            "typical_processing": "4-6 weeks",
        },
    },
    "AU": {
        "military_items": {
            "regime": "Defence Trade Controls Act 2012 / Customs Act 1901",
            "administering_body": "Defence Export Controls (DEC), Department of Defence",
            "requires_license": True,
            "classification_list": "Defence and Strategic Goods List (DSGL) Part 1",
            "penalties": "Up to 10 years imprisonment; substantial fines",
            "registration_required": True,
            "typical_processing": "35 business days",
        },
        "dual_use": {
            "regime": "Customs Act 1901 / WMD Act 1995",
            "administering_body": "Defence Export Controls (DEC)",
            "requires_license": True,
            "classification_list": "DSGL Part 2",
            "penalties": "Fines up to A$2.5M and/or 10 years imprisonment",
            "registration_required": False,
            "typical_processing": "35 business days",
        },
    },
    "EU": {
        "dual_use": {
            "regime": "EU Dual-Use Regulation 2021/821",
            "administering_body": "National competent authorities of each Member State",
            "requires_license": True,
            "classification_list": "EU Dual-Use List (Annex I), extended by national lists",
            "penalties": "Determined by each Member State; typically includes imprisonment and fines",
            "registration_required": False,
            "typical_processing": "30-60 working days (varies by Member State)",
        },
    },
}

# ── License validity rules ─────────────────────────────────────────────────

LICENSE_VALIDITY_RULES: dict[str, dict[str, Any]] = {
    "driving": {
        "check_methods": ["online_portal", "issuing_authority_inquiry", "physical_document"],
        "verification_data_required": ["license_number", "issuing_state_province", "date_of_birth"],
        "common_restrictions": ["vision_correction_required", "daytime_only", "automatic_transmission_only"],
        "revocation_triggers": ["DUI", "excessive_points", "medical_disqualification"],
    },
    "medical_practitioner": {
        "check_methods": ["national_registry", "issuing_board_inquiry"],
        "verification_data_required": ["license_number", "issuing_state_province", "full_name"],
        "common_restrictions": ["supervised_practice", "limited_scope", "probationary"],
        "revocation_triggers": ["malpractice", "disciplinary_action", "criminal_conviction", "impairment"],
    },
    "lawyer": {
        "check_methods": ["bar_association_directory", "state_registry"],
        "verification_data_required": ["bar_number", "jurisdiction", "full_name"],
        "common_restrictions": ["inactive_status", "limited_scope", "suspended"],
        "revocation_triggers": ["disbarment", "disciplinary_action", "criminal_conviction", "failure_to_pay_dues"],
    },
    "pilot": {
        "check_methods": ["aviation_authority_registry", "logbook_review"],
        "verification_data_required": ["certificate_number", "medical_class", "issuing_authority"],
        "common_restrictions": ["VFR_only", "single_engine_only", "instrument_rating_required"],
        "revocation_triggers": ["medical_disqualification", "violation", "accident_history"],
    },
    "export_controlled": {
        "check_methods": ["export_control_authority", "consignee_screening", "end_use_verification"],
        "verification_data_required": ["license_number", "exporter_id", "commodity_classification"],
        "common_restrictions": ["destination_restriction", "end_use_restriction", "re_export_restriction"],
        "revocation_triggers": ["diversion", "unauthorized_re_export", "false_statement"],
    },
    "firearm": {
        "check_methods": ["national_firearms_registry", "police_inquiry"],
        "verification_data_required": ["license_number", "issuing_authority", "full_name"],
        "common_restrictions": ["restricted_class_only", "sporting_purpose_only", "storage_requirements"],
        "revocation_triggers": [
            "criminal_conviction",
            "domestic_violence_restraining_order",
            "mental_health_adjudication",
        ],
    },
}


# ── Functions ──────────────────────────────────────────────────────────────


def get_license_info(license_type: str, country: str) -> dict[str, Any] | None:
    """Return the registry and renewal info for a license type in a country."""
    ltype = license_type.strip().lower().replace(" ", "_").replace("-", "_")
    code = country.strip().upper()
    country_registries = LICENSE_REGISTRIES.get(code)
    if country_registries is None:
        return None
    entry = country_registries.get(ltype)
    if entry is None:
        # Try partial match
        for key, value in country_registries.items():
            if ltype in key or key in ltype:
                return {"license_type": key, "country": code, "found": True, **dict(value)}
        return None
    return {"license_type": ltype, "country": code, "found": True, **dict(entry)}


def get_export_license_requirements(country: str, goods_category: str) -> dict[str, Any] | None:
    """Return export control requirements for a goods category in a country.

    ``goods_category`` is one of: military_items, dual_use, nuclear, encryption.
    """
    code = country.strip().upper()
    country_exports = EXPORT_LICENSE_REQUIREMENTS.get(code)
    if country_exports is None:
        return None
    entry = country_exports.get(goods_category.strip().lower())
    if entry is None:
        return None
    return {"country": code, "goods_category": goods_category, **dict(entry)}


def check_license_validity(license_type: str, issuing_body: str, license_id: str) -> dict[str, Any]:
    """Return the verification procedure and data requirements for a license.

    This does NOT actually perform an online check — it returns the canonical
    verification method and required data for an agent to perform that check.
    """
    ltype = license_type.strip().lower().replace(" ", "_").replace("-", "_")
    rules = LICENSE_VALIDITY_RULES.get(ltype)
    if rules is None:
        return {
            "license_type": ltype,
            "verifiable": False,
            "note": f"No validity-check rules defined for '{ltype}'.",
        }
    return {
        "license_type": ltype,
        "issuing_body": issuing_body,
        "license_id": license_id,
        "verifiable": True,
        **dict(rules),
    }


def list_professions_for_country(country: str) -> list[str]:
    """Return the list of license types available for a country."""
    code = country.strip().upper()
    registries = LICENSE_REGISTRIES.get(code)
    if registries is None:
        return []
    return list(registries.keys())


def get_regulating_body(license_type: str, country: str) -> dict[str, Any] | None:
    """Return just the issuing/regulating body for a license type."""
    info = get_license_info(license_type, country)
    if info is None:
        return None
    return {
        "license_type": info["license_type"],
        "country": info["country"],
        "issuing_body": info["issuing_body"],
        "verification_url": info.get("verification_url", ""),
    }


__all__ = [
    "LICENSE_TYPES",
    "LICENSE_REGISTRIES",
    "EXPORT_LICENSE_REQUIREMENTS",
    "LICENSE_VALIDITY_RULES",
    "get_license_info",
    "get_export_license_requirements",
    "check_license_validity",
    "list_professions_for_country",
    "get_regulating_body",
]
