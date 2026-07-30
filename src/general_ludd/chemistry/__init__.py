"""Chemistry expert package.

Implements CHEM-001 (router), CHEM-002 (identity), CHEM-005 (reactions),
CHEM-007 (stoichiometry), and CHEM-008 (safety) per
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md``. The ansible collection at
``collections/ansible_collections/general_ludd/chemistry/`` consumes this
module via the service API; roles carry orchestration only, not independent
chemical logic.
"""

from __future__ import annotations

from general_ludd.chemistry.core import (
    ATOMIC_WEIGHTS,
    COMMON_NAMES,
    HAZARD_REGISTRY,
    INCOMPATIBILITY_MATRIX,
    TASK_CAPABILITY,
    analyze_reaction,
    molar_mass,
    parse_formula,
    resolve_identity,
    route_chemistry_task,
    screen_hazards,
    stoichiometry_dilution,
    stoichiometry_moles,
    stoichiometry_yield,
)

__all__ = [
    "ATOMIC_WEIGHTS",
    "COMMON_NAMES",
    "HAZARD_REGISTRY",
    "INCOMPATIBILITY_MATRIX",
    "TASK_CAPABILITY",
    "analyze_reaction",
    "molar_mass",
    "parse_formula",
    "resolve_identity",
    "route_chemistry_task",
    "screen_hazards",
    "stoichiometry_dilution",
    "stoichiometry_moles",
    "stoichiometry_yield",
]
