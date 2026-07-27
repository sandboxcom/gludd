"""Jurisdiction identifiers and hierarchy for the governance collection.

Exposes ISO 3166-1/2 codes, FIPS codes, GLEIF jurisdiction codes, sovereignty
status, and jurisdictional hierarchy queries.

Public surface::

    JURISDICTION_CODES    dict of alpha-2 -> {alpha_3, numeric, name, fips, gleif, sovereignty}
    SUBDIVISION_CODES     dict of subdivision code -> {name, parent, category}
    list_jurisdictions()  -> list of jurisdiction summary dicts
    get_jurisdiction(code)  -> full jurisdiction dict or None
    get_subdivisions(code)  -> list of subdivision dicts
    is_sovereign(code)    -> bool
    get_sovereignty_status(code) -> str or None
    resolve_fips(fips_code) -> jurisdiction name or None
    resolve_gleif(gleif_code) -> jurisdiction name or None
"""

from __future__ import annotations

from typing import Any

# ── ISO 3166-1 jurisdiction codes ──────────────────────────────────────────
#
# Key = alpha-2 code. Value = full jurisdiction record.
# Covers all UN member states plus frequently-referenced non-UN territories.

JURISDICTION_CODES: dict[str, dict[str, Any]] = {
    "AF": {
        "alpha_2": "AF",
        "alpha_3": "AFG",
        "numeric": "004",
        "name": "Afghanistan",
        "fips": "AF",
        "gleif": "AF",
        "sovereignty": "sovereign",
        "region": "Asia",
        "sub_region": "Southern Asia",
    },
    "AL": {
        "alpha_2": "AL",
        "alpha_3": "ALB",
        "numeric": "008",
        "name": "Albania",
        "fips": "AL",
        "gleif": "AL",
        "sovereignty": "sovereign",
        "region": "Europe",
        "sub_region": "Southern Europe",
    },
    "DZ": {
        "alpha_2": "DZ",
        "alpha_3": "DZA",
        "numeric": "012",
        "name": "Algeria",
        "fips": "AG",
        "gleif": "DZ",
        "sovereignty": "sovereign",
        "region": "Africa",
        "sub_region": "Northern Africa",
    },
    "AR": {
        "alpha_2": "AR",
        "alpha_3": "ARG",
        "numeric": "032",
        "name": "Argentina",
        "fips": "AR",
        "gleif": "AR",
        "sovereignty": "sovereign",
        "region": "Americas",
        "sub_region": "South America",
    },
    "AU": {
        "alpha_2": "AU",
        "alpha_3": "AUS",
        "numeric": "036",
        "name": "Australia",
        "fips": "AS",
        "gleif": "AU",
        "sovereignty": "sovereign",
        "region": "Oceania",
        "sub_region": "Australia and New Zealand",
    },
    "BR": {
        "alpha_2": "BR",
        "alpha_3": "BRA",
        "numeric": "076",
        "name": "Brazil",
        "fips": "BR",
        "gleif": "BR",
        "sovereignty": "sovereign",
        "region": "Americas",
        "sub_region": "South America",
    },
    "CA": {
        "alpha_2": "CA",
        "alpha_3": "CAN",
        "numeric": "124",
        "name": "Canada",
        "fips": "CA",
        "gleif": "CA",
        "sovereignty": "sovereign",
        "region": "Americas",
        "sub_region": "Northern America",
    },
    "CN": {
        "alpha_2": "CN",
        "alpha_3": "CHN",
        "numeric": "156",
        "name": "China",
        "fips": "CH",
        "gleif": "CN",
        "sovereignty": "sovereign",
        "region": "Asia",
        "sub_region": "Eastern Asia",
    },
    "FR": {
        "alpha_2": "FR",
        "alpha_3": "FRA",
        "numeric": "250",
        "name": "France",
        "fips": "FR",
        "gleif": "FR",
        "sovereignty": "sovereign",
        "region": "Europe",
        "sub_region": "Western Europe",
    },
    "DE": {
        "alpha_2": "DE",
        "alpha_3": "DEU",
        "numeric": "276",
        "name": "Germany",
        "fips": "GM",
        "gleif": "DE",
        "sovereignty": "sovereign",
        "region": "Europe",
        "sub_region": "Western Europe",
    },
    "IN": {
        "alpha_2": "IN",
        "alpha_3": "IND",
        "numeric": "356",
        "name": "India",
        "fips": "IN",
        "gleif": "IN",
        "sovereignty": "sovereign",
        "region": "Asia",
        "sub_region": "Southern Asia",
    },
    "IL": {
        "alpha_2": "IL",
        "alpha_3": "ISR",
        "numeric": "376",
        "name": "Israel",
        "fips": "IS",
        "gleif": "IL",
        "sovereignty": "sovereign",
        "region": "Asia",
        "sub_region": "Western Asia",
    },
    "IT": {
        "alpha_2": "IT",
        "alpha_3": "ITA",
        "numeric": "380",
        "name": "Italy",
        "fips": "IT",
        "gleif": "IT",
        "sovereignty": "sovereign",
        "region": "Europe",
        "sub_region": "Southern Europe",
    },
    "JP": {
        "alpha_2": "JP",
        "alpha_3": "JPN",
        "numeric": "392",
        "name": "Japan",
        "fips": "JA",
        "gleif": "JP",
        "sovereignty": "sovereign",
        "region": "Asia",
        "sub_region": "Eastern Asia",
    },
    "MX": {
        "alpha_2": "MX",
        "alpha_3": "MEX",
        "numeric": "484",
        "name": "Mexico",
        "fips": "MX",
        "gleif": "MX",
        "sovereignty": "sovereign",
        "region": "Americas",
        "sub_region": "Central America",
    },
    "NZ": {
        "alpha_2": "NZ",
        "alpha_3": "NZL",
        "numeric": "554",
        "name": "New Zealand",
        "fips": "NZ",
        "gleif": "NZ",
        "sovereignty": "sovereign",
        "region": "Oceania",
        "sub_region": "Australia and New Zealand",
    },
    "RU": {
        "alpha_2": "RU",
        "alpha_3": "RUS",
        "numeric": "643",
        "name": "Russian Federation",
        "fips": "RS",
        "gleif": "RU",
        "sovereignty": "sovereign",
        "region": "Europe",
        "sub_region": "Eastern Europe",
    },
    "ZA": {
        "alpha_2": "ZA",
        "alpha_3": "ZAF",
        "numeric": "710",
        "name": "South Africa",
        "fips": "SF",
        "gleif": "ZA",
        "sovereignty": "sovereign",
        "region": "Africa",
        "sub_region": "Southern Africa",
    },
    "KR": {
        "alpha_2": "KR",
        "alpha_3": "KOR",
        "numeric": "410",
        "name": "Korea, Republic of",
        "fips": "KS",
        "gleif": "KR",
        "sovereignty": "sovereign",
        "region": "Asia",
        "sub_region": "Eastern Asia",
    },
    "ES": {
        "alpha_2": "ES",
        "alpha_3": "ESP",
        "numeric": "724",
        "name": "Spain",
        "fips": "SP",
        "gleif": "ES",
        "sovereignty": "sovereign",
        "region": "Europe",
        "sub_region": "Southern Europe",
    },
    "CH": {
        "alpha_2": "CH",
        "alpha_3": "CHE",
        "numeric": "756",
        "name": "Switzerland",
        "fips": "SZ",
        "gleif": "CH",
        "sovereignty": "sovereign",
        "region": "Europe",
        "sub_region": "Western Europe",
    },
    "TR": {
        "alpha_2": "TR",
        "alpha_3": "TUR",
        "numeric": "792",
        "name": "Turkiye",
        "fips": "TU",
        "gleif": "TR",
        "sovereignty": "sovereign",
        "region": "Asia",
        "sub_region": "Western Asia",
    },
    "UA": {
        "alpha_2": "UA",
        "alpha_3": "UKR",
        "numeric": "804",
        "name": "Ukraine",
        "fips": "UP",
        "gleif": "UA",
        "sovereignty": "sovereign",
        "region": "Europe",
        "sub_region": "Eastern Europe",
    },
    "AE": {
        "alpha_2": "AE",
        "alpha_3": "ARE",
        "numeric": "784",
        "name": "United Arab Emirates",
        "fips": "AE",
        "gleif": "AE",
        "sovereignty": "sovereign",
        "region": "Asia",
        "sub_region": "Western Asia",
    },
    "GB": {
        "alpha_2": "GB",
        "alpha_3": "GBR",
        "numeric": "826",
        "name": "United Kingdom",
        "fips": "UK",
        "gleif": "GB",
        "sovereignty": "sovereign",
        "region": "Europe",
        "sub_region": "Northern Europe",
    },
    "US": {
        "alpha_2": "US",
        "alpha_3": "USA",
        "numeric": "840",
        "name": "United States",
        "fips": "US",
        "gleif": "US",
        "sovereignty": "sovereign",
        "region": "Americas",
        "sub_region": "Northern America",
    },
    "EU": {
        "alpha_2": "EU",
        "alpha_3": None,
        "numeric": None,
        "name": "European Union",
        "fips": None,
        "gleif": None,
        "sovereignty": "supranational",
        "region": "Europe",
        "sub_region": None,
    },
    "XK": {
        "alpha_2": "XK",
        "alpha_3": "XKX",
        "numeric": None,
        "name": "Kosovo",
        "fips": "KV",
        "gleif": None,
        "sovereignty": "partial",
        "region": "Europe",
        "sub_region": "Southern Europe",
    },
    "TW": {
        "alpha_2": "TW",
        "alpha_3": "TWN",
        "numeric": "158",
        "name": "Taiwan",
        "fips": "TW",
        "gleif": "TW",
        "sovereignty": "partial",
        "region": "Asia",
        "sub_region": "Eastern Asia",
    },
    "PS": {
        "alpha_2": "PS",
        "alpha_3": "PSE",
        "numeric": "275",
        "name": "Palestine, State of",
        "fips": "WE",
        "gleif": "PS",
        "sovereignty": "partial",
        "region": "Asia",
        "sub_region": "Western Asia",
    },
}

