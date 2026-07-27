"""
postal_delivery -- Postal codes, courier tracking, address validation,
customs declarations, and address formatting for the governance collection.

Data shape:

    POSTAL_CODE_PATTERNS[country] -> dict with pattern (regex str) and example
    COURIER_TRACKING[courier] -> dict with url_template
    CUSTOMS_DECLARATION_FORMATS[country] -> dict with form_id and required_fields list
    ADDRESS_FORMATS[country] -> dict with required_fields list and template hint

Functions:
    get_postal_code_pattern(code_or_name) -> dict | None
    get_courier_tracking_url(courier, tracking_number) -> str | None
    get_customs_declaration_format(country) -> dict | None
    normalize_address(country, address_string) -> dict
    validate_address(country, fields) -> dict
    search_countries_by_name(query) -> list[dict]
"""

from __future__ import annotations

from typing import Any

# ── Country name lookup ─────────────────────────────────────────────────────

_COUNTRY_NAMES: dict[str, str] = {
    "australia": "AU",
    "brazil": "BR",
    "canada": "CA",
    "china": "CN",
    "france": "FR",
    "germany": "DE",
    "india": "IN",
    "japan": "JP",
    "united kingdom": "GB",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "uk": "GB",
    "great britain": "GB",
    "england": "GB",
}

_COUNTRY_DISPLAY_NAMES: dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "DE": "Germany",
    "FR": "France",
    "AU": "Australia",
    "JP": "Japan",
    "BR": "Brazil",
    "IN": "India",
    "CN": "China",
}

# ── Postal code patterns ────────────────────────────────────────────────────

POSTAL_CODE_PATTERNS: dict[str, dict[str, str]] = {
    "US": {
        "pattern": r"^\d{5}(-\d{4})?$",
        "example": "12345-6789",
    },
    "GB": {
        "pattern": r"^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$",
        "example": "SW1A 1AA",
    },
    "CA": {
        "pattern": r"^[A-Z]\d[A-Z] ?\d[A-Z]\d$",
        "example": "K1A 0B1",
    },
    "DE": {
        "pattern": r"^\d{5}$",
        "example": "10115",
    },
    "FR": {
        "pattern": r"^\d{5}$",
        "example": "75001",
    },
    "AU": {
        "pattern": r"^\d{4}$",
        "example": "2000",
    },
    "JP": {
        "pattern": r"^\d{3}-\d{4}$",
        "example": "100-0001",
    },
    "BR": {
        "pattern": r"^\d{5}-\d{3}$",
        "example": "01001-000",
    },
    "IN": {
        "pattern": r"^\d{6}$",
        "example": "110001",
    },
    "CN": {
        "pattern": r"^\d{6}$",
        "example": "100000",
    },
    "IT": {
        "pattern": r"^\d{5}$",
        "example": "00100",
    },
    "ES": {
        "pattern": r"^\d{5}$",
        "example": "28001",
    },
}

# ── Courier tracking URL templates ──────────────────────────────────────────

COURIER_TRACKING: dict[str, dict[str, str]] = {
    "usps": {
        "url_template": "https://tools.usps.com/go/TrackConfirmAction?tLabels={tracking_number}",
    },
    "fedex": {
        "url_template": "https://www.fedex.com/fedextrack/?trknbr={tracking_number}",
    },
    "ups": {
        "url_template": "https://www.ups.com/track?tracknum={tracking_number}",
    },
    "dhl": {
        "url_template": "https://www.dhl.com/en/express/tracking.html?AWB={tracking_number}",
    },
    "royal_mail": {
        "url_template": "https://www.royalmail.com/track-your-item#/tracking-results/{tracking_number}",
    },
    "canada_post": {
        "url_template": "https://www.canadapost-postescanada.ca/track-reperage/en#/details/{tracking_number}",
    },
}

# ── Customs declaration formats ─────────────────────────────────────────────

