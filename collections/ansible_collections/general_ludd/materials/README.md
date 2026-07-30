# general_ludd.materials

Materials engineering expert collection (spec `MATE-001` —
[`docs/specs/FEATURE_MATERIALS_ENGINEER.md`](../../../docs/specs/FEATURE_MATERIALS_ENGINEER.md)).

Translates functional, environmental, manufacturing, safety, cost, and lifecycle
requirements into traceable material and process decisions across polymers,
metals, ceramics, composites, and textiles (forming, joining, machining,
additive, and strength assessment).

## Implemented roles (`roles/`)

All 16 spec §3 roles are implemented. Each role carries orchestration only
(parameter validation, output capture, JSON marshalling); chemical/mechanical
logic lives in the Python service layer.

| Role | Purpose |
|---|---|
| `requirements_capture` | Normalize loads, environment, life, geometry, manufacturing, inspection, cost, repair, and regulatory constraints. |
| `material_select` | Rank material families and grades using traceable requirements, uncertainty, and trade-offs. |
| `polymer_process_plan` | Select resin/additives/drying/tooling/process windows for forming plastics. |
| `metal_forming_plan` | Select alloy condition, stock form, forming sequence, heat treatment, and springback controls. |
| `joining_plan` | Compare welding, brazing, soldering, adhesive, and mechanical joining with joint and inspection needs. |
| `welding_plan` | Produce a reviewable preliminary fusion, cold, solid-state, or pressure-welding plan and qualification needs. |
| `machining_plan` | Plan stock, datum, fixturing, milling/turning sequence, tool class, coolant constraints, tolerance, and inspection. |
| `additive_plan` | Select process, orientation, supports, compensation, post-processing, and qualification coupons. |
| `textile_plan` | Select fiber/yarn/architecture and sewing, weaving, knitting, braiding, molding, or finishing process. |
| `molding_plan` | Analyze flow, shrinkage, draft, gates, vents, cure/cooling, residual stress, and defects. |
| `strength_assess` | Check static, fatigue, fracture, creep, buckling, impact, wear, thermal, and environmental limits. |
| `multiphysics_model` | Build traceable structural, thermal, fluid, electromagnetic, cure, forming, or coupled simulation plans. |
| `tolerance_model` | Perform dimensional-chain, distortion, thermal-expansion, process-capability, and assembly analysis. |
| `failure_analyze` | Develop competing failure hypotheses and a nondestructive/destructive test plan without overstating causality. |
| `manufacturing_plan` | Combine processes, quality gates, cost, energy, waste, repair, recycling, and scale-up into a route card. |
| `inspection_plan` | Define incoming, in-process, final, and lifecycle measurements with acceptance and traceability. |

## Python service API (`src/general_ludd/materials/`)

Typed entry points consumed by the roles; the collection never re-implements
the logic below.

| Module | Key exports |
|---|---|
| `core.py` | `normalize_requirements`, `select_materials`, `assess_strength`, `plan_polymer_process`, `plan_metal_forming`, `lookup_material`, `get_properties`, `MATERIALS`, `MATERIAL_FAMILIES`, `ROLES` |
| `contracts.py` | `DesignRequirements`, `MaterialCandidate`, `ProcessPlan`, `EngineeringVerdict`, `SimulationPlan`, `FAILURE_CONSEQUENCES`, `VERDICT_STATES` |
| `material_selection.py` | `screen_candidates`, `rank_candidates`, `resolve_property` |
| `strength.py` | `check_tension`, `check_bending`, `check_shear`, `check_compression`, `check_buckling_euler`, `check_fatigue_sn`, `check_thermal_stress` |
| `polymers.py` / `metals.py` / `additive.py` / `joining.py` / `machining.py` / `textiles.py` | `PolymerProcessAdvisor`, `MetalFormingAdvisor`, `AdditiveManufacturingAdvisor`, `JoiningAdvisor`, `MachiningAdvisor`, `TextileAdvisor` |
| `failure.py` | `FailureAnalyzer` |
| `process_planning.py` | `plan_manufacturing`, `plan_inspection`, `estimate_cost`, `estimate_energy`, `RouteCard` |
| `tolerance.py` | `ToleranceChain`, `assess_assembly`, `process_capability` |
| `property_store.py` | `PropertyStore`, `PropertyRecord`, `ResolvedProperty`, `StoreQuery` |
| `source_registry.py` | `SourceRegistry`, `SourceEntry`, `Authority`, `FreshnessReport` |
| `units.py` | `convert`, `dim_of`, `known_units`, `DimensionMismatch`, `UnknownUnit` |

## Tests

14 unit-test modules under `tests/unit/test_materials_*.py` (core, contracts,
polymers, metals, joining, machining, additive, tolerance, process_planning,
simulation, source_registry, science).

```bash
make test TESTFILE='tests/unit/test_materials_core.py'
make test TESTFILE='tests/unit/test_materials_*.py'
```

## Safety

Every property record identifies units, basis, method, uncertainty, and
condition. Missing condition metadata is flagged `insufficient_context`.
Unit mismatch or missing data blocks a positive verdict (fail-closed per
MATE-SAFE-006).
