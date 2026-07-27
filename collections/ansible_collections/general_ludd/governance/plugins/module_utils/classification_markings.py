"""Classification markings — cross-system visual-label map for the governance collection.

Exposes banner line formats, portion-marking conventions, caveat codes, and
dissemination control markings across US, UK, NATO, and EU classification systems.

Public surface::

    BANNER_FORMATS        dict of system -> {level: banner line template}
    PORTION_MARKINGS      dict of system -> portion-marking spec
    CAVEAT_CODES          dict of caveat code -> {description, systems_applicable}
    DISSEM_CONTROLS       dict of control code -> {description, systems_applicable}
    DECLASS_SCHEDULES    dict of system -> declassification schedule templates

    get_banner_line(system, level, caveats=None)  -> banner line string or None
    get_portion_marking(system, level, caveats=None) -> portion marking string or None
    resolve_caveat(code)  -> caveat dict or None
    get_dissem_control(code) -> dissem control dict or None
"""

from __future__ import annotations

from typing import Any

# ── Banner line formats (overall classification of a document) ───────────
#
# Each entry maps a classification level to a template string. Caveats
# are substituted via `{caveats}` placeholder.

BANNER_FORMATS: dict[str, dict[str, str]] = {
    "US": {
        "public": "UNCLASSIFIED",
        "unclassified": "UNCLASSIFIED",
        "restricted": "CONTROLLED UNCLASSIFIED INFORMATION (CUI)",
        "confidential": "CONFIDENTIAL",
        "secret": "SECRET",
        "top_secret": "TOP SECRET",
        "caveated": "TOP SECRET//{caveats}",
    },
    "UK": {
        "public": "OFFICIAL",
        "unclassified": "OFFICIAL",
        "restricted": "OFFICIAL-SENSITIVE",
        "confidential": "OFFICIAL-SENSITIVE",
        "secret": "UK SECRET",
        "top_secret": "UK TOP SECRET",
        "caveated": "UK TOP SECRET {caveats}",
    },
    "NATO": {
        "public": "NATO UNCLASSIFIED",
        "unclassified": "NATO UNCLASSIFIED",
        "restricted": "NATO RESTRICTED",
        "confidential": "NATO CONFIDENTIAL",
        "secret": "NATO SECRET",
        "top_secret": "NATO SECRET",
        "caveated": "COSMIC TOP SECRET//{caveats}",
    },
    "EU": {
        "public": "NON CLASSIFIE",
        "unclassified": "NON CLASSIFIE",
        "restricted": "RESTREINT UE/EU RESTRICTED",
        "confidential": "CONFIDENTIEL UE/EU CONFIDENTIAL",
        "secret": "SECRET UE/EU SECRET",
        "top_secret": "TRES SECRET UE/EU TOP SECRET",
        "caveated": "TRES SECRET UE/EU TOP SECRET//{caveats}",
    },
    "FR": {
        "restricted": "DIFFUSION RESTREINTE",
        "confidential": "CONFIDENTIEL DEFENSE",
        "secret": "SECRET DEFENSE",
        "top_secret": "TRES SECRET DEFENSE",
        "caveated": "TRES SECRET DEFENSE//{caveats}",
    },
    "DE": {
        "restricted": "VS-NUR FUER DEN DIENSTGEBRAUCH",
        "confidential": "VS-VERTRAULICH",
        "secret": "GEHEIM",
        "top_secret": "STRENG GEHEIM",
        "caveated": "STRENG GEHEIM//{caveats}",
    },
    "CA": {
        "unclassified": "UNCLASSIFIED",
        "restricted": "PROTECTED A",
        "confidential": "PROTECTED B",
        "secret": "SECRET",
        "top_secret": "TOP SECRET",
        "caveated": "TOP SECRET//{caveats}",
    },
    "AU": {
        "unclassified": "UNCLASSIFIED",
        "restricted": "PROTECTED",
        "confidential": "CONFIDENTIAL",
        "secret": "SECRET",
        "top_secret": "TOP SECRET",
        "caveated": "TOP SECRET//{caveats}",
    },
}

