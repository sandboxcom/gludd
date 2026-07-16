"""
info_classification -- Information classification and access frameworks.

Provides national-security classification schemes, clearance hierarchies,
official government information sources, and freedom-of-information request
procedures across multiple jurisdictions.

Data structures:
    - CLASSIFICATION_LEVELS: unified hierarchy (public -> caveated)
    - CLASSIFICATION_BY_COUNTRY: per-jurisdiction native schemes
    - ACCESS_FRAMEWORKS: clearance, need-to-know, compartments, SAPs
    - INFO_SOURCES: gazettes, parliaments, courts, stats, banks, audit offices
    - FOIA_PROCESS: per-country FOI request procedures

Functions:
    get_classification_system(country) -> scheme dict or None
    get_access_requirements(level, country) -> requirements dict or None
    check_clearance_equiv(level_a, country_a, country_b) -> bool
    find_official_source(topic, country) -> source dict or None
    get_public_records_url(record_type, country) -> URL string or None
    get_foia_procedure(country) -> procedure dict or None
    file_foia_request_template(country, topic) -> template string or None
"""

from __future__ import annotations

from typing import Any

# ====================================================================
# CLASSIFICATION_LEVELS -- unified hierarchy
# ====================================================================

CLASSIFICATION_LEVELS: dict[str, dict[str, Any]] = {
    "public": {
        "rank": 0,
        "description": "Approved for unrestricted public release.",
        "handling": "No access controls; freely distributable.",
    },
    "unclassified": {
        "rank": 1,
        "description": "Not classified but may require limited dissemination.",
        "handling": "No security clearance required.",
    },
    "restricted": {
        "rank": 2,
        "description": "Dissemination limited; may include CUI, OFFICIAL-SENSITIVE, Protected A.",
        "handling": "Need-to-know; no formal clearance required.",
    },
    "confidential": {
        "rank": 3,
        "description": "Disclosure could cause damage to national security.",
        "handling": "Confidential clearance or equivalent required.",
    },
    "secret": {
        "rank": 4,
        "description": "Disclosure could cause serious damage to national security.",
        "handling": "Secret clearance required.",
    },
    "top_secret": {
        "rank": 5,
        "description": "Disclosure could cause exceptionally grave damage.",
        "handling": "Top Secret clearance + enhanced vetting required.",
    },
    "caveated": {
        "rank": 6,
        "description": "Beyond Top Secret; requires compartmented access (SCI, "
                       "COSMIC, NOFORN, or national-equivalent caveats).",
        "handling": "TS/SCI or equivalent; read-in to specific compartments "
                    "required; NOFORN restricts non-citizen access.",
    },
}

# ====================================================================
# CLASSIFICATION_BY_COUNTRY
# ====================================================================