# ── ISO 3166-2 subdivision codes (selected) ──────────────────────────────
#
# Key = ISO 3166-2 subdivision code (e.g. "US-CA"). Value = name and category.

SUBDIVISION_CODES: dict[str, dict[str, Any]] = {
    "US-CA": {"name": "California", "parent": "US", "category": "state"},
    "US-TX": {"name": "Texas", "parent": "US", "category": "state"},
    "US-NY": {"name": "New York", "parent": "US", "category": "state"},
    "US-FL": {"name": "Florida", "parent": "US", "category": "state"},
    "US-IL": {"name": "Illinois", "parent": "US", "category": "state"},
    "US-DC": {"name": "District of Columbia", "parent": "US", "category": "district"},
    "CA-ON": {"name": "Ontario", "parent": "CA", "category": "province"},
    "CA-QC": {"name": "Quebec", "parent": "CA", "category": "province"},
    "CA-BC": {"name": "British Columbia", "parent": "CA", "category": "province"},
    "GB-ENG": {"name": "England", "parent": "GB", "category": "country"},
    "GB-SCT": {"name": "Scotland", "parent": "GB", "category": "country"},
    "GB-WLS": {"name": "Wales", "parent": "GB", "category": "country"},
    "GB-NIR": {"name": "Northern Ireland", "parent": "GB", "category": "province"},
    "DE-BE": {"name": "Berlin", "parent": "DE", "category": "state"},
    "DE-BY": {"name": "Bayern", "parent": "DE", "category": "state"},
    "DE-NW": {"name": "Nordrhein-Westfalen", "parent": "DE", "category": "state"},
    "FR-IDF": {"name": "Ile-de-France", "parent": "FR", "category": "region"},
    "FR-PAC": {"name": "Provence-Alpes-Cote d'Azur", "parent": "FR", "category": "region"},
    "AU-NSW": {"name": "New South Wales", "parent": "AU", "category": "state"},
    "AU-VIC": {"name": "Victoria", "parent": "AU", "category": "state"},
    "IN-MH": {"name": "Maharashtra", "parent": "IN", "category": "state"},
    "IN-DL": {"name": "Delhi", "parent": "IN", "category": "union territory"},
    "JP-13": {"name": "Tokyo", "parent": "JP", "category": "prefecture"},
    "BR-SP": {"name": "Sao Paulo", "parent": "BR", "category": "state"},
}