# ── Portion marking conventions ──────────────────────────────────────────
#
# Each entry describes how to mark individual paragraphs, sections, or
# attachments with their portion classification and caveats.

PORTION_MARKINGS: dict[str, dict[str, Any]] = {
    "US": {
        "convention": "Portion marking precedes the text: (U), (C), (S), (TS). "
        "Caveats appended: (TS//SI), (S//REL TO USA, AUS). "
        "Subject/title lines inherit the overall classification.",
        "prefix": True,
        "delimiter": "//",
        "examples": [
            "(U) This is an unclassified paragraph.",
            "(S//SI) This is a SECRET paragraph with SI caveat.",
            "(TS//SI//NF) TOP SECRET with SI and NOFORN caveats.",
        ],
    },
    "UK": {
        "convention": "UK portion marks use the classification word: OFFICIAL, "
        "OFFICIAL-SENSITIVE, SECRET, TOP SECRET. "
        "Caveats follow: UK TOP SECRET STRAP1.",
        "prefix": True,
        "delimiter": " ",
        "examples": [
            "OFFICIAL: Routine correspondence.",
            "SECRET: Sensible operational detail.",
            "UK TOP SECRET STRAP1: Compartmented intelligence.",
        ],
    },
    "NATO": {
        "convention": "Portion marks use NATO classification words: "
        "NATO UNCLASSIFIED, NATO RESTRICTED, NATO CONFIDENTIAL, "
        "NATO SECRET, COSMIC TOP SECRET. "
        "Caveats appended with //: COSMIC TOP SECRET//ATOMAL.",
        "prefix": True,
        "delimiter": "//",
        "examples": [
            "NATO UNCLASSIFIED: Public information.",
            "NATO SECRET: Operational plans.",
            "COSMIC TOP SECRET//ATOMAL: Nuclear weapons information.",
        ],
    },
    "EU": {
        "convention": "EU portion marks use the classification abbreviation: "
        "NC (non classifie), RESTREINT, CONFIDENTIEL, SECRET, "
        "TRES SECRET. Bilingual format common: "
        "RESTREINT UE/EU RESTRICTED.",
        "prefix": True,
        "delimiter": " ",
        "examples": [
            "RESTREINT UE/EU RESTRICTED: Internal EU memo.",
            "SECRET UE/EU SECRET: Classified Council document.",
            "TRES SECRET UE/EU TOP SECRET: Highly sensitive intelligence.",
        ],
    },
}

# ── Caveat codes (additional restrictions on dissemination) ──────────────
#
# These are the standard codes that appear after the classification level.

