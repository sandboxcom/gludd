"""Materials engineering knowledge module for the materials collection.

Standalone accessor for the materials registry, designed to run on a managed
target without the application-layer Python package. Mirrors the public surface
of ``src/general_ludd/materials/core.py``.

Public surface::

    MATERIAL_FAMILIES   tuple of 5 material-family tokens
    ROLES               tuple of 5 role tokens
    MATERIALS           dict of representative material records
    list_material_families() -> tuple
    lookup_material(query)    -> dict | None
    get_properties(material_id) -> list[dict]
"""

from __future__ import annotations

from typing import Any

MATERIAL_FAMILIES: tuple[str, ...] = (
    "polymer",
    "metal",
    "ceramic",
    "composite",
    "textile",
)

ROLES: tuple[str, ...] = (
    "requirements_capture",
    "material_select",
    "polymer_process_plan",
    "metal_forming_plan",
    "strength_assess",
)

MATERIALS: dict[str, dict[str, Any]] = {
    "pa66_gf30": {
        "material_id": "pa66_gf30",
        "designation": "Polyamide 66, 30% glass fiber reinforced",
        "family": "polymer",
        "class": "thermoplastic_reinforced",
        "aliases": ("PA66-GF30", "Nylon 66 GF30"),
        "properties": [
            {
                "name": "yield_strength",
                "value_or_range": 180.0,
                "unit": "MPa",
                "basis": "nominal",
                "method": "ISO 527",
                "uncertainty": 12.0,
                "condition": {"product_form": "injection_molded", "moisture": "conditioned"},
                "state": "ok",
            },
        ],
        "source": {"publisher": "CAMPUS Plastics Database", "revision": "2024"},
    },
    "abs": {
        "material_id": "abs",
        "designation": "Acrylonitrile Butadiene Styrene",
        "family": "polymer",
        "class": "thermoplastic",
        "aliases": ("ABS",),
        "properties": [
            {
                "name": "yield_strength",
                "value_or_range": 45.0,
                "unit": "MPa",
                "basis": "nominal",
                "method": "ISO 527",
                "uncertainty": 5.0,
                "condition": {},
                "state": "insufficient_context",
            },
        ],
        "source": {"publisher": "CAMPUS Plastics Database", "revision": "2024"},
    },
    "aisi_1045": {
        "material_id": "aisi_1045",
        "designation": "AISI 1045 medium carbon steel",
        "family": "metal",
        "class": "ferrous_carbon",
        "aliases": ("1045", "S45C", "C45"),
        "properties": [
            {
                "name": "yield_strength",
                "value_or_range": 310.0,
                "unit": "MPa",
                "basis": "nominal",
                "method": "ASTM A29",
                "uncertainty": 20.0,
                "condition": {"product_form": "cold_drawn", "temper": "as_drawn"},
                "state": "ok",
            },
            {
                "name": "youngs_modulus",
                "value_or_range": 200.0,
                "unit": "GPa",
                "basis": "nominal",
                "method": "ASTM E111",
                "uncertainty": 5.0,
                "condition": {"product_form": "cold_drawn"},
                "state": "ok",
            },
        ],
        "source": {"publisher": "ASM Handbook Vol. 1", "revision": "2023"},
    },
    "aa6061_t6": {
        "material_id": "aa6061_t6",
        "designation": "Aluminum 6061-T6",
        "family": "metal",
        "class": "non_ferrous_aluminum",
        "aliases": ("6061-T6", "AlMg1SiCu"),
        "properties": [
            {
                "name": "yield_strength",
                "value_or_range": 276.0,
                "unit": "MPa",
                "basis": "nominal",
                "method": "ASTM B209",
                "uncertainty": 15.0,
                "condition": {"product_form": "sheet", "temper": "T6"},
                "state": "ok",
            },
        ],
        "source": {"publisher": "ASM Handbook Vol. 2", "revision": "2023"},
    },
}

_ALIAS_INDEX: dict[str, str] = {}
for _mid, _mat in MATERIALS.items():
    _ALIAS_INDEX[_mid.lower()] = _mid
    for _alias in _mat["aliases"]:
        _ALIAS_INDEX[_alias.lower()] = _mid


def list_material_families() -> tuple[str, ...]:
    return MATERIAL_FAMILIES


def lookup_material(query: str) -> dict[str, Any] | None:
    if not query or not query.strip():
        return None
    q = query.strip().lower()
    mid = _ALIAS_INDEX.get(q)
    if mid is None:
        return None
    return MATERIALS[mid]


def get_properties(material_id: str) -> list[dict[str, Any]]:
    mat = MATERIALS.get(material_id) or lookup_material(material_id)
    if mat is None:
        return []
    return list(mat["properties"])
