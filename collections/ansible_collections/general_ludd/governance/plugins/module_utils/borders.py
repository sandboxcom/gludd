"""
borders -- Border type taxonomy, recognition status, and crossing requirements.

Data shape:

    BORDER_DATA[region] = {
        "type": <BORDER_TYPES member>,
        "controlling_bodies": list[str],
        "recognition": <RECOGNITION_STATUS member>,
        "crossing_requirements": {
            "documents": list[str],
            "visa_required": bool,
            "visa_type": <VISA_TYPES member> | None,
            "notes": str | None,
        },
    }

Functions:
    lookup_border(region) -> dict | None
    get_crossing_requirements(origin, destination) -> dict
    get_recognition_status(entity) -> str | None
    get_visa_requirements(passport_country, destination_country) -> dict
"""

from __future__ import annotations

from typing import Any

# ── Border type taxonomy ─────────────────────────────────────────────────────
#
# Land      - terrestrial state boundaries (e.g. US-Canada).
# Maritime  - territorial-sea / EEZ boundaries at sea.
# Airspace  - controlled flight information regions (FIRs).
# Customs   - customs-control lines that may differ from a state border
#             (e.g. the Schengen internal perimeter, Common Travel Area).
# Administrative - internal subdivisions between provinces/states/municipalities
#                   that carry movement controls.
# Contested - boundaries where sovereignty is actively disputed.
# Demilitarized - zones established by treaty where military presence is barred.

BORDER_TYPES: frozenset[str] = frozenset(
    {
        "land",
        "maritime",
        "airspace",
        "customs",
        "administrative",
        "contested",
        "demilitarized",
    }
)

# ── Recognition status ────────────────────────────────────────────────────────
#
# universal     - recognised as sovereign by the vast majority of UN members.
# partial       - recognised by some UN members but not others.
# disputed      - sovereignty actively contested by one or more claimants.
# unrecognised  - no or near-zero UN-member recognition.
# de_facto      - exercises effective territorial control without broad
#                 diplomatic recognition (often overlaps with partial/unrecognised).

RECOGNITION_STATUS: frozenset[str] = frozenset(
    {
        "universal",
        "partial",
        "disputed",
        "unrecognised",
        "de_facto",
    }
)

# ── Visa types ─────────────────────────────────────────────────────────────────

VISA_TYPES: frozenset[str] = frozenset(
    {
        "tourist",
        "business",
        "transit",
        "student",
        "work",
        "diplomatic",
        "refugee",
        "digital_nomad",
    }
)