CLASSIFICATION_BY_COUNTRY: dict[str, dict[str, Any]] = {
    "US": {
        "authority": "Executive Order 13526 (2009); 32 CFR 2001",
        "levels": [
            {"name": "Unclassified", "unified": "unclassified"},
            {"name": "CUI / Controlled Unclassified Information", "unified": "restricted"},
            {"name": "Confidential", "unified": "confidential"},
            {"name": "Secret", "unified": "secret"},
            {"name": "Top Secret", "unified": "top_secret"},
            {"name": "TS/SCI -- Top Secret/Sensitive Compartmented Information",
             "unified": "caveated", "caveats": ["SCI", "NOFORN", "ORCON"]},
        ],
    },
    "UK": {
        "authority": "Government Security Classification Policy (2018, Cabinet Office)",
        "levels": [
            {"name": "OFFICIAL", "unified": "unclassified"},
            {"name": "OFFICIAL-SENSITIVE", "unified": "restricted"},
            {"name": "SECRET", "unified": "secret"},
            {"name": "TOP SECRET", "unified": "top_secret"},
        ],
    },
    "CA": {
        "authority": "Treasury Board Standard on Security Classification (Canada)",
        "levels": [
            {"name": "UNCLASSIFIED", "unified": "unclassified"},
            {"name": "PROTECTED A", "unified": "restricted"},
            {"name": "PROTECTED B", "unified": "confidential"},
            {"name": "PROTECTED C", "unified": "confidential"},
            {"name": "SECRET", "unified": "secret"},
            {"name": "TOP SECRET", "unified": "top_secret"},
        ],
    },
    "AU": {
        "authority": "Australian Government Security Classification System (AGSCS, PSPF)",
        "levels": [
            {"name": "UNCLASSIFIED", "unified": "unclassified"},
            {"name": "PROTECTED", "unified": "restricted"},
            {"name": "CONFIDENTIAL", "unified": "confidential"},
            {"name": "SECRET", "unified": "secret"},
            {"name": "TOP SECRET", "unified": "top_secret"},
        ],
    },
    "FR": {
        "authority": "Code de la defense (art. R*413-1 et seq.); IGCN",
        "levels": [
            {"name": "Diffusion restreinte", "unified": "restricted"},
            {"name": "Confidentiel de la defense nationale", "unified": "confidential"},
            {"name": "Secret de la defense nationale", "unified": "secret"},
            {"name": "Tres secret de la defense nationale (TSD)",
             "unified": "caveated", "caveats": ["TSD", "SPECIALEMENT"]},
        ],
    },
    "DE": {
        "authority": "VS-Verschlusssachen-Anweisung (VSA); BSI",
        "levels": [
            {"name": "VS-NfD (Verschlusssache -- nur fuer den Dienstgebrauch)",
             "unified": "restricted"},
            {"name": "VS-VERTRAULICH", "unified": "confidential"},
            {"name": "GEHEIM", "unified": "secret"},
            {"name": "STRENG GEHEIM", "unified": "top_secret"},
        ],
    },
    "RU": {
        "authority": "Federal Law 'On State Secrets' (FZ-5485-1, 1993, as amended)",
        "levels": [
            {"name": "Ne secret (Not secret)", "unified": "unclassified"},
            {"name": "DSP (For official use only)", "unified": "restricted"},
            {"name": "Secretno (Secret)", "unified": "secret"},
            {"name": "Sovershenno secretno (Top Secret)", "unified": "top_secret"},
            {"name": "Osoboy vazhnosti (Of special importance)",
             "unified": "caveated"},
        ],
    },
    "CN": {
        "authority": "Law of the People's Republic of China on Guarding State Secrets (2010)",
        "levels": [
            {"name": "Gongkai (Public)", "unified": "public"},
            {"name": "Mimi (Secret)", "unified": "secret"},
            {"name": "Jimi (Confidential / Machine-secret)", "unified": "confidential"},
            {"name": "Juemi (Top secret / Absolute secret)", "unified": "top_secret"},
        ],
    },
    "EU": {
        "authority": "Council Decision 2013/488/EU on security rules for classified info (EUCI)",
        "levels": [
            {"name": "EU RESTRICTED", "unified": "restricted"},
            {"name": "EU CONFIDENTIAL", "unified": "confidential"},
            {"name": "EU SECRET", "unified": "secret"},
            {"name": "EU TOP SECRET", "unified": "top_secret"},
        ],
    },
    "NATO": {
        "authority": "Security Within the North Atlantic Treaty Organisation (C-M(2002)49)",
        "levels": [
            {"name": "NATO UNCLASSIFIED", "unified": "unclassified"},
            {"name": "NATO RESTRICTED", "unified": "restricted"},
            {"name": "NATO CONFIDENTIAL", "unified": "confidential"},
            {"name": "NATO SECRET", "unified": "secret"},
            {"name": "COSMIC (MOST SECRET)",
             "unified": "caveated", "caveats": ["COSMIC", "ATOMAL", "BOHEMIA"]},
        ],
    },
}

# ====================================================================
# ACCESS_FRAMEWORKS
# ====================================================================