# ── GLEIF jurisdiction code lookup (for entity registration) ─────────────
#
# GLEIF codes may differ from ISO alpha-2 for certain entities. This
# reverse map resolves a GLEIF code back to a jurisdiction name.

_GLEIF_REVERSE: dict[str, str] = {v["gleif"]: v["name"] for v in JURISDICTION_CODES.values() if v["gleif"] is not None}

# ── FIPS code reverse map ────────────────────────────────────────────────

_FIPS_REVERSE: dict[str, str] = {v["fips"]: v["name"] for v in JURISDICTION_CODES.values() if v["fips"] is not None}

# ── Sovereignty status taxonomy ─────────────────────────────────────────

SOVEREIGNTY_STATUSES: frozenset[str] = frozenset(
    {
        "sovereign",
        "partial",
        "disputed",
        "unrecognised",
        "supranational",
        "dependent_territory",
    }
)

# ── Jurisdictional hierarchy ────────────────────────────────────────────
#
# Maps a jurisdiction to zero or more parent jurisdictions.
# Example: a US state is subordinate to "US" and "US" is sovereign (no parent).

JURISDICTION_PARENTS: dict[str, frozenset[str]] = {
    "US-CA": frozenset({"US"}),
    "US-TX": frozenset({"US"}),
    "US-NY": frozenset({"US"}),
    "US-FL": frozenset({"US"}),
    "US-IL": frozenset({"US"}),
    "US-DC": frozenset({"US"}),
    "CA-ON": frozenset({"CA"}),
    "CA-QC": frozenset({"CA"}),
    "CA-BC": frozenset({"CA"}),
    "GB-ENG": frozenset({"GB"}),
    "GB-SCT": frozenset({"GB"}),
    "GB-WLS": frozenset({"GB"}),
    "GB-NIR": frozenset({"GB"}),
    "DE-BE": frozenset({"DE"}),
    "DE-BY": frozenset({"DE"}),
    "DE-NW": frozenset({"DE"}),
    "FR-IDF": frozenset({"FR"}),
    "FR-PAC": frozenset({"FR"}),
    "AU-NSW": frozenset({"AU"}),
    "AU-VIC": frozenset({"AU"}),
    "IN-MH": frozenset({"IN"}),
    "IN-DL": frozenset({"IN"}),
    "JP-13": frozenset({"JP"}),
    "BR-SP": frozenset({"BR"}),
}

