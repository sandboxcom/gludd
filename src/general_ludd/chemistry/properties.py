"""CHEM-004 property evidence — measured/predicted values with provenance.

Implements CHEM-004 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §2 and §10.

Every reported value carries a unit, a method, the conditions under which it
was measured or predicted, an uncertainty (which may be zero for defined
constants), and a provenance record pointing at source, locator, citation, and
access date. Per spec §5 ("Conflicting values remain distinct and are compared
by conditions and evidence quality; the newest value does not automatically
win") and CHEM-AT-003, conflicting observations are retained as distinct
records rather than collapsed.

The built-in :data:`PROPERTY_REGISTRY` is a small fixture set sufficient to
exercise the lookup contract and acceptance tests. Real deployments back this
registry with an immutable, content-addressed evidence store (spec §5); the
function signature is stable across both.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from typing import Any

_CORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core.py")


def _load_core():
    spec = importlib.util.spec_from_file_location("chemistry_core_for_properties", _CORE_PATH)
    assert spec is not None and spec.loader is not None, "chemistry core spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_core = _load_core()
SCHEMA_VERSION = _core.SCHEMA_VERSION


def _new_id() -> str:
    return str(uuid.uuid4())


def _obs(
    *,
    value: float,
    unit: str,
    method: str,
    method_id: str,
    conditions: dict[str, Any],
    uncertainty: float,
    locator: str,
    citation: str,
    access_date: str = "2026-07-29",
) -> dict[str, Any]:
    """Build a single property observation record."""
    return {
        "value": value,
        "unit": unit,
        "method": method,
        "method_id": method_id,
        "conditions": dict(conditions),
        "uncertainty": uncertainty,
        "provenance": {
            "source_id": _new_id(),
            "locator": locator,
            "citation": citation,
            "access_date": access_date,
        },
    }


# ---------------------------------------------------------------------------
# Built-in property fixture registry
# ---------------------------------------------------------------------------
# Keyed by (normalized entity name, property name). Each entry is a list of
# observations; conflicting values (different conditions or methods) are kept
# as distinct list members per spec §5 / CHEM-AT-003.

_NIST_WATER = "https://webbook.nist.gov/cgi/cbook.cgi?ID=C7732185"
_NIST_ETHANOL = "https://webbook.nist.gov/cgi/cbook.cgi?ID=C64175"
_NIST_METHANE = "https://webbook.nist.gov/cgi/cbook.cgi?ID=C74828"
_NIST_BENZENE = "https://webbook.nist.gov/cgi/cbook.cgi?ID=C71432"

PROPERTY_REGISTRY: dict[tuple[str, str], list[dict[str, Any]]] = {
    ("water", "boiling_point"): [
        _obs(
            value=373.15,
            unit="K",
            method="measured",
            method_id="nist_webbook@2024",
            conditions={"pressure": "1 atm"},
            uncertainty=0.05,
            locator=_NIST_WATER,
            citation="NIST Chemistry WebBook — water normal boiling point",
        ),
        # Conflicting observation at reduced pressure — retained as distinct
        # evidence rather than collapsed (CHEM-AT-003).
        _obs(
            value=371.15,
            unit="K",
            method="measured",
            method_id="nist_webbook@2024",
            conditions={"pressure": "95 kPa"},
            uncertainty=0.10,
            locator=_NIST_WATER,
            citation="NIST Chemistry WebBook — water boiling point at 95 kPa",
        ),
    ],
    ("water", "melting_point"): [
        _obs(
            value=273.15,
            unit="K",
            method="measured",
            method_id="nist_webbook@2024",
            conditions={"pressure": "1 atm"},
            uncertainty=0.01,
            locator=_NIST_WATER,
            citation="NIST Chemistry WebBook — water normal melting point",
        ),
    ],
    ("water", "density"): [
        _obs(
            value=997.0,
            unit="kg/m^3",
            method="measured",
            method_id="iapws@2018",
            conditions={"temperature": "298.15 K", "pressure": "1 atm"},
            uncertainty=0.5,
            locator=_NIST_WATER,
            citation="IAPWS R6-95 (2018) — density of liquid water at 298.15 K",
        ),
    ],
    ("ethanol", "boiling_point"): [
        _obs(
            value=351.45,
            unit="K",
            method="measured",
            method_id="nist_webbook@2024",
            conditions={"pressure": "1 atm"},
            uncertainty=0.10,
            locator=_NIST_ETHANOL,
            citation="NIST Chemistry WebBook — ethanol normal boiling point",
        ),
    ],
    ("ethanol", "flash_point"): [
        _obs(
            value=286.15,
            unit="K",
            method="measured",
            method_id="closed_cup@ASTM_D56",
            conditions={"pressure": "1 atm"},
            uncertainty=1.0,
            locator=_NIST_ETHANOL,
            citation="ASTM D56 closed-cup flash point — ethanol",
        ),
    ],
    ("methane", "boiling_point"): [
        _obs(
            value=111.66,
            unit="K",
            method="measured",
            method_id="nist_webbook@2024",
            conditions={"pressure": "1 atm"},
            uncertainty=0.02,
            locator=_NIST_METHANE,
            citation="NIST Chemistry WebBook — methane normal boiling point",
        ),
        # Predicted (group-contribution) estimate retained alongside the
        # measured value. Marked ``predicted`` so callers can filter.
        _obs(
            value=114.50,
            unit="K",
            method="predicted",
            method_id="joback_group_contrib@1987",
            conditions={"pressure": "1 atm"},
            uncertainty=5.0,
            locator="Joback KG, Reid RC. Estimation of pure-component properties "
            "from group contributions. Chem Eng Commun. 1987;57(1-6):233-243.",
            citation="Joback-Reid group contribution estimate for methane bp",
        ),
    ],
    ("benzene", "boiling_point"): [
        _obs(
            value=353.23,
            unit="K",
            method="measured",
            method_id="nist_webbook@2024",
            conditions={"pressure": "1 atm"},
            uncertainty=0.05,
            locator=_NIST_BENZENE,
            citation="NIST Chemistry WebBook — benzene normal boiling point",
        ),
    ],
}


def _normalize_entity(entity: str) -> str:
    """Normalize common-name / formula queries for registry lookup.

    Lowercases and strips whitespace. Aliases that map to the same canonical
    record (e.g. ``H2O`` → ``water``) are normalized via ``COMMON_NAMES`` so a
    formula query hits the same observations as the common name.
    """
    cleaned = entity.strip().lower()
    aliases = {
        "h2o": "water",
        "ice": "water",
        "c2h5oh": "ethanol",
        "c2h6o": "ethanol",
        "ch3oh": "methanol",
        "ch4": "methane",
        "c6h6": "benzene",
    }
    return aliases.get(cleaned, cleaned)


def lookup_property(
    entity: str,
    property_name: str,
    *,
    conditions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return property observations with full evidence (CHEM-004).

    Parameters
    ----------
    entity:
        Chemical name (``"water"``), formula (``"H2O"``), or canonical alias.
    property_name:
        Property identifier (``"boiling_point"``, ``"density"`` ...). Case is
        normalized.
    conditions:
        Optional filter. When supplied, only observations whose ``conditions``
        dict matches the requested keys exactly are returned. Keys absent from
        the filter are not constrained.

    Returns
    -------
    dict
        Schema-versioned record with ``observations`` (a list — possibly empty
        — of evidence records), ``status``, and ``limitations``.

    Notes
    -----
    * Conflicting values are retained as distinct observations; the newest
      value never silently wins (spec §5, CHEM-AT-003).
    * An unknown property yields an empty observation list with a degradation
      limitation — never a fabricated value.
    """
    key = (_normalize_entity(entity), property_name.strip().lower())
    registered = PROPERTY_REGISTRY.get(key, [])
    observations = [dict(o) for o in registered]
    limitations: list[str] = []

    if conditions:
        filtered = [o for o in observations if all(o["conditions"].get(k) == v for k, v in conditions.items())]
        if not filtered and observations:
            limitations.append("condition-mismatch: no observations match the requested conditions")
        observations = filtered

    if not observations:
        if not registered:
            limitations.append(f"no-evidence: no observations recorded for {entity}/{property_name}")
            status = "degraded"
        else:
            # Observations exist but none match the requested conditions.
            status = "refused"
        observations = []
    else:
        status = "succeeded"

    return {
        "schema_version": SCHEMA_VERSION,
        "entity": entity,
        "property": property_name,
        "observations": observations,
        "status": status,
        "limitations": limitations,
        "errors": [],
    }


__all__ = [
    "PROPERTY_REGISTRY",
    "lookup_property",
]