ACCESS_FRAMEWORKS: dict[str, Any] = {
    "clearance_levels": {
        "US": [
            {"level": "Confidential", "rank": 3, "investigation": "NACLC"},
            {"level": "Secret", "rank": 4, "investigation": "NACLC / Tier 3"},
            {"level": "Top Secret", "rank": 5, "investigation": "SSBI / Tier 5"},
            {"level": "TS/SCI", "rank": 6, "investigation": "SSBI-PR + polygraph"},
        ],
        "UK": [
            {"level": "CTC (Counter-Terrorist Check)", "rank": 2},
            {"level": "SC (Security Clearance)", "rank": 4},
            {"level": "DV (Developed Vetting)", "rank": 5},
            {"level": "Enhanced DV / STRAP", "rank": 6},
        ],
        "CA": [
            {"level": "Reliability Status", "rank": 2},
            {"level": "Secret", "rank": 4},
            {"level": "Top Secret", "rank": 5},
        ],
        "AU": [
            {"level": "Baseline", "rank": 1},
            {"level": "Negative Vetting 1 (Secret)", "rank": 4},
            {"level": "Negative Vetting 2 (Top Secret)", "rank": 5},
            {"level": "Positive Vetting", "rank": 6},
        ],
        "NATO": [
            {"level": "NATO SECRET clearance", "rank": 4},
            {"level": "COSMIC clearance", "rank": 6},
        ],
    },
    "need_to_know": {
        "principle": "Access to classified information requires both the appropriate "
                     "clearance AND a demonstrated need-to-know for the specific "
                     "information being sought.",
        "enforcement": "Discretionary by information originator or classification guide; "
                       "not automatically granted by clearance level alone.",
        "waivers": "Original Classification Authority (OCA) may waive need-to-know "
                   "for specific individuals or roles.",
    },
    "compartments": {
        "description": "SCI (Sensitive Compartmented Information) compartments restrict "
                       "access beyond the base clearance level.",
        "US_SCI_compartments": [
            "HCS -- HUMINT Control System (human intelligence)",
            "SI -- Special Intelligence (SIGINT, includes COMINT/ELINT)",
            "TK -- TALENT KEYHOLE (imagery from reconnaissance satellites)",
            "G -- GAMMA (intercepted foreign communications, COMINT subset)",
            "U -- UMBRA (highest-level COMINT, legacy)",
            "K -- KLONDIKE (special SIGINT handling)",
            "NOFORN -- Not releasable to foreign nationals",
            "ORCON -- Originator Controlled distribution",
            "FDO -- Foreign Disclosure Office approved",
        ],
        "NATO_compartments": [
            "ATOMAL -- Nuclear weapons information shared US/NATO",
            "BOHEMIA -- Special intelligence handling",
            "COSMIC -- Council-graded most-secret material",
        ],
    },
    "special_access_programs": {
        "description": "SAPs (Special Access Programs) are highly restricted programs "
                       "requiring formal read-in beyond SCI.",
        "categories": [
            "Acquisition / Procurement SAPs (AAP)",
            "Intelligence SAPs (SAP-SI)",
            "Operations and Support SAPs (O&S-SAP)",
        ],
        "US_authority": "Under Secretary of Defense for Intelligence & Security (USD(I&S))",
        "examples": [
            "F-117 / Have Blue (stealth, historical)",
            "Senior Trend (historical)",
            "Classified ISR programs",
        ],
    },
}

# ====================================================================
# INFO_SOURCES
# ====================================================================