CUSTOMS_DECLARATION_FORMATS: dict[str, dict[str, Any]] = {
    "US": {
        "form_id": "CBP Form 6059B / CPB Form 7501",
        "required_fields": [
            "sender_name",
            "sender_address",
            "recipient_name",
            "recipient_address",
            "description_of_goods",
            "quantity",
            "value",
            "weight",
            "country_of_origin",
            "harmonized_code",
        ],
        "notes": "CBP e-commerce: Section 321 de minimis < $800 USD.",
        "de_minimis_usd": 800,
    },
    "GB": {
        "form_id": "CN22 / CN23 (UPU S10)",
        "required_fields": [
            "sender_name",
            "sender_address",
            "recipient_name",
            "recipient_address",
            "description_of_goods",
            "quantity",
            "value",
            "weight",
            "country_of_origin",
        ],
        "notes": "CN22 for value < GBP 270; CN23 for higher. UKCA/CE marking for applicable goods.",
        "de_minimis_gbp": 135,
    },
    "DE": {
        "form_id": "CN22 / CN23 (UPU S10) / ATLAS Zollanmeldung",
        "required_fields": [
            "sender_name",
            "sender_address",
            "recipient_name",
            "recipient_address",
            "description_of_goods",
            "quantity",
            "value",
            "weight",
            "country_of_origin",
            "harmonized_code",
        ],
        "notes": (
            "EU customs territory. IOSS for B2C e-commerce imports <= EUR 150. "
            "ATLAS electronic submission required for commercial goods."
        ),
        "de_minimis_eur": 150,
    },
    "CA": {
        "form_id": "CBSA Customs Declaration (Form E14) / Commercial Invoice",
        "required_fields": [
            "sender_name",
            "sender_address",
            "recipient_name",
            "recipient_address",
            "description_of_goods",
            "quantity",
            "value",
            "weight",
            "country_of_origin",
        ],
        "notes": (
            "De minimis CAD 20 (postal); CAD 40 (courier) for duties; "
            "CAD 150 (courier) for tax. CBSA eManifest for commercial goods."
        ),
        "de_minimis_cad": 20,
    },
    "AU": {
        "form_id": "Customs Declaration Form (B650) / Import Declaration (N10)",
        "required_fields": [
            "sender_name",
            "sender_address",
            "recipient_name",
            "recipient_address",
            "description_of_goods",
            "quantity",
            "value",
            "weight",
            "country_of_origin",
        ],
        "notes": (
            "Self-Assessed Clearance (SAC) for goods <= AUD 1000. "
            "Import Declaration (N10) for goods > AUD 1000. Biosecurity declaration required."
        ),
        "de_minimis_aud": 1000,
    },
    "JP": {
        "form_id": "CN22 / CN23 (UPU S10) / Japan Customs Import Declaration",
        "required_fields": [
            "sender_name",
            "sender_address",
            "recipient_name",
            "recipient_address",
            "description_of_goods",
            "quantity",
            "value",
            "weight",
            "country_of_origin",
        ],
        "notes": (
            "Simplified customs for value <= JPY 200,000. NACCS electronic declaration system for commercial goods."
        ),
        "de_minimis_jpy": 166666,
    },
    "EU": {
        "form_id": "CN22 / CN23 (UPU S10) / Union Customs Code declaration",
        "required_fields": [
            "sender_name",
            "sender_address",
            "recipient_name",
            "recipient_address",
            "description_of_goods",
            "quantity",
            "value",
            "weight",
            "country_of_origin",
            "harmonized_code",
            "eori_number",
        ],
        "notes": (
            "EU-wide: customs territory. IOSS (Import One-Stop Shop) for "
            "B2C e-commerce <= EUR 150. EORI number required for commercial shipments."
        ),
        "de_minimis_eur": 150,
    },
    "BR": {
        "form_id": "Declaracao de Importacao (DI) / Declaracao Simplificada (DS)",
        "required_fields": [
            "sender_name",
            "sender_address",
            "recipient_name",
            "recipient_cpf_cnpj",
            "recipient_address",
            "description_of_goods",
            "quantity",
            "value",
            "weight",
            "country_of_origin",
            "harmonized_code",
        ],
        "notes": (
            "Registro e Rastreamento (REMESSA) for postal. Siscomex for commercial goods. "
            "Import tax 60% on value including shipping for individuals."
        ),
        "de_minimis_usd": 50,
    },
}

# ── Address formats and required fields ─────────────────────────────────────

