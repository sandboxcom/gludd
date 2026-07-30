"""Materials engineering package — exposes the core property/access functions
for the top 5 user-visible roles from spec MATE-001 §3.

The knowledge base and role functions live in :mod:`general_ludd.materials.core`.
"""

from __future__ import annotations

from general_ludd.materials.core import (
    ASSESS_FAIL_CLOSED,
    INSUFFICIENT_CONTEXT,
    INSUFFICIENT_DATA,
    MATERIAL_FAMILIES,
    MATERIALS,
    METAL_FORMING_OPS,
    POLYMER_PROCESSES,
    ROLES,
    SCHEMA_VERSION,
    assess_strength,
    get_properties,
    list_material_families,
    lookup_material,
    normalize_requirements,
    plan_metal_forming,
    plan_polymer_process,
    select_materials,
)

__all__ = [
    "ASSESS_FAIL_CLOSED",
    "INSUFFICIENT_CONTEXT",
    "INSUFFICIENT_DATA",
    "MATERIALS",
    "MATERIAL_FAMILIES",
    "METAL_FORMING_OPS",
    "POLYMER_PROCESSES",
    "ROLES",
    "SCHEMA_VERSION",
    "assess_strength",
    "get_properties",
    "list_material_families",
    "lookup_material",
    "normalize_requirements",
    "plan_metal_forming",
    "plan_polymer_process",
    "select_materials",
]