INFO_SOURCES: dict[str, dict[str, Any]] = {
    "official_gazettes": {
        "US": {
            "name": "Federal Register",
            "url": "https://www.federalregister.gov",
            "description": "Daily journal of the US federal government; rules, "
                           "proposed rules, public notices, executive orders.",
        },
        "UK": {
            "name": "The London Gazette",
            "url": "https://www.thegazette.co.uk",
            "description": "Official public record of the UK; state, legal, "
                           "and regulatory notices.",
        },
        "CA": {
            "name": "Canada Gazette",
            "url": "https://gazette.gc.ca",
            "description": "Official newspaper of the Government of Canada.",
        },
        "AU": {
            "name": "Commonwealth of Australia Gazette",
            "url": "https://www.legislation.gov.au",
            "description": "Official government notices for Australia.",
        },
        "FR": {
            "name": "Journal Officiel de la Republique Francaise",
            "url": "https://www.legifrance.gouv.fr",
            "description": "Official gazette of the French Republic; laws, "
                           "decrees, and public notices.",
        },
        "DE": {
            "name": "Bundesanzeiger",
            "url": "https://www.bundesanzeiger.de",
            "description": "Federal Gazette of Germany.",
        },
        "EU": {
            "name": "Official Journal of the European Union",
            "url": "https://eur-lex.europa.eu",
            "description": "EU legislation, regulations, and public notices.",
        },
    },
    "parliamentary_records": {
        "US": {
            "name": "Congressional Record",
            "url": "https://www.congress.gov/congressional-record",
            "description": "Official record of proceedings of the US Congress.",
        },
        "UK": {
            "name": "Hansard",
            "url": "https://hansard.parliament.uk",
            "description": "Official report of parliamentary debates, House of "
                           "Commons and House of Lords.",
        },
        "CA": {
            "name": "House of Commons Debates",
            "url": "https://www.ourcommons.ca/DocumentViewer/en/44-1/hansard",
            "description": "Official record of Canadian parliamentary debates.",
        },
        "AU": {
            "name": "ParlInfo (Hansard)",
            "url": "https://parlinfo.aph.gov.au",
            "description": "Parliamentary debates and records of Australia.",
        },
        "FR": {
            "name": "Journal officiel -- Debats parlementaires",
            "url": "https://www.assemblee-nationale.fr",
            "description": "Debates of the French National Assembly and Senate.",
        },
    },
    "court_records": {
        "US": {
            "name": "PACER (Public Access to Court Electronic Records)",
            "url": "https://pacer.uscourts.gov",
            "description": "Federal court case and docket information.",
        },
        "UK": {
            "name": "BAILII (British and Irish Legal Information Institute)",
            "url": "https://www.bailii.org",
            "description": "Judgments of UK and Irish courts, free access.",
        },
        "CA": {
            "name": "CanLII",
            "url": "https://www.canlii.org",
            "description": "Canadian Legal Information Institute -- court decisions.",
        },
        "AU": {
            "name": "AustLII",
            "url": "https://www.austlii.edu.au",
            "description": "Australasian Legal Information Institute.",
        },
        "EU": {
            "name": "CURIA -- Court of Justice of the EU",
            "url": "https://curia.europa.eu",
            "description": "Judgments of the CJEU and General Court.",
        },
    },
    "statistics_offices": {
        "US": {
            "name": "Bureau of Labor Statistics / Census Bureau",
            "url": "https://www.bls.gov",
            "description": "Economic, employment, and demographic statistics. "
                           "Census: https://www.census.gov",
        },
        "UK": {
            "name": "Office for National Statistics (ONS)",
            "url": "https://www.ons.gov.uk",
            "description": "UK official statistics on economy, population, society.",
        },
        "CA": {
            "name": "Statistics Canada",
            "url": "https://www.statcan.gc.ca",
            "description": "National statistical agency of Canada.",
        },
        "FR": {
            "name": "INSEE",
            "url": "https://www.insee.fr",
            "description": "National Institute of Statistics and Economic Studies.",
        },
        "DE": {
            "name": "Destatis (Statistisches Bundesamt)",
            "url": "https://www.destatis.de",
            "description": "Federal Statistical Office of Germany.",
        },
        "AU": {
            "name": "Australian Bureau of Statistics (ABS)",
            "url": "https://www.abs.gov.au",
            "description": "National statistical agency of Australia.",
        },
    },
    "central_banks": {
        "US": {
            "name": "Federal Reserve System",
            "url": "https://www.federalreserve.gov",
            "description": "Central bank of the United States; monetary policy, "
                           "banking supervision, economic research.",
        },
        "UK": {
            "name": "Bank of England",
            "url": "https://www.bankofengland.co.uk",
            "description": "Central bank of the United Kingdom.",
        },
        "CA": {
            "name": "Bank of Canada",
            "url": "https://www.bankofcanada.ca",
            "description": "Central bank of Canada.",
        },
        "AU": {
            "name": "Reserve Bank of Australia",
            "url": "https://www.rba.gov.au",
            "description": "Central bank of Australia.",
        },
        "FR": {
            "name": "Banque de France / European Central Bank",
            "url": "https://www.banque-france.fr",
            "description": "French central bank; ECB at https://www.ecb.europa.eu",
        },
        "DE": {
            "name": "Deutsche Bundesbank",
            "url": "https://www.bundesbank.de",
            "description": "Central bank of Germany.",
        },
        "EU": {
            "name": "European Central Bank (ECB)",
            "url": "https://www.ecb.europa.eu",
            "description": "Central bank for the eurozone.",
        },
    },
    "audit_offices": {
        "US": {
            "name": "Government Accountability Office (GAO)",
            "url": "https://www.gao.gov",
            "description": "Audit, evaluation, and investigation arm of Congress.",
        },
        "UK": {
            "name": "National Audit Office (NAO)",
            "url": "https://www.nao.org.uk",
            "description": "Independent parliamentary spending watchdog.",
        },
        "CA": {
            "name": "Office of the Auditor General of Canada",
            "url": "https://www.oag-bvg.gc.ca",
            "description": "Federal audit office of Canada.",
        },
        "AU": {
            "name": "Australian National Audit Office (ANAO)",
            "url": "https://www.anao.gov.au",
            "description": "Federal audit office of Australia.",
        },
        "FR": {
            "name": "Cour des comptes",
            "url": "https://www.ccomptes.fr",
            "description": "Supreme audit institution of France.",
        },
        "DE": {
            "name": "Bundesrechnungshof",
            "url": "https://www.bundesrechnungshof.de",
            "description": "Federal Court of Audit of Germany.",
        },
        "EU": {
            "name": "European Court of Auditors",
            "url": "https://www.eca.europa.eu",
            "description": "EU institution for auditing EU finances.",
        },
    },
}