ADDRESS_FORMATS: dict[str, dict[str, Any]] = {
    "US": {
        "required_fields": ["recipient", "street_address", "city", "state", "zip_code"],
        "format_template": "{recipient}\n{street_address}\n{city}, {state} {zip_code}",
        "label": "United States",
    },
    "GB": {
        "required_fields": ["recipient", "street_address", "city", "postal_code"],
        "format_template": "{recipient}\n{street_address}\n{city}\n{postal_code}",
        "label": "United Kingdom",
    },
    "CA": {
        "required_fields": ["recipient", "street_address", "city", "province", "postal_code"],
        "format_template": "{recipient}\n{street_address}\n{city}, {province} {postal_code}",
        "label": "Canada",
    },
    "DE": {
        "required_fields": ["recipient", "street_address", "city", "postal_code"],
        "format_template": "{recipient}\n{street_address}\n{postal_code} {city}",
        "label": "Germany",
    },
    "FR": {
        "required_fields": ["recipient", "street_address", "city", "postal_code"],
        "format_template": "{recipient}\n{street_address}\n{postal_code} {city}",
        "label": "France",
    },
    "AU": {
        "required_fields": ["recipient", "street_address", "city", "state", "postal_code"],
        "format_template": "{recipient}\n{street_address}\n{city}, {state} {postal_code}",
        "label": "Australia",
    },
    "JP": {
        "required_fields": ["recipient", "street_address", "city", "prefecture", "postal_code"],
        "format_template": "〒{postal_code}\n{prefecture} {city}\n{street_address}\n{recipient}",
        "label": "Japan",
    },
    "BR": {
        "required_fields": ["recipient", "street_address", "neighborhood", "city", "state", "postal_code"],
        "format_template": "{recipient}\n{street_address}, {neighborhood}\n{city} - {state}\n{postal_code}",
        "label": "Brazil",
    },
    "IN": {
        "required_fields": ["recipient", "street_address", "city", "state", "postal_code"],
        "format_template": "{recipient}\n{street_address}\n{city}, {state} {postal_code}",
        "label": "India",
    },
    "CN": {
        "required_fields": ["recipient", "street_address", "city", "province", "postal_code"],
        "format_template": "{postal_code}\n{province} {city}\n{street_address}\n{recipient}",
        "label": "China",
    },
}

# ── Functions ──────────────────────────────────────────────────────────────


def _resolve_country_code(code_or_name: str) -> str | None:
    """Resolve a country code (US, GB, ...) or full name to an ISO code."""
    candidate = code_or_name.strip().upper()
    if len(candidate) == 2 and candidate in POSTAL_CODE_PATTERNS:
        return candidate
    lower = code_or_name.strip().lower()
    return _COUNTRY_NAMES.get(lower)


def get_postal_code_pattern(code_or_name: str) -> dict[str, str] | None:
    """Return the postal code pattern for a country code or full name."""
    code = _resolve_country_code(code_or_name)
    if code is None:
        return None
    data = POSTAL_CODE_PATTERNS.get(code)
    if data is None:
        return None
    return dict(data)


def get_courier_tracking_url(courier: str, tracking_number: str) -> str | None:
    """Build a tracking URL for a courier and tracking number."""
    key = courier.strip().lower()
    entry = COURIER_TRACKING.get(key)
    if entry is None:
        return None
    return entry["url_template"].format(tracking_number=tracking_number)


def get_customs_declaration_format(country: str) -> dict[str, Any] | None:
    """Return the customs declaration format for a country code."""
    code = country.strip().upper()
    data = CUSTOMS_DECLARATION_FORMATS.get(code)
    if data is None:
        return None
    return dict(data)


def normalize_address(country: str, address_string: str) -> dict[str, Any]:
    """Normalize a raw address string into a structured dict for a country."""
    code = country.strip().upper()
    result: dict[str, Any] = {
        "country": code,
        "raw": address_string,
    }
    fmt = ADDRESS_FORMATS.get(code)
    if fmt is not None:
        result["format_template"] = fmt.get("format_template")
        result["required_fields"] = list(fmt.get("required_fields", []))
    return result


def validate_address(country: str, fields: dict[str, str]) -> dict[str, Any]:
    """Validate that an address dict has the required fields for a country."""
    code = country.strip().upper()
    fmt = ADDRESS_FORMATS.get(code)
    if fmt is None:
        return {
            "valid": True,
            "country": code,
            "note": f"No address format defined for {code}; no validation performed.",
            "missing_fields": [],
        }
    required = list(fmt["required_fields"])
    missing = [f for f in required if f not in fields or not fields[f]]
    return {
        "valid": len(missing) == 0,
        "country": code,
        "missing_fields": missing,
    }


def search_countries_by_name(query: str) -> list[dict[str, Any]]:
    """Return countries whose name or display name matches a query string."""
    q = query.strip().lower()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, code in _COUNTRY_NAMES.items():
        if q in name and code not in seen:
            seen.add(code)
            results.append(
                {
                    "code": code,
                    "name": _COUNTRY_DISPLAY_NAMES.get(code, code),
                    "matched_by": name,
                }
            )
    for code, display in _COUNTRY_DISPLAY_NAMES.items():
        if code not in seen and q in display.lower():
            seen.add(code)
            results.append(
                {
                    "code": code,
                    "name": display,
                    "matched_by": display,
                }
            )
    return results


__all__ = [
    "ADDRESS_FORMATS",
    "COURIER_TRACKING",
    "CUSTOMS_DECLARATION_FORMATS",
    "POSTAL_CODE_PATTERNS",
    "get_courier_tracking_url",
    "get_customs_declaration_format",
    "get_postal_code_pattern",
    "normalize_address",
    "search_countries_by_name",
    "validate_address",
]