def _req(
    documents: list[str],
    visa_required: bool,
    visa_type: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Build a crossing-requirements dict, validating the visa type token."""
    if visa_type is not None and visa_type not in VISA_TYPES:
        raise ValueError(f"Unknown visa type {visa_type!r}")
    return {
        "documents": documents,
        "visa_required": visa_required,
        "visa_type": visa_type,
        "notes": notes,
    }


# ── Major border regions ──────────────────────────────────────────────────────
#
# Keys are human-readable region/border names. Values describe the border type,
# which bodies exercise control, its recognition posture, and the documents +
# visa posture required to cross.

BORDER_DATA: dict[str, dict[str, Any]] = {
    "US-Canada land border": {
        "type": "land",
        "controlling_bodies": [
            "US Customs and Border Protection",
            "Canada Border Services Agency",
        ],
        "recognition": "universal",
        "crossing_requirements": _req(
            documents=["passport", "WHTI-compliant ID"],
            visa_required=False,
            notes="Visa-free for stays <180 days under reciprocal agreements.",
        ),
    },
    "US-Mexico land border": {
        "type": "land",
        "controlling_bodies": [
            "US Customs and Border Protection",
            "Instituto Nacional de Migracion (Mexico)",
        ],
        "recognition": "universal",
        "crossing_requirements": _req(
            documents=["passport", "BCC (if applicable)"],
            visa_required=True,
            visa_type="tourist",
            notes="Mexican nationals require a US visa or BCC; ESTA for VWP nationals.",
        ),
    },
    "Schengen internal border": {
        "type": "customs",
        "controlling_bodies": ["Schengen Member States", "Frontex (coordination)"],
        "recognition": "universal",
        "crossing_requirements": _req(
            documents=["national ID card", "passport"],
            visa_required=False,
            notes="Free movement within the Schengen Area for 90/180 days.",
        ),
    },
    "Korean Demilitarized Zone (DMZ)": {
        "type": "demilitarized",
        "controlling_bodies": [
            "United Nations Command",
            "Korean People's Army (DPRK)",
            "Republic of Korea Armed Forces",
        ],
        "recognition": "universal",
        "crossing_requirements": _req(
            documents=["military authorisation", "diplomatic clearance"],
            visa_required=False,
            notes="Civilian crossing prohibited except at JSA under escort.",
        ),
    },
    "India-Pakistan Line of Control (Kashmir)": {
        "type": "contested",
        "controlling_bodies": [
            "Indian Army",
            "Pakistan Armed Forces",
            "United Nations Military Observer Group (UNMOGIP)",
        ],
        "recognition": "disputed",
        "crossing_requirements": _req(
            documents=["passport", "special permit"],
            visa_required=True,
            visa_type="diplomatic",
            notes="Heavily militarised; civilian crossings restricted to a few points.",
        ),
    },
    "Western Sahara boundary": {
        "type": "contested",
        "controlling_bodies": ["Kingdom of Morocco", "Sahrawi Arab Democratic Republic"],
        "recognition": "disputed",
        "crossing_requirements": _req(
            documents=["passport", "Moroccan entry stamp"],
            visa_required=True,
            visa_type="tourist",
            notes="Status of the territory is contested between Morocco and the SADR.",
        ),
    },
    "Bering Strait maritime boundary": {
        "type": "maritime",
        "controlling_bodies": [
            "US Coast Guard",
            "Russian Border Guard Service",
        ],
        "recognition": "universal",
        "crossing_requirements": _req(
            documents=["passport", "vessel registration"],
            visa_required=True,
            visa_type="transit",
            notes="Maritime boundary agreement 1990; native peoples cross for subsistence.",
        ),
    },
    "Antarctic Treaty area": {
        "type": "demilitarized",
        "controlling_bodies": ["Antarctic Treaty System", "national Antarctic programmes"],
        "recognition": "universal",
        "crossing_requirements": _req(
            documents=["expedition permit", "environmental impact assessment"],
            visa_required=False,
            notes="No state sovereignty; activities governed by the Antarctic Treaty (1959).",
        ),
    },
    "Northern Cyprus Green Line": {
        "type": "contested",
        "controlling_bodies": [
            "Republic of Cyprus",
            "Turkish Republic of Northern Cyprus",
            "UN Peacekeeping Force in Cyprus (UNFICYP)",
        ],
        "recognition": "de_facto",
        "crossing_requirements": _req(
            documents=["passport", "Green Line crossing card"],
            visa_required=False,
            notes="TRNC is recognised only by Turkey; EU Green Line Regulation governs crossings.",
        ),
    },
    "Schengen external border": {
        "type": "customs",
        "controlling_bodies": ["Schengen Member States", "Frontex (coordination)"],
        "recognition": "universal",
        "crossing_requirements": _req(
            documents=["passport", "Schengen visa (if required)"],
            visa_required=True,
            visa_type="tourist",
            notes="Non-EU/EEA nationals may require a Schengen visa (90/180 days).",
        ),
    },
    "Brazil-Argentina Iguazu crossing": {
        "type": "land",
        "controlling_bodies": [
            "Policia Federal (Brazil)",
            "Direccion Nacional de Migraciones (Argentina)",
        ],
        "recognition": "universal",
        "crossing_requirements": _req(
            documents=["passport", "national ID (Mercosur nationals)"],
            visa_required=False,
            notes="Mercosur nationals travel visa-free with national ID.",
        ),
    },
    "UK-Ireland Common Travel Area": {
        "type": "customs",
        "controlling_bodies": [
            "UK Home Office",
            "Irish Naturalisation and Immigration Service",
        ],
        "recognition": "universal",
        "crossing_requirements": _req(
            documents=["passport", "national ID (where accepted)"],
            visa_required=False,
            notes="British and Irish citizens move freely; no fixed immigration controls.",
        ),
    },
}


# ── Recognition posture by entity ─────────────────────────────────────────────
#
# Maps entity names (states, territories, claimants) to a recognition status.
# Used by get_recognition_status().

ENTITY_RECOGNITION: dict[str, str] = {
    "France": "universal",
    "Germany": "universal",
    "United States": "universal",
    "Canada": "universal",
    "Brazil": "universal",
    "Argentina": "universal",
    "United Kingdom": "universal",
    "Ireland": "universal",
    "South Korea": "universal",
    "North Korea": "universal",
    "India": "universal",
    "Pakistan": "universal",
    "Morocco": "universal",
    "Kosovo": "partial",
    "Taiwan": "partial",
    "Northern Cyprus": "unrecognised",
    "Turkish Republic of Northern Cyprus": "unrecognised",
    "Sahrawi Arab Democratic Republic": "partial",
    "Western Sahara": "disputed",
    "Transnistria": "de_facto",
    "Abkhazia": "partial",
    "South Ossetia": "partial",
    "Nagorno-Karabakh": "de_facto",
    "Somaliland": "de_facto",
}


# ── Visa / entry regime by passport -> destination ────────────────────────────
#
# A simplified matrix: for each destination (ISO-3166 alpha-2 or common code),
# which passport holders are visa-free vs visa-required. Where a passport is
# not listed, the destination defaults to "visa required (tourist)".

_DESTINATION_DEFAULT_DOC = "passport"


def _visa_entry(
    visa_required: bool,
    visa_type: str | None = "tourist",
    notes: str | None = None,
) -> dict[str, Any]:
    return {
        "documents": [_DESTINATION_DEFAULT_DOC],
        "visa_required": visa_required,
        "visa_type": visa_type,
        "notes": notes,
    }


# Destinations with explicit visa-free passport sets.
# Anything not listed for a destination defaults to visa_required=True.

VISA_REGIME: dict[str, dict[str, Any]] = {
    "FR": {
        "regime": "schengen",
        "visa_free_passports": {
            "US", "CA", "GB", "AU", "NZ", "JP", "DE", "IT", "ES", "NL", "BR", "AR",
        },
        **_visa_entry(visa_required=False, visa_type="tourist",
                      notes="Schengen visa-free for 90/180 days for listed passports."),
    },
    "DE": {
        "regime": "schengen",
        "visa_free_passports": {
            "US", "CA", "GB", "AU", "NZ", "JP", "FR", "IT", "ES", "NL", "BR", "AR",
        },
        **_visa_entry(visa_required=False, visa_type="tourist",
                      notes="Schengen visa-free for 90/180 days for listed passports."),
    },
    "US": {
        "regime": "vwp",
        "visa_free_passports": {
            "GB", "AU", "NZ", "JP", "DE", "FR", "IT", "ES", "NL",
        },
        **_visa_entry(visa_required=True, visa_type="tourist",
                      notes="Visa Waiver Program (ESTA) for listed passports; others require a visa."),
    },
    "CA": {
        "regime": "eta",
        "visa_free_passports": {
            "GB", "AU", "NZ", "JP", "DE", "FR", "IT", "ES", "NL", "US",
        },
        **_visa_entry(visa_required=True, visa_type="tourist",
                      notes="eTA for visa-exempt nationals; others require a visitor visa."),
    },
    "BR": {
        "regime": "mercosur",
        "visa_free_passports": {
            "AR", "UY", "PY", "US", "CA", "GB", "AU", "NZ", "JP",
        },
        **_visa_entry(visa_required=False, visa_type="tourist",
                      notes="Mercosur nationals visa-free; many others visa-free for tourism."),
    },
    "AR": {
        "regime": "mercosur",
        "visa_free_passports": {
            "BR", "UY", "PY", "US", "CA", "GB", "AU", "NZ", "JP",
        },
        **_visa_entry(visa_required=False, visa_type="tourist",
                      notes="Mercosur nationals visa-free; many others visa-free for tourism."),
    },
}


# ── Functions ─────────────────────────────────────────────────────────────────


def lookup_border(region: str) -> dict[str, Any] | None:
    """Look up a border region by name (case-insensitive).

    Returns a copy of the entry from BORDER_DATA, or None if not found.
    """
    key = region.strip()
    # Exact match first.
    if key in BORDER_DATA:
        return dict(BORDER_DATA[key])
    # Case-insensitive fallback.
    lowered = key.lower()
    for name, entry in BORDER_DATA.items():
        if name.lower() == lowered:
            return dict(entry)
    return None


def get_crossing_requirements(origin: str, destination: str) -> dict[str, Any]:
    """Return the crossing requirements for travelling origin -> destination.

    Uses the visa regime matrix; falls back to a default "passport required,
    tourist visa likely" result when the pair is unknown.
    """
    dest = destination.strip().upper()
    regime = VISA_REGIME.get(dest)
    if regime is not None:
        return {
            "documents": regime["documents"],
            "visa_required": regime["visa_free_passports"] is not None
            and origin.strip().upper() not in regime["visa_free_passports"],
            "visa_type": regime["visa_type"],
            "notes": regime.get("notes"),
        }
    if origin.strip().upper() == dest:
        # Internal movement within an unknown entity: no crossing docs required.
        return {
            "documents": [],
            "visa_required": False,
            "visa_type": None,
            "notes": "No international border crossing (same entity).",
        }
    return {
        "documents": [_DESTINATION_DEFAULT_DOC],
        "visa_required": True,
        "visa_type": "tourist",
        "notes": "No visa-regime data for this pair; a passport and tourist visa are typically required.",
    }


def get_recognition_status(entity: str) -> str | None:
    """Return the recognition status for a named entity, or None if unknown."""
    key = entity.strip()
    if key in ENTITY_RECOGNITION:
        return ENTITY_RECOGNITION[key]
    lowered = key.lower()
    for name, status in ENTITY_RECOGNITION.items():
        if name.lower() == lowered:
            return status
    return None


def get_visa_requirements(
    passport_country: str, destination_country: str
) -> dict[str, Any]:
    """Return the visa requirements for a passport holder entering a destination.

    Returns a dict with keys: documents, visa_required, visa_type, notes.
    Unknown passports fall back to visa-required with an error note.
    """
    dest = destination_country.strip().upper()
    passport = passport_country.strip().upper()
    regime = VISA_REGIME.get(dest)
    if regime is not None:
        visa_free = passport in regime["visa_free_passports"]
        return {
            "documents": list(regime["documents"]),
            "visa_required": not visa_free,
            "visa_type": regime["visa_type"],
            "notes": regime.get("notes"),
        }
    return {
        "documents": [_DESTINATION_DEFAULT_DOC],
        "visa_required": True,
        "visa_type": "tourist",
        "notes": (
            f"No visa-regime data for destination {dest!r}; "
            f"passport {passport!r} treated as visa-required."
        ),
        "error": f"unknown destination {dest!r}",
    }