# ====================================================================
# FOIA_PROCESS
# ====================================================================

FOIA_PROCESS: dict[str, dict[str, Any]] = {
    "US": {
        "law": "Freedom of Information Act (FOIA), 5 U.S.C. section 552",
        "authority": "Agency FOIA Public Liaison; Office of Government Information Services (OGIS)",
        "response_time_days": 20,
        "fee": "Variable; fee categories for commercial, news media, educational, and "
               "all-other requesters. First 100 pages / 2 hours free for non-commercial.",
        "exemptions": 9,
        "exemption_summary": "National security, internal personnel rules, trade secrets, "
                             "law enforcement, personal privacy, financial institution "
                             "records, geological information.",
        "appeal": "Administrative appeal within 90 days; judicial review available.",
        "portal": "https://www.foia.gov",
        "steps": [
            "Identify the federal agency holding the records.",
            "Draft a written request describing the records sought (reasonably described).",
            "Submit via the agency's FOIA portal, email, or mail.",
            "Specify fee category and willingness to pay (or request fee waiver).",
            "Agency responds within 20 business days (extensions for complex/unusual).",
            "If denied or unsatisfied, file administrative appeal within 90 days.",
            "If appeal denied, seek judicial review in federal district court.",
        ],
    },
    "UK": {
        "law": "Freedom of Information Act 2000 (FOIA)",
        "authority": "Information Commissioner's Office (ICO)",
        "response_time_days": 20,
        "fee": "Cost limit 600 GBP (central government) / 450 GBP (other public authorities); "
               "discretionary disbursement costs below threshold.",
        "exemptions": 23,
        "exemption_summary": "Absolute exemptions (security bodies, court records, "
                             "personal data) and qualified exemptions (prejudice test, "
                             "public interest balance).",
        "appeal": "Internal review first; then ICO complaint; then Information Tribunal.",
        "portal": "https://ico.org.uk",
        "steps": [
            "Identify the public authority holding the information.",
            "Submit request in writing (letter or email) with name and contact.",
            "Specify whether seeking recorded information.",
            "Authority must respond within 20 working days.",
            "If refused, request internal review.",
            "If unsatisfied, complain to the ICO.",
            "Further appeal to First-tier Tribunal (Information Rights).",
        ],
    },
    "CA": {
        "law": "Access to Information Act (R.S.C., 1985, c. A-1)",
        "authority": "Office of the Information Commissioner of Canada",
        "response_time_days": 30,
        "fee": "5.00 CAD application fee; additional charges for production over 5 hours.",
        "exemptions": "Sections 13-24: international affairs, defence, law enforcement, "
                      "personal information, third-party commercial info.",
        "appeal": "Complaint to Information Commissioner; judicial review to Federal Court.",
        "portal": "https://www.canada.ca/en/treasury-board-secretariat/services/access-information-privacy.html",
        "steps": [
            "Identify the federal institution.",
            "Complete Access to Information Request Form (TBS/SCT 350-40).",
            "Include 5.00 CAD application fee.",
            "Submit to the institution's Access to Information Coordinator.",
            "Response within 30 calendar days (extension notice possible).",
            "Complaint to Information Commissioner if dissatisfied.",
            "Application to Federal Court for review if warranted.",
        ],
    },
    "AU": {
        "law": "Freedom of Information Act 1982 (Cth)",
        "authority": "Office of the Australian Information Commissioner (OAIC)",
        "response_time_days": 30,
        "fee": "No application fee; processing charges may apply for non-personal requests.",
        "exemptions": "Sections 30-47: Cabinet documents, national security, "
                      "law enforcement, business affairs, personal privacy.",
        "appeal": "Internal review (IC review); then AAT review.",
        "portal": "https://www.oaic.gov.au",
        "steps": [
            "Identify the agency or minister.",
            "Submit request in writing; specify documents sought.",
            "Agency must respond within 30 days.",
            "Request internal review if refused.",
            "Apply to OAIC for IC review.",
            "Further appeal to Administrative Appeals Tribunal (AAT).",
        ],
    },
    "FR": {
        "law": "Loi n 78-753 du 17 juillet 1978 (Loi CADA)",
        "authority": "Commission d'acces aux documents administratifs (CADA)",
        "response_time_days": 30,
        "fee": "Generally free; reproduction costs may apply.",
        "exemptions": "Secret protege, vie privee, secret des affaires, "
                      "surete de l'Etat.",
        "appeal": "CADA opinion; then tribunal administratif.",
        "portal": "https://www.cada.fr",
        "steps": [
            "Identify the administration holding the document.",
            "Request in writing (email or mail).",
            "Administration responds within 30 days (2 months extension possible).",
            "If refused, seek CADA opinion (non-binding recommendation).",
            "Appeal to tribunal administratif if unresolved.",
        ],
    },
    "DE": {
        "law": "Informationsfreiheitsgesetz (IFG, 2006)",
        "authority": "Bundesbeauftragter fuer den Datenschutz (BfDI)",
        "response_time_days": 30,
        "fee": "Gebuehrenordnung; partial cost recovery for non-personal requests.",
        "exemptions": "International relations, defence, military security, "
                      "ongoing decision-making, personal data.",
        "appeal": "Widerspruch (administrative objection); then Verwaltungsgericht.",
        "portal": "https://www.bfdi.bund.de",
        "steps": [
            "Identify the federal authority.",
            "Submit Antrag in writing.",
            "Authority responds within 1 month (extension to 3 months possible).",
            "File Widerspruch if refused.",
            "Klage at Verwaltungsgericht if unresolved.",
        ],
    },
}

