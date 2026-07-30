"""Materials engineering package — exposes the core property/access functions
for the top 5 user-visible roles from spec MATE-001 §3, plus the domain
advisors, contracts, and selection/strength utilities.

The knowledge base and role functions live in :mod:`general_ludd.materials.core`.
"""

from __future__ import annotations

from general_ludd.materials.additive import AdditiveManufacturingAdvisor
from general_ludd.materials.contracts import (
    FAILURE_CONSEQUENCES,
    PROCESS_FAMILIES,
    VERDICT_STATES,
    DesignRequirements,
    EngineeringVerdict,
    MaterialCandidate,
    ProcessPlan,
    SimulationPlan,
)
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
from general_ludd.materials.failure import FailureAnalyzer
from general_ludd.materials.joining import JoiningAdvisor
from general_ludd.materials.machining import MachiningAdvisor
from general_ludd.materials.material_selection import (
    rank_candidates,
    resolve_property,
    screen_candidates,
)
from general_ludd.materials.metals import MetalFormingAdvisor
from general_ludd.materials.polymers import PolymerProcessAdvisor
from general_ludd.materials.process_planning import (
    RouteCard,
    estimate_cost,
    estimate_energy,
    plan_inspection,
    plan_manufacturing,
)
from general_ludd.materials.property_store import (
    PropertyRecord,
    PropertyStore,
    ResolvedProperty,
    StoreQuery,
)
from general_ludd.materials.source_registry import (
    Authority,
    FreshnessReport,
    SourceEntry,
    SourceRegistry,
)
from general_ludd.materials.strength import (
    check_bending,
    check_buckling_euler,
    check_compression,
    check_fatigue_sn,
    check_shear,
    check_tension,
    check_thermal_stress,
)
from general_ludd.materials.textiles import TextileAdvisor
from general_ludd.materials.tolerance import (
    ToleranceChain,
    assess_assembly,
    process_capability,
)
from general_ludd.materials.units import (
    DimensionMismatch,
    UnknownUnit,
    convert,
    dim_of,
    known_units,
)

__all__ = [
    "ASSESS_FAIL_CLOSED",
    "FAILURE_CONSEQUENCES",
    "INSUFFICIENT_CONTEXT",
    "INSUFFICIENT_DATA",
    "MATERIALS",
    "MATERIAL_FAMILIES",
    "METAL_FORMING_OPS",
    "POLYMER_PROCESSES",
    "PROCESS_FAMILIES",
    "ROLES",
    "SCHEMA_VERSION",
    "VERDICT_STATES",
    "AdditiveManufacturingAdvisor",
    "Authority",
    "DesignRequirements",
    "DimensionMismatch",
    "EngineeringVerdict",
    "FailureAnalyzer",
    "FreshnessReport",
    "JoiningAdvisor",
    "MachiningAdvisor",
    "MaterialCandidate",
    "MetalFormingAdvisor",
    "PolymerProcessAdvisor",
    "ProcessPlan",
    "PropertyRecord",
    "PropertyStore",
    "ResolvedProperty",
    "RouteCard",
    "SimulationPlan",
    "SourceEntry",
    "SourceRegistry",
    "StoreQuery",
    "TextileAdvisor",
    "ToleranceChain",
    "UnknownUnit",
    "assess_assembly",
    "assess_strength",
    "check_bending",
    "check_buckling_euler",
    "check_compression",
    "check_fatigue_sn",
    "check_shear",
    "check_tension",
    "check_thermal_stress",
    "convert",
    "dim_of",
    "estimate_cost",
    "estimate_energy",
    "get_properties",
    "known_units",
    "list_material_families",
    "lookup_material",
    "normalize_requirements",
    "plan_inspection",
    "plan_manufacturing",
    "plan_metal_forming",
    "plan_polymer_process",
    "process_capability",
    "rank_candidates",
    "resolve_property",
    "screen_candidates",
    "select_materials",
]

__all__.sort()