CAVEAT_CODES: dict[str, dict[str, Any]] = {
    "NOFORN": {
        "description": "Not releasable to foreign nationals.",
        "systems": ["US", "NATO"],
        "full_name": "No Foreign Nationals",
    },
    "NF": {
        "description": "Not releasable to foreign nationals (abbreviated).",
        "systems": ["US", "NATO"],
        "full_name": "No Foreign (equivalent to NOFORN)",
    },
    "ORCON": {
        "description": "Originator-controlled dissemination. Recipients "
        "must obtain originator approval before further "
        "dissemination.",
        "systems": ["US", "NATO"],
        "full_name": "Originator Controlled",
    },
    "REL TO USA": {
        "description": "Releasable only to the United States.",
        "systems": ["US", "NATO"],
        "full_name": "Releasable to USA",
    },
    "REL TO USA, AUS, GBR": {
        "description": "Releasable to the Five Eyes partnership: US, Australia, UK. Other partners added as specified.",
        "systems": ["US", "NATO"],
        "full_name": "Releasable to USA, Australia, United Kingdom",
    },
    "REL TO USA, AUS, CAN, GBR, NZL": {
        "description": "Releasable to all Five Eyes partners.",
        "systems": ["US", "NATO"],
        "full_name": "Releasable to Five Eyes",
    },
    "SI": {
        "description": "Special Intelligence — SIGINT compartment.",
        "systems": ["US"],
        "full_name": "Special Intelligence (SIGINT)",
    },
    "HCS": {
        "description": "HUMINT Control System — human intelligence compartment.",
        "systems": ["US"],
        "full_name": "HUMINT Control System",
    },
    "TK": {
        "description": "TALENT KEYHOLE — imagery intelligence from reconnaissance satellites.",
        "systems": ["US"],
        "full_name": "TALENT KEYHOLE (IMINT)",
    },
    "FOUO": {
        "description": "For Official Use Only — limited dissemination within government channels.",
        "systems": ["US"],
        "full_name": "For Official Use Only",
    },
    "COSMIC": {
        "description": "NATO Council-graded most-secret material.",
        "systems": ["NATO"],
        "full_name": "COSMIC (Council Secret)",
    },
    "ATOMAL": {
        "description": "Nuclear weapons information shared under the "
        "US-UK-NATO agreement for cooperation on atomic "
        "information.",
        "systems": ["NATO"],
        "full_name": "Atomic Information",
    },
    "BOHEMIA": {
        "description": "Special NATO intelligence handling compartment.",
        "systems": ["NATO"],
        "full_name": "BOHEMIA (NATO special intelligence)",
    },
    "STRAP": {
        "description": "UK compartmented intelligence handling system. STRAP1 is the most sensitive tier.",
        "systems": ["UK"],
        "full_name": "STRAP (UK compartmented intelligence)",
    },
    "SPECIALEMENT": {
        "description": "French special-access compartment.",
        "systems": ["FR"],
        "full_name": "Specialement (French SAP equivalent)",
    },
}

# ── Dissemination control markings ───────────────────────────────────────

DISSEM_CONTROLS: dict[str, dict[str, Any]] = {
    "LIMDIS": {
        "description": "Limited Distribution — dissemination restricted to "
        "named recipients or specific distribution list.",
        "systems": ["US"],
        "full_name": "Limited Distribution",
    },
    "NOCONTRACT": {
        "description": "Not releasable to contractors or consultants.",
        "systems": ["US"],
        "full_name": "No Contractor Dissemination",
    },
    "PROPIN": {
        "description": "Caution — Proprietary Information Involved. Contains commercial proprietary data.",
        "systems": ["US"],
        "full_name": "Proprietary Information Involved",
    },
    "WNINTEL": {
        "description": "Warning Notice — Intelligence Sources and Methods Involved.",
        "systems": ["US"],
        "full_name": "Warning Notice — Intelligence Sources/Methods",
    },
    "DESCRIPTOR": {
        "description": "UK dissemination descriptor: classification carries additional handling descriptors.",
        "systems": ["UK"],
        "full_name": "Handling Descriptor",
    },
    "UK EYES ONLY": {
        "description": "Dissemination restricted to UK nationals only.",
        "systems": ["UK"],
        "full_name": "UK Eyes Only",
    },
    "ACCM": {
        "description": "Additional Control Measures — UK supplementary handling instructions.",
        "systems": ["UK"],
        "full_name": "Additional Control Measures",
    },
}

# ── Declassification schedules (templates by system) ────────────────────

DECLASS_SCHEDULES: dict[str, dict[str, Any]] = {
    "US": {
        "authority": "Executive Order 13526, Section 1.5",
        "default_years": 10,
        "max_years": 25,
        "exemptions": [
            "50X1-HUM — Reveal human intelligence source",
            "50X2-WMD — Reveal WMD design or use info",
            "25X1 — Reveal confidential source identity where needed indefinitely",
            "25X2 — Reveal information that would assist in WMD development",
            "50X — Specific review required",
        ],
        "template": "DECLASSIFY ON: {date}",
        "date_format": "YYYYMMDD or YYYYMMDD-derived",
    },
    "UK": {
        "authority": "Public Records Act 1958, Freedom of Information Act 2000",
        "default_years": 20,
        "max_years": 30,
        "exemptions": [
            "Security Service / SIS records (national security)",
            "Intelligence sources and methods",
        ],
        "template": "DECLASSIFY: {date}",
        "date_format": "DD Month YYYY or YYYY",
    },
    "NATO": {
        "authority": "C-M(2002)49 (NATO Security Policy)",
        "default_years": 30,
        "max_years": 30,
        "exemptions": [
            "Member-state originated (originator controls declassification)",
        ],
        "template": "DECLASSIFIED ON: {date}",
        "date_format": "DD Month YYYY",
    },
    "EU": {
        "authority": "Council Decision 2013/488/EU",
        "default_years": 30,
        "max_years": 30,
        "exemptions": ["Originator-controlled declassification"],
        "template": "DECLASSIFIED: {date}",
        "date_format": "DD/MM/YYYY",
    },
}