# ====================================================================
# Functions
# ====================================================================


def get_classification_system(country: str) -> dict[str, Any] | None:
    """Return the classification scheme for a country code.

    Returns None for unknown countries.
    """
    return CLASSIFICATION_BY_COUNTRY.get(country)


def get_access_requirements(level: str, country: str) -> dict[str, Any] | None:
    """Return access requirements for a unified classification level in a country.

    Combines the unified level's handling requirements with the country's
    native clearance structure.
    """
    unified = CLASSIFICATION_LEVELS.get(level)
    if unified is None:
        return None
    scheme = CLASSIFICATION_BY_COUNTRY.get(country)
    if scheme is None:
        return None
    clearances = ACCESS_FRAMEWORKS["clearance_levels"].get(country, [])
    return {
        "level": level,
        "rank": unified["rank"],
        "country": country,
        "handling": unified["handling"],
        "description": unified["description"],
        "clearance_required": unified["rank"] >= 3,
        "native_levels": [
            lv for lv in scheme["levels"] if lv.get("unified") == level
        ],
        "country_clearances": clearances,
    }


# Cross-country equivalence table (unified rank -> equivalent native level)
_EQUIV_OVERRIDES: dict[tuple[str, str, str], bool] = {
    # Explicit non-equivalences (no sharing agreements at caveated levels)
    ("top_secret", "RU", "NATO"): False,
    ("top_secret", "CN", "NATO"): False,
    ("caveated", "US", "FR"): False,
}


