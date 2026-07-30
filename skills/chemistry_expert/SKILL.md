---
name: chemistry-expert
description: "Use for chemical identity resolution, formula parsing, molar mass, stoichiometry (moles/dilution/yield), reaction balancing & classification, hazard screening & compatibility, cheminformatics (SMILES validation, descriptors, Tanimoto similarity, tautomers, substructure search), thermo-kinetics (equilibrium, Arrhenius, phase stability, mass/energy balance), quantum/MD validation, electrochemistry (Nernst, cell potential, corrosion, electrolysis), spectroscopy, analytical method validation, and process scale-up. Trigger keywords: chemistry, chemical, molecule, SMILES, InChI, molar mass, stoichiometry, reaction, balance, yield, limiting reactant, hazard, compatibility, GHS, tautomer, substructure, Tanimoto, descriptor, Arrhenius, equilibrium, Nernst, cell potential, electrolysis, mass spec, HPLC, calibration, protocol, provenance, scale-up."
---

# Chemistry Expert

A full-stack chemistry service: identity resolution → property lookup → reaction
analysis → hazard gating → protocol drafting. Implements spec CHEM-001 through
CHEM-008 from `docs/specs/FEATURE_CHEMISTRY_EXPERT.md`. The typed entry point is
`general_ludd.chemistry.api.ChemistryExpertAPI`; the ansible collection under
`collections/ansible_collections/general_ludd/chemistry/` wraps it and carries
no independent chemical logic.

## When to Use

Any actionable chemistry task: resolving an ambiguous name, computing moles for a
prep, screening reagents for incompatibility, validating a SMILES, drafting a
reaction protocol. If the query is purely about material mechanical/optical
properties, use `materials-engineer` instead.

## Available Roles (task kinds)

The router (`ChemistryRouter`) maps a `TaskKind` to a workflow and a risk tier
before any actionable artifact is produced.

| TaskKind | Workflow | Owning module |
|---|---|---|
| `identity` | identity_resolve | `core.resolve_identity`, `entities.resolve_entity` |
| `property` | property_lookup | `properties.lookup_property` |
| `reaction` | reaction_analyze | `core.analyze_reaction`, `reactions.{balance,classify,compare}` |
| `stoichiometry` | stoichiometry | `core.stoichiometry_{moles,dilution,yield}`, `stoichiometry.*` |
| `hazard` | hazard_review | `safety.{check_compatibility,classify_risk}`, `core.screen_hazards` |
| `protocol` | protocol_draft | `protocols.{create_protocol_draft,issue_approval_token,validate_protocol}` |
| `compute` | quantum_workflow | `compute.{validate_quantum,validate_md}` |
| `spectra` | spectra_analyze | `spectroscopy.SpectraAnalyzer` |
| `analytical` | analytical_validate | `analytical.{dixon_q,detect_outliers_grubbs,subtract_blank}` |
| `electrochemistry` | electrochemistry | `electrochemistry.{nernst_equation,cell_potential,corrosion_rate,electrolysis_energy}` |
| `process` | process_scaleup | `process.ProcessScaleUp` |
| `inventory` | inventory_check | `inventory.check_lot_suitability` |

Cheminformatics (`cheminformatics.{validate_structure,standardize_structure,enumerate_tautomers,substructure_search,compute_descriptors,tanimoto_similarity}`),
thermo-kinetics (`thermo_kinetics.{equilibrium_constant,arrhenius_rate,check_phase_stability,mass_balance_check,energy_balance_check,limiting_reactant,ideal_gas_law}`),
and provenance (`provenance.{build_chain,verify_chain}`) are always-available helpers.

## Service API Entry Points

| Entry point | Purpose |
|---|---|
| `ChemistryExpertAPI(policy)` | Top-level orchestrator: validate → mutation-gate → route → safety stops → dispatch |
| `route_chemistry_task(request)` | Legacy single-shot router in `core` |
| `ChemistryRouter(policy).route(request)` | Returns `WorkflowRoute(workflow, risk_tier, requires_hazard_review)` |
| `ChemistryPolicy` | Constraint check + mutation gate (audit-service fail-closed) |

Request/response shapes: `ChemistryRequest`, `ChemistryResult`, `TaskKind`,
`RiskTier` (`low`/`moderate`/`high`/`prohibited`), `SafetyRecord`, `ChemistryConstraints`.

## Safety Boundaries

- **Risk classification runs before detailed work** and again after identity
  resolution (spec §9). A `high`/`prohibited` tier forces `hazard_review`.
- **Hazard-gated workflows** (`protocol_draft`, `quantum_workflow`,
  `molecular_simulation`, `process_scaleup`) require a preceding `hazard_review`
  at `moderate` risk or worse before returning an actionable artifact.
- **Ambiguous identities** ("ether", "citral", "morphine", or `ambiguous:` prefix)
  stop actionable work and return candidate records for disambiguation.
- **Mutation tasks** require an approval token; if the audit service is
  unavailable the result is `refused` (fail-closed, spec §9).
- Outputs are advisory for screening and computation — never a replacement for
  certified SDS, regulatory review, or lab measurement.

## Usage Examples

```python
from general_ludd.chemistry import ChemistryExpertAPI, ChemistryPolicy
from general_ludd.chemistry.schemas import ChemistryRequest, TaskKind

api = ChemistryExpertAPI(ChemistryPolicy())
result = api.execute(ChemistryRequest(task=TaskKind.stoichiometry, payload={...}))
# result.status in {succeeded, refused, degraded, failed, awaiting_approval}
```

```python
from general_ludd.chemistry import tanimoto_similarity, nernst_equation
sim = tanimoto_similarity("CCO", "CCN")
E = nernst_equation(E0=1.23, n=2, Q=0.1, temperature_k=298.15)
```

## See Also

- `materials-engineer` — mechanical / optical / electronic material properties
- `docs/specs/FEATURE_CHEMISTRY_EXPERT.md` — full capability & safety spec
- `src/general_ludd/physics/analytical_chemistry.py` — mass-spec / GC identification