# ── Public functions ────────────────────────────────────────────────────


def get_banner_line(
    system: str,
    level: str,
    caveats: list[str] | None = None,
) -> str | None:
    """Return the banner line for a classification level in a given system.

    Args:
        system: Classification system (US, UK, NATO, EU, FR, DE, CA, AU).
        level: Unified level name (public, unclassified, restricted,
               confidential, secret, top_secret, caveated).
        caveats: Optional list of caveat codes to append (e.g. ["SI", "NOFORN"]).

    Returns the banner line string, or None if the system or level is unknown.
    """
    sys_banners = BANNER_FORMATS.get(system.upper())
    if sys_banners is None:
        return None
    template = sys_banners.get(level.lower())
    if template is None:
        return None
    if caveats and "{caveats}" in template:
        return template.format(caveats="//".join(caveats))
    return template


def get_portion_marking(
    system: str,
    level: str,
    caveats: list[str] | None = None,
) -> str | None:
    """Return a portion-marking string for a paragraph or section.

    Constructs a marking string following the system's convention, e.g.
    "(S//SI)" for US SECRET with SI caveat, or "UK SECRET STRAP1" for UK.
    """
    sys_markings = PORTION_MARKINGS.get(system.upper())
    if sys_markings is None:
        return None
    banners = BANNER_FORMATS.get(system.upper(), {})
    base = banners.get(level.lower())
    if base is None:
        return None
    delimiter = sys_markings["delimiter"]
    if caveats:
        return f"{base}{delimiter}{'//'.join(caveats)}"
    if sys_markings["prefix"]:
        return f"({base})" if system.upper() in ("US",) else base
    return base


def resolve_caveat(code: str) -> dict[str, Any] | None:
    """Resolve a caveat code to its description dict, or None."""
    c = code.strip().upper()
    if c in CAVEAT_CODES:
        return dict(CAVEAT_CODES[c])
    for known, rec in CAVEAT_CODES.items():
        if known.replace(" ", "") == c.replace(" ", ""):
            return dict(rec)
    return None


def get_dissem_control(code: str) -> dict[str, Any] | None:
    """Resolve a dissemination control code, or None."""
    c = code.strip().upper()
    if c in DISSEM_CONTROLS:
        return dict(DISSEM_CONTROLS[c])
    for known, rec in DISSEM_CONTROLS.items():
        if known.replace(" ", "") == c.replace(" ", ""):
            return dict(rec)
    return None


def get_declass_schedule(system: str) -> dict[str, Any] | None:
    """Return the declassification schedule for a system, or None."""
    return DECLASS_SCHEDULES.get(system.upper())


def list_systems() -> list[str]:
    """Return all supported classification systems."""
    return sorted(BANNER_FORMATS.keys())


def list_caveats(system: str | None = None) -> list[str]:
    """Return caveat codes, optionally filtered by system."""
    if system is None:
        return sorted(CAVEAT_CODES.keys())
    sys = system.upper()
    return sorted([code for code, rec in CAVEAT_CODES.items() if sys in rec["systems"]])


__all__ = [
    "BANNER_FORMATS",
    "PORTION_MARKINGS",
    "CAVEAT_CODES",
    "DISSEM_CONTROLS",
    "DECLASS_SCHEDULES",
    "get_banner_line",
    "get_portion_marking",
    "resolve_caveat",
    "get_dissem_control",
    "get_declass_schedule",
    "list_systems",
    "list_caveats",
]