def check_clearance_equiv(
    level_a: str, country_a: str, country_b: str
) -> bool:
    """Check whether a clearance level in country_a is recognized in country_b.

    Based on the unified classification rank and documented sharing arrangements.
    Returns False for unknown levels or non-shareable combinations.
    """
    unified = CLASSIFICATION_LEVELS.get(level_a)
    if unified is None:
        return False
    if country_a not in CLASSIFICATION_BY_COUNTRY:
        return False
    if country_b not in CLASSIFICATION_BY_COUNTRY:
        return False

    override = _EQUIV_OVERRIDES.get((level_a, country_a, country_b))
    if override is not None:
        return override

    # Public and unclassified are universally shareable
    if unified["rank"] <= 1:
        return True

    # NATO members share at equivalent levels (US/UK/CA/AU/FR/DE/EU)
    nato_allies = {"US", "UK", "CA", "AU", "FR", "DE", "EU", "NATO"}
    if country_a in nato_allies and country_b in nato_allies:
        # Restricted/confidential/secret/top_secret are shareable among allies
        if 2 <= unified["rank"] <= 5:
            return True

    # Caveated information requires bilateral agreements
    if unified["rank"] >= 6:
        return False

    return False


def find_official_source(topic: str, country: str) -> dict[str, Any] | None:
    """Find an official information source by topic keyword and country.

    Args:
        topic: keyword to search across INFO_SOURCES categories
            (e.g., 'gazette', 'court', 'parliament', 'statistics', 'bank', 'audit').
        country: ISO-style country code (US, UK, FR, etc.).

    Returns the first matching source dict, or None.
    """
    topic_lower = topic.lower()
    for category, countries in INFO_SOURCES.items():
        if topic_lower in category.lower() or topic_lower in category.replace("_", " "):
            entry = countries.get(country)
            if entry is not None:
                return {"category": category, **entry}
    return None


