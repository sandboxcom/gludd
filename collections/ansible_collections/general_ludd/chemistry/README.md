# general_ludd.chemistry

Chemistry expert collection (spec `CHEM-*` —
[`docs/specs/FEATURE_CHEMISTRY_EXPERT.md`](../../../docs/specs/FEATURE_CHEMISTRY_EXPERT.md)).

Decision-support roles for evidence-grounded chemical identity, reaction
reasoning, stoichiometry, and hazard screening.

## Implemented roles (`roles/`)

All 20 capabilities from the spec are implemented as orchestration roles; the
chemical logic lives in `src/general_ludd/chemistry/`.

| Role | Capability | Purpose |
|---|---|---|
| `chemistry_router` | CHEM-001 | Route a chemistry question to the smallest qualified role set. |
| `identity_resolve` | CHEM-002 | Resolve chemical identity preserving stereo / isotope / salt distinctions. |
| `property_lookup` | CHEM-003 | Look up physicochemical properties with units, basis, and uncertainty. |
| `reaction_analyze` | CHEM-005 | Balance, classify, and compare reactions; reason about products and conditions. |
| `stoichiometry` | CHEM-007 | Molar mass, mole/dilution/yield stoichiometry. |
| `hazard_review` | CHEM-008 | Screen hazards and chemical incompatibilities (fail-closed). |
| `analytical_validate` | CHEM-009 | Method validation, calibration curves, outlier detection (Grubbs/Dixon Q). |
| `cheminformatics` | CHEM-010 | Structure standardization, descriptors, tautomers, similarity, substructure search. |
| `electrochemistry` | CHEM-011 | Nernst, cell potential, corrosion rate, electrolysis energy, impedance. |
| `thermo_kinetics` | CHEM-012 | Equilibrium, Arrhenius, energy/mass balance, phase stability, ideal gas. |
| `spectra_analyze` | CHEM-013 | Spectroscopic peak/region analysis. |
| `protocol_draft` | CHEM-014 | Draft a reviewable wet-lab protocol with approval token gating. |
| `inventory_check` | CHEM-015 | Check lot suitability against a target specification. |
| `process_scaleup` | CHEM-016 | Process scale-up heat/mass balance and risk review. |
| `molecular_simulation` | CHEM-017 | MD / quantum job validation. |
| `quantum_workflow` | CHEM-018 | Quantum chemistry workflow orchestration. |
| `chemistry_research` | CHEM-019 | Research discovery for chemistry questions. |
| `chemistry_refresh` | CHEM-020 | Staged refresh of chemistry knowledge base. |
| `chemistry_promote` | CHEM-PROMO | Promote a snapshot to the active alias (human-gated). |
| `tool_discover` | CHEM-TOOLS | Discover mature existing cheminformatics / lab tools before custom code. |

## Python service API (`src/general_ludd/chemistry/`)

The typed service interfaces invoked by the collection. Roles never duplicate
chemical algorithms.

| Module | Key exports |
|---|---|
| `core.py` | `route_chemistry_task`, `resolve_identity`, `analyze_reaction`, `molar_mass`, `parse_formula`, `stoichiometry_moles/dilution/yield`, `screen_hazards`, `ATOMIC_WEIGHTS`, `COMMON_NAMES`, `HAZARD_REGISTRY`, `INCOMPATIBILITY_MATRIX` |
| `schemas.py` | `ChemistryRequest`, `ChemistryResult`, `ChemicalEntity`, `ChemicalStructure`, `SafetyRecord`, `RiskTier`, `TaskKind`, `ResultStatus`, `ValidationRecord` |
| `api.py` | Public API surface for the service |
| `reactions.py` | `balance_reaction`, `classify_reaction`, `compare_reactions` |
| `stoichiometry.py` | `calculate_amounts`, `calculate_concentration`, `calculate_yield` |
| `safety.py` | `SafetyScreen`, `classify_risk`, `check_compatibility` |
| `analytical.py` | `CalibrationCurve`, `MethodValidation`, `dixon_q`, `detect_outliers_grubbs`, `subtract_blank` |
| `cheminformatics.py` | `standardize_structure`, `validate_structure`, `compute_descriptors`, `enumerate_tautomers`, `tanimoto_similarity`, `substructure_search` |
| `electrochemistry.py` | `nernst_equation`, `cell_potential`, `corrosion_rate`, `electrolysis_energy`, `cycling_degradation`, `impedance_basic` |
| `thermo_kinetics.py` | `equilibrium_constant`, `arrhenius_rate`, `energy_balance_check`, `mass_balance_check`, `ideal_gas_law`, `limiting_reactant`, `check_phase_stability` |
| `spectroscopy.py` | `SpectraAnalyzer` |
| `properties.py` | `lookup_property` |
| `entities.py` | `EntityRegistry`, `resolve_entity`, `RelatedRecord` |
| `inventory.py` | `InventoryRecord`, `check_lot_suitability` |
| `process.py` | `ProcessScaleUp` |
| `compute.py` | `QuantumJob/Result`, `MolecularDynamicsJob/Result`, `validate_quantum`, `validate_md` |
| `protocols.py` | `create_protocol_draft`, `validate_protocol`, `issue_approval_token`, `recompute_digest` |
| `provenance.py` | `ProvenanceChain`, `build_chain`, `verify_chain` |
| `promotion.py` | `PromotionPipeline`, `ChemistrySnapshot`, `canary_hash` |
| `validation.py` | `validate_result`, `supports_execution` |
| `policy.py` | Safety policy enforcement |

## Tests

17 chemistry unit-test modules under `tests/unit/test_chemistry_*.py` plus
`test_organic_chemistry.py` and `test_inorganic_chemistry.py`.

```bash
make test TESTFILE='tests/unit/test_chemistry_core.py'
make test TESTFILE='tests/unit/test_chemistry_*.py'
```

## Safety boundary

The expert is **decision support**, not an autonomous laboratory operator.
High-risk and prohibited requests return `status: refused` with a `policy`
limitation rather than an actionable artifact. Wet-lab outputs are drafts
until an identified, qualified human approves the exact version for the
declared facility.

## Quick start

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.chemistry.identity_resolve
      vars:
        identity_query: "water"
        identity_output_dir: /tmp/chemistry
```