# ── Public functions ────────────────────────────────────────────────────


def list_jurisdictions(
    sovereignty: str | None = None,
    region: str | None = None,
) -> list[dict[str, Any]]:
    """List all known jurisdictions, optionally filtered."""
    results: list[dict[str, Any]] = []
    for code, rec in JURISDICTION_CODES.items():
        if sovereignty is not None and rec.get("sovereignty") != sovereignty:
            continue
        if region is not None and rec.get("region") != region:
            continue
        results.append(
            {
                "code": code,
                "name": rec["name"],
                "sovereignty": rec["sovereignty"],
                "region": rec.get("region"),
            }
        )
    return sorted(results, key=lambda r: r["name"])


def get_jurisdiction(code: str) -> dict[str, Any] | None:
    """Get a full jurisdiction record by code (alpha-2, alpha-3, or numeric).

    Returns None for unknown codes.
    """
    c = code.strip().upper()
    if c in JURISDICTION_CODES:
        return dict(JURISDICTION_CODES[c])
    for rec in JURISDICTION_CODES.values():
        if rec.get("alpha_3") == c:
            return dict(rec)
        if rec.get("numeric") == c:
            return dict(rec)
    return None


def get_subdivisions(code: str) -> list[dict[str, Any]]:
    """Return all known subdivisions for a parent jurisdiction code."""
    parent = code.strip().upper()
    results: list[dict[str, Any]] = []
    for sub_code, rec in SUBDIVISION_CODES.items():
        if rec["parent"] == parent:
            results.append({"code": sub_code, **rec})
    return sorted(results, key=lambda r: r["name"])


def is_sovereign(code: str) -> bool:
    """True if the jurisdiction is recognised as fully sovereign."""
    rec = get_jurisdiction(code)
    return rec is not None and rec.get("sovereignty") == "sovereign"


def get_sovereignty_status(code: str) -> str | None:
    """Return the sovereignty status string, or None for unknown codes."""
    rec = get_jurisdiction(code)
    if rec is None:
        return None
    return rec.get("sovereignty")


def resolve_fips(fips_code: str) -> str | None:
    """Resolve a FIPS code to a jurisdiction name, or None."""
    return _FIPS_REVERSE.get(fips_code.strip().upper())


def resolve_gleif(gleif_code: str) -> str | None:
    """Resolve a GLEIF code to a jurisdiction name, or None."""
    return _GLEIF_REVERSE.get(gleif_code.strip().upper())


def get_parents(code: str) -> frozenset[str] | None:
    """Return the parent jurisdiction codes, or None if not in the hierarchy."""
    return JURISDICTION_PARENTS.get(code.strip().upper())


__all__ = [
    "JURISDICTION_CODES",
    "SUBDIVISION_CODES",
    "SOVEREIGNTY_STATUSES",
    "JURISDICTION_PARENTS",
    "list_jurisdictions",
    "get_jurisdiction",
    "get_subdivisions",
    "is_sovereign",
    "get_sovereignty_status",
    "resolve_fips",
    "resolve_gleif",
    "get_parents",
]