def get_public_records_url(record_type: str, country: str) -> str | None:
    """Return the official URL for a public records type in a country.

    Args:
        record_type: one of 'gazette', 'parliament', 'court', 'statistics',
            'bank', 'audit' (or the full INFO_SOURCES category name).
        country: country code.

    Returns the URL string, or None if not found.
    """
    aliases: dict[str, str] = {
        "gazette": "official_gazettes",
        "parliament": "parliamentary_records",
        "parliamentary": "parliamentary_records",
        "court": "court_records",
        "statistics": "statistics_offices",
        "stats": "statistics_offices",
        "bank": "central_banks",
        "central_bank": "central_banks",
        "audit": "audit_offices",
    }
    category = aliases.get(record_type.lower(), record_type.lower())
    sources = INFO_SOURCES.get(category)
    if sources is None:
        return None
    entry = sources.get(country)
    if entry is None:
        return None
    url = entry.get("url")
    if isinstance(url, str):
        return url
    return None


def get_foia_procedure(country: str) -> dict[str, Any] | None:
    """Return the FOI request procedure for a country, or None."""
    return FOIA_PROCESS.get(country)


def file_foia_request_template(country: str, topic: str) -> str | None:
    """Generate a FOI request letter template for a country and topic.

    Returns a formatted request string, or None if the country is unsupported.
    """
    proc = FOIA_PROCESS.get(country)
    if proc is None:
        return None

    if country == "US":
        return (
            f"Freedom of Information Act Request\n\n"
            f"To: FOIA Officer\n"
            f"[Agency Name]\n"
            f"[Agency Address]\n\n"
            f"Date: [DATE]\n\n"
            f"Under the Freedom of Information Act (FOIA), 5 U.S.C. section 552, "
            f"I hereby request the following records:\n\n"
            f"    All records pertaining to: {topic}\n\n"
            f"This request is submitted under FOIA. I am a representative of "
            f"the news media / educational institution / all-other requester "
            f"(select one) and request a fee waiver or reduction. I am willing "
            f"to pay up to $[AMOUNT] in fees; please contact me before "
            f"exceeding that amount.\n\n"
            f"Please provide the records in electronic format. If any portion "
            f"of this request is denied, please cite the specific FOIA "
            f"exemption and provide an index of withheld records.\n\n"
            f"Sincerely,\n[YOUR NAME]\n[CONTACT INFORMATION]"
        )

    if country == "UK":
        return (
            f"Freedom of Information Act 2000 Request\n\n"
            f"To: [Public Authority]\n"
            f"[Address]\n\n"
            f"Date: [DATE]\n\n"
            f"Under the Freedom of Information Act 2000 (FOIA), I hereby "
            f"request the following recorded information:\n\n"
            f"    All recorded information relating to: {topic}\n\n"
            f"I would prefer to receive the information electronically. If my "
            f"request is refused or partially refused, please cite the "
            f"applicable exemption and explain the public interest test.\n\n"
            f"If the cost of complying exceeds the appropriate limit, please "
            f"advise how I may refine my request.\n\n"
            f"Sincerely,\n[YOUR NAME]\n[CONTACT INFORMATION]"
        )

    if country == "CA":
        return (
            f"Access to Information Act Request\n\n"
            f"To: Access to Information Coordinator\n"
            f"[Institution Name]\n[Address]\n\n"
            f"Date: [DATE]\n\n"
            f"Under the Access to Information Act (R.S.C., 1985, c. A-1), I "
            f"hereby request access to the following records:\n\n"
            f"    Records relating to: {topic}\n\n"
            f"Enclosed is the 5.00 CAD application fee. Please provide records "
            f"in electronic format where possible.\n\n"
            f"Requester: [YOUR NAME]\n[CONTACT INFORMATION]"
        )

    # Generic template for other countries
    law = proc.get("law", "applicable freedom of information law")
    return (
        f"Freedom of Information Request\n\n"
        f"To: [Authority]\n[Address]\n\n"
        f"Date: [DATE]\n\n"
        f"Under {law}, I hereby request access to records relating to:\n\n"
        f"    {topic}\n\n"
        f"Please provide the records in electronic format where available.\n\n"
        f"Sincerely,\n[YOUR NAME]\n[CONTACT INFORMATION]"
    )
