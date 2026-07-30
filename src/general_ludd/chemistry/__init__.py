"""Chemistry expert package.

Implements CHEM-001 (router), CHEM-002 (identity), CHEM-005 (reactions),
CHEM-007 (stoichiometry), and CHEM-008 (safety) per
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md``, plus the analytical,
cheminformatics, compute, electrochemistry, entities, inventory, process,
promotion, properties, protocols, provenance, spectroscopy, thermo-kinetics,
and validation modules. The ansible collection at
``collections/ansible_collections/general_ludd/chemistry/`` consumes this
module via the service API; roles carry orchestration only, not independent
chemical logic.
"""

from __future__ import annotations

from general_ludd.chemistry.analytical import (
    CalibrationCurve,
    MethodValidation,
    detect_outliers_grubbs,
    dixon_q,
    subtract_blank,
)
from general_ludd.chemistry.cheminformatics import (
    compute_descriptors,
    enumerate_tautomers,
    standardize_structure,
    substructure_search,
    tanimoto_similarity,
    validate_structure,
)
from general_ludd.chemistry.compute import (
    MolecularDynamicsJob,
    MolecularDynamicsResult,
    QuantumJob,
    QuantumResult,
    validate_md,
    validate_quantum,
)
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
from general_ludd.chemistry.electrochemistry import (
    cell_potential,
    corrosion_rate,
    cycling_degradation,
    electrolysis_energy,
    impedance_basic,
    nernst_equation,
)
from general_ludd.chemistry.entities import (
    EntityRegistry,
    RelatedRecord,
    resolve_entity,
)
from general_ludd.chemistry.inventory import (
    InventoryRecord,
    check_lot_suitability,
)
from general_ludd.chemistry.process import ProcessScaleUp
from general_ludd.chemistry.promotion import (
    ChemistrySnapshot,
    PromotionPipeline,
    canary_hash,
)
from general_ludd.chemistry.properties import lookup_property
from general_ludd.chemistry.protocols import (
    create_protocol_draft,
    issue_approval_token,
    recompute_digest,
    validate_protocol,
)
from general_ludd.chemistry.provenance import (
    ProvenanceChain,
    build_chain,
    verify_chain,
)
from general_ludd.chemistry.reactions import (
    balance_reaction,
    classify_reaction,
    compare_reactions,
)
from general_ludd.chemistry.safety import (
    SafetyScreen,
    check_compatibility,
    classify_risk,
)
from general_ludd.chemistry.schemas import (
    ArtifactInput,
    ChemicalEntity,
    ChemicalStructure,
    ChemistryConstraints,
    ChemistryRequest,
    ChemistryResult,
    CitationRecord,
    ComponentRecord,
    ConditionRecord,
    DataClassification,
    EntityKind,
    ErrorRecord,
    FractionBasis,
    IdentifierRecord,
    IsotopeStatus,
    NameRecord,
    ResultStatus,
    RiskTier,
    SafetyRecord,
    StereoStatus,
    StructureRepresentation,
    TaskKind,
    ValidationRecord,
    ValidationStatus,
    ValueRecord,
)
from general_ludd.chemistry.spectroscopy import SpectraAnalyzer
from general_ludd.chemistry.stoichiometry import (
    calculate_amounts,
    calculate_concentration,
    calculate_yield,
)
from general_ludd.chemistry.thermo_kinetics import (
    arrhenius_rate,
    check_phase_stability,
    energy_balance_check,
    equilibrium_constant,
    ideal_gas_law,
    limiting_reactant,
    mass_balance_check,
)
from general_ludd.chemistry.validation import (
    supports_execution,
    validate_result,
)

__all__ = [
    "ATOMIC_WEIGHTS",
    "COMMON_NAMES",
    "HAZARD_REGISTRY",
    "INCOMPATIBILITY_MATRIX",
    "TASK_CAPABILITY",
    "ArtifactInput",
    "CalibrationCurve",
    "ChemicalEntity",
    "ChemicalStructure",
    "ChemistryConstraints",
    "ChemistryRequest",
    "ChemistryResult",
    "ChemistrySnapshot",
    "CitationRecord",
    "ComponentRecord",
    "ConditionRecord",
    "DataClassification",
    "EntityKind",
    "EntityRegistry",
    "ErrorRecord",
    "FractionBasis",
    "IdentifierRecord",
    "InventoryRecord",
    "IsotopeStatus",
    "MethodValidation",
    "MolecularDynamicsJob",
    "MolecularDynamicsResult",
    "NameRecord",
    "ProcessScaleUp",
    "PromotionPipeline",
    "ProvenanceChain",
    "QuantumJob",
    "QuantumResult",
    "RelatedRecord",
    "ResultStatus",
    "RiskTier",
    "SafetyRecord",
    "SafetyScreen",
    "SpectraAnalyzer",
    "StereoStatus",
    "StructureRepresentation",
    "TaskKind",
    "ValidationRecord",
    "ValidationStatus",
    "ValueRecord",
    "analyze_reaction",
    "arrhenius_rate",
    "balance_reaction",
    "build_chain",
    "calculate_amounts",
    "calculate_concentration",
    "calculate_yield",
    "canary_hash",
    "cell_potential",
    "check_compatibility",
    "check_lot_suitability",
    "check_phase_stability",
    "classify_reaction",
    "classify_risk",
    "compare_reactions",
    "compute_descriptors",
    "corrosion_rate",
    "create_protocol_draft",
    "cycling_degradation",
    "detect_outliers_grubbs",
    "dixon_q",
    "electrolysis_energy",
    "energy_balance_check",
    "enumerate_tautomers",
    "equilibrium_constant",
    "ideal_gas_law",
    "impedance_basic",
    "issue_approval_token",
    "limiting_reactant",
    "lookup_property",
    "mass_balance_check",
    "molar_mass",
    "nernst_equation",
    "parse_formula",
    "recompute_digest",
    "resolve_entity",
    "resolve_identity",
    "route_chemistry_task",
    "screen_hazards",
    "standardize_structure",
    "stoichiometry_dilution",
    "stoichiometry_moles",
    "stoichiometry_yield",
    "substructure_search",
    "subtract_blank",
    "supports_execution",
    "tanimoto_similarity",
    "validate_md",
    "validate_protocol",
    "validate_quantum",
    "validate_result",
    "validate_structure",
    "verify_chain",
]
