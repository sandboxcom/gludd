# Feature: Materials Engineering Expert Collection

**Spec ID:** MATE-001  
**Status:** DRAFT — implementation-ready; cited research pass pending  
**Target:** development after `v0.1.0-beta.3`  
**Collection:** `general_ludd.materials`

## 1. Purpose

Gludd SHALL provide a materials-engineering expert that translates functional,
environmental, manufacturing, safety, cost, and lifecycle requirements into
traceable material and process decisions. It SHALL reason across polymers,
metals, ceramics, composites, textiles, adhesives, coatings, and hybrid
assemblies; plan forming and joining; and model whether a proposed build is
strong, manufacturable, inspectable, repairable, and safe.

The expert SHALL report property ranges, conditions, uncertainty, units,
provenance, and assumptions. It SHALL never present a handbook value or
simulation as a certified design, lot-specific test result, welding procedure
qualification, or authority approval.

## 2. Scope

### 2.1 Included materials

- Thermoplastics, thermosets, elastomers, foams, fibers, films, laminates, and
  fiber-reinforced polymers.
- Ferrous and non-ferrous alloys, refractory and precious metals, metal foams,
  amorphous metals, and powder-metallurgy products.
- Glasses, ceramics, cementitious materials, natural materials, wood, paper,
  leather, textiles, composites, adhesives, sealants, coatings, and solders.
- Recycled, bio-derived, graded, porous, architected, and multi-material
  systems, with explicit limitations when data is incomplete.

### 2.2 Included processes

- Plastic extrusion, injection, blow, rotational, compression, transfer,
  thermoforming, vacuum forming, casting, pultrusion, and layup.
- Metal casting, forging, rolling, extrusion, drawing, stamping, bending,
  spinning, hydroforming, heat treatment, and powder metallurgy.
- Fusion, solid-state, cold, pressure, resistance, friction, diffusion,
  ultrasonic, forge, explosion, laser, electron-beam, arc, and thermoplastic
  welding, plus brazing, soldering, adhesive bonding, and mechanical fastening.
- Milling, turning, drilling, boring, reaming, broaching, grinding, honing,
  lapping, EDM, laser, plasma, waterjet, and chemical machining.
- Material extrusion, vat photopolymerization, powder-bed fusion, binder
  jetting, material jetting, sheet lamination, directed-energy deposition, and
  wire-arc additive manufacturing.
- Sewing, stitching, weaving, knitting, braiding, felting, tufting, nonwoven
  formation, textile coating, molding, and composite preform manufacture.

### 2.3 Excluded

- Autonomous control of hazardous machinery, furnaces, lasers, pressure
  vessels, explosive welding, or high-energy test equipment.
- Certification, stamping, or approval of a safety-critical design.
- Substituting generic property data for supplier/lot/condition test evidence.
- Generation of an executable manufacturing procedure without required human
  review, hazard controls, and equipment-specific validation.
- Advice for weapon construction or unsafe circumvention of industrial safety.

## 3. User-visible roles

| Role | Requirement |
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

## 4. Required knowledge domains

### 4.1 Material identity and state

Every property record SHALL identify material family, standardized designation
when known, chemistry or formulation range, supplier/grade if known, product
form, direction, processing history, heat treatment, moisture state,
temperature, strain rate, aging, and test method. A value lacking required
condition metadata SHALL be marked `insufficient_context`.

The expert SHALL support:

- elastic, plastic, viscoelastic, hyperelastic, creep, fatigue, fracture,
  damage, anisotropic, orthotropic, rate-dependent, and temperature-dependent
  behavior;
- phase diagrams, transformations, precipitation, crystallinity, glass
  transition, melting, cure, rheology, grain structure, porosity, residual
  stress, and texture;
- corrosion, oxidation, galvanic interaction, stress-corrosion cracking,
  hydrogen effects, UV/weathering, solvent attack, hydrolysis, moisture uptake,
  radiation, wear, and thermal cycling;
- thermal, electrical, magnetic, optical, acoustic, permeability, fire, smoke,
  toxicity, biocompatibility, and outgassing properties where relevant.

### 4.2 Plastics and polymer forming

The expert SHALL reason about molecular weight, branching, reinforcement,
fillers, plasticizers, stabilizers, drying, melt temperature, mold temperature,
shear heating, viscosity, residence time, degradation, cure kinetics,
shrinkage, warpage, weld/knit lines, sink, voids, fiber orientation, and
regrind. It SHALL distinguish thermoplastic melting from thermoset cure and
shall not recommend an incompatible forming or rework process.

### 4.3 Metals and forming

The expert SHALL reason about alloy/temper, casting and wrought form, rolling
direction, grain flow, work hardening, recrystallization, formability, bend
allowance, springback, forging ratio, solidification, segregation, porosity,
hot tearing, quenching, tempering, solution treatment, aging, distortion, and
machinability.

### 4.4 Joining and welding

The joining model SHALL distinguish:

- fusion processes that melt the base or filler;
- solid-state processes that join below bulk melting;
- cold welding driven by clean intimate contact and pressure;
- pressure-assisted methods including resistance, forge, roll, diffusion,
  friction, ultrasonic, and explosion welding;
- polymer thermal, hot-gas, hot-plate, extrusion, ultrasonic, vibration,
  radio-frequency, laser, and solvent-assisted joining;
- brazing and soldering, adhesive bonding, riveting, bolting, staking,
  crimping, and hybrid joints.

For each candidate it SHALL assess material compatibility, surface preparation,
joint geometry, heat input, restraint, shielding, filler/consumable, heat
affected zone, intermetallics, residual stress, distortion, hydrogen/moisture,
galvanic effects, inspectability, repair, operator qualification, procedure
qualification, and applicable code/standard. Hazardous processes SHALL return
a preliminary engineering plan requiring qualified human approval.

### 4.5 Machining and additive manufacturing

Machining reasoning SHALL include datum schemes, accessibility, fixturing,
rigidity, chatter, cutting-tool material/coating class, chip formation,
work hardening, heat, coolant compatibility, burrs, surface integrity,
allowances, tool wear, tolerance, metrology, and safe process boundaries.

Additive reasoning SHALL include feedstock, machine class, process window,
orientation, anisotropy, supports, scan/toolpath strategy, minimum feature,
porosity, shrinkage, residual stress, distortion compensation, thermal history,
post-cure or heat treatment, depowdering, machining allowance, surface finish,
coupon placement, nondestructive examination, and lot traceability.

### 4.6 Textiles and flexible materials

The expert SHALL represent fiber, filament, staple, yarn, twist, sizing, weave,
knit, braid, stitch, seam, nonwoven, ply, drape, crimp, coating, laminate, and
composite preform. It SHALL reason about directional tensile/tear strength,
abrasion, fatigue, permeability, stretch/recovery, seam efficiency, fraying,
drape, moisture, thermal/fire response, UV aging, wash/chemical exposure, and
manufacturing defects.

### 4.7 Strength and build modeling

The expert SHALL cover tension, compression, shear, torsion, bending, bearing,
contact, adhesive/cohesive failure, fatigue, fracture, creep, relaxation,
buckling, impact, vibration, thermal stress, wear, corrosion allowance, and
damage tolerance. It SHALL distinguish yield, ultimate, proof, endurance, and
allowable values and SHALL never combine incompatible basis or units.

Models SHALL support assemblies, fasteners, welds, seams, adhesives, contacts,
composites, shells, beams, solids, lattice structures, moving mechanisms,
pressure/fluid loads, thermal gradients, electromagnetic heating, curing,
forming, and manufacturing-induced residual stresses.

## 5. Interfaces and data contracts

All values SHALL use explicit units convertible through a single units service.
Inputs and outputs SHALL be versioned, JSON-serializable, and validated.

### 5.1 `DesignRequirements`

```text
schema_version: string
geometry_refs: [{uri, digest, coordinate_system}]
load_cases: [{id, type, magnitude, unit, direction, spectrum, confidence}]
environment: [{factor, range, unit, duration, cycle}]
design_life: {value, unit, reliability_target}
failure_consequence: noncritical | significant | safety_critical
manufacturing: {quantity, rate, processes_allowed, processes_forbidden}
interfaces: [{material, finish, contact, movement}]
tolerances: [{feature, value, unit, statistical_basis}]
inspection: {access, methods_allowed, sampling}
cost_sustainability: {limits, repair, recycled_content, end_of_life}
assumptions: [{id, statement, owner, validation}]
```

### 5.2 `MaterialCandidate`

```text
material_id: stable designation
condition: {product_form, direction, temper_or_cure, moisture, temperature}
properties: [{name, value_or_range, unit, basis, method, uncertainty}]
source: {uri, publisher, revision, retrieved_at, digest, license}
requirement_margins: [{requirement_id, margin, state, calculation_id}]
manufacturing_compatibility: [{process, state, reason}]
joining_compatibility: [{process, pairing, state, reason}]
hazards: [{id, severity, controls, authoritative_source}]
confidence: integer 0..100
unknowns: [{field, impact, required_test}]
```

### 5.3 `ProcessPlan`

```text
plan_id: UUID
process_family: enum
equipment_class: string
material_inputs: [{material_id, lot_required, condition}]
steps: [{id, operation, inputs, parameter_window, hold_point, outputs}]
tooling: [{id, material, geometry_ref, life_assumption}]
controls: [{parameter, sensor, bounds, sampling, reaction_plan}]
inspection: [{stage, method, acceptance, calibration}]
qualification: [{coupon_or_trial, standard_or_internal_method, approver}]
hazards: [{hazard, control, residual_risk, approval}]
provenance: [{source, revision, digest}]
```

### 5.4 `SimulationPlan`

```text
model_id: UUID
question: single falsifiable engineering question
solver_adapter: capability ID
geometry_digest: string
material_models: [{region, model, data_source, calibration_range}]
loads_and_boundaries: [{id, type, value, unit, basis}]
mesh: {element_family, target_size, convergence_plan}
contacts_and_joints: [{regions, model, parameters, evidence}]
coupling: [{physics, direction, time_scale}]
verification: {benchmarks, conservation_checks, convergence}
validation: {experiment, measurements, acceptance}
uncertainty: {variables, distributions_or_bounds, propagation_method}
outputs: [{quantity, location, unit, acceptance}]
```

### 5.5 `EngineeringVerdict`

```text
request_id: UUID
state: infeasible | insufficient_data | candidate | validated_for_scope
candidate_ids: [string]
governing_cases: [{case_id, failure_mode, margin, uncertainty}]
manufacturing_route_id: UUID | null
inspection_plan_id: UUID | null
required_tests: [{id, purpose, specimen, method, acceptance}]
required_human_reviews: [{discipline, reason, state}]
limitations: [stable reason code]
evidence_bundle_uri: URI
```

`validated_for_scope` SHALL mean only that the stated model and evidence meet the
declared acceptance scope. It SHALL NOT mean regulatory or professional
certification.

## 6. Tool and simulator adapters

The domain SHALL use typed adapters and SHALL prefer maintained, validated
tools over custom solvers. Candidate adapter classes include:

- CAD/geometry and meshing;
- linear/nonlinear structural and explicit dynamics;
- heat transfer, CFD, electromagnetic, acoustics, and coupled multiphysics;
- injection/flow/cure, sheet/bulk forming, welding heat-source, machining, and
  additive thermal/distortion simulation;
- electronic-circuit and field co-simulation when a material choice affects an
  enclosure, conductor, dielectric, thermal path, or sensor;
- molecular/chemistry and thermodynamic calculations through the chemistry
  expert boundary;
- astronomy/space-environment data through the relevant domain expert boundary;
- statistics, optimization, uncertainty quantification, and experiment design.

An adapter SHALL declare solver/version, license, supported physics, unit
conventions, determinism controls, resource bounds, checkpoint/restart, input
and output schemas, validation cases, and known limitations.

No model SHALL run merely because a compatible executable is installed. The
expert SHALL first state the engineering question, choose appropriate fidelity,
run dimensional and hand-calculation checks, define verification/validation,
and bound compute resources.

## 7. Decision method

### MATE-DEC-001: Requirements first

Material or process ranking SHALL be invalid until mandatory loads,
environment, life, failure consequence, geometry, quantity, inspection, and
manufacturing constraints are present or explicitly marked unknown.

### MATE-DEC-002: Screening and ranking

The expert SHALL:

1. reject candidates violating a hard constraint;
2. normalize compatible units and conditions;
3. compute traceable performance indices and margins;
4. rank surviving candidates under at least nominal, conservative, and
   sensitivity cases;
5. expose trade-offs and unknowns rather than collapse them into one score;
6. prescribe the smallest test that can resolve a decision-changing unknown.

### MATE-DEC-003: Data hierarchy

For a specific build, lot/condition test data outranks supplier grade data,
which outranks maintained standards/handbooks, which outranks analogous or
model-estimated data. Lower-tier data SHALL be labeled and SHALL not silently
replace higher-tier evidence.

### MATE-DEC-004: Calculation traceability

Every derived value SHALL retain equation/solver ID, inputs with units,
assumptions, numerical precision, software version, and source digests.

## 8. Safety, security, and failure behavior

### MATE-SAFE-001: Safety classification

Safety-critical, regulated, high-pressure, flight, medical, structural,
flammable, toxic, explosive, high-temperature, high-voltage, or human-support
applications SHALL require a qualified human review before an executable plan.

### MATE-SAFE-002: Hazard controls

Plans SHALL identify machine motion, stored energy, heat, pressure, fumes,
dust, sensitizers, solvents, UV/laser/radiation, inert gas, oxygen depletion,
fire/explosion, noise, sharp chips, and ergonomic hazards as applicable.
Missing authoritative hazard data SHALL block an executable process plan.

### MATE-SAFE-003: No fabricated precision

If material condition, property basis, units, source, solver validity, or
boundary conditions are unknown, the expert SHALL widen uncertainty or return
`insufficient_data`. It SHALL not copy a similar grade's nominal value without
labeling it as an analogy.

### MATE-SAFE-004: File and tool isolation

Imported CAD, mesh, solver, image, and machine files SHALL be treated as
untrusted. Parsers and solvers SHALL run in resource-bounded sandboxes with
network off by default. Output paths SHALL be contained, and generated machine
code SHALL never be sent directly to equipment.

### MATE-SAFE-005: Approval boundaries

The expert SHALL separate:

- conceptual recommendation;
- preliminary engineering calculation;
- simulation verified against benchmarks;
- design validated by representative testing;
- process qualified on specified equipment/material/operator;
- production release approved by an authorized human.

Each transition SHALL require its declared evidence and approval.

### MATE-SAFE-006: Fail closed

Unit mismatch, extrapolation beyond calibrated range, nonconverged model,
mesh-dependent result, failed conservation check, missing inspection access,
unqualified joining procedure, unknown material lot, or stale source SHALL
block a positive verdict.

## 9. Zero-downtime manufacturing and model changes

Manufacturing knowledge, model, and recipe changes SHALL use a ZDD-style
promotion protocol so an operating line or validated design is not silently
disrupted:

```text
BASELINE -> OFFLINE_MODEL -> COUPON -> PILOT -> SHADOW_INSPECTION
         -> CONTROLLED_RAMP -> PRODUCTION
                    |             |
                    +-> REVERT <--+
```

### MATE-ZDD-001: Immutable baseline

Material lot, machine, tooling, program, recipe, calibration, inspection plan,
and approved model SHALL be versioned and digest-addressed. Promotion SHALL not
mutate the prior baseline.

### MATE-ZDD-002: Representative evidence

A change SHALL advance only after the declared coupon or pilot represents
governing geometry, process extremes, material condition, and inspection
method. Simulation alone SHALL not approve a production change.

### MATE-ZDD-003: Mixed-version operation

During ramp, parts from old and new routes SHALL remain traceable and
segregatable. Downstream assembly and inspection SHALL accept both or the
change SHALL wait for a controlled stop.

### MATE-ZDD-004: Automatic hold

Out-of-control measurements, process drift, model/measurement disagreement,
unknown lot identity, or stale calibration SHALL stop new-route promotion and
retain the last approved route. Automatic equipment shutdown remains the
machine safety system's responsibility.

### MATE-ZDD-005: Reversion

Reversion SHALL restore the prior digest-addressed recipe and verify first-piece
quality. Parts produced since the last conforming evidence SHALL be quarantined
with full genealogy.

## 10. Observability and evidence

Every recommendation, calculation, simulation, plan, test, and promotion SHALL
emit structured events with `trace_id`, `request_id`, `material_id`, `lot_id`
when applicable, model/plan ID, source digests, units, assumption IDs, software
version, resource use, state, reason code, and evidence URI.

Required events:

```text
materials.requirements.normalized
materials.candidate.screened
materials.candidate.ranked
materials.process.planned
materials.joining.planned
materials.model.created
materials.model.verified
materials.test.requested
materials.test.recorded
materials.uncertainty.updated
materials.route.promoted
materials.route.held
materials.route.reverted
```

Long simulations SHALL stream phase progress and emit a heartbeat at least
every 30 seconds. Solver stdout alone is not observability. Metrics SHALL track
data completeness, decision-changing unknowns, verification/validation state,
solver failure class, prediction error, process capability, scrap/rework,
inspection escapes, and promotion/reversion rate.

## 11. Knowledge freshness and practitioner evidence

The collection SHALL maintain a source registry for standards, handbooks,
material databases, supplier data, peer-reviewed studies, validated solver
benchmarks, equipment documentation, safety data, and public practitioner issue
reports. Each entry SHALL include authority, revision, retrieval time, digest,
license, applicability, uncertainty, and review expiry.

The serialized research follow-up SHALL add cited forum/issue evidence for
long-lived practical failures in at least:

- moisture, drying, shrinkage, warpage, weld lines, and recycled polymer feed;
- cracking, porosity, distortion, hydrogen, dissimilar-metal, and inspection
  limitations in joining;
- chatter, work hardening, heat, tool wear, burrs, and thin-wall distortion;
- additive anisotropy, support removal, porosity, residual stress, and
  feedstock/machine variability;
- textile seam efficiency, fraying, drape, fiber damage, and composite
  delamination;
- FEA boundary-condition, contact, mesh, material-model, and false-convergence
  errors;
- translation from handbook properties to actual lot/process performance.

Each finding SHALL map to requirement IDs and record URL, observation dates
when available, material/process/equipment context, symptom, confirmed cause or
uncertainty, mitigation, and limits. Anecdotes SHALL inform failure cases, not
become property data.

### 11.1 Practitioner evidence: booleans admitted as engineering numbers

- **Requirements:** MATE-SAFE-003, MATE-SAFE-006, and MATE-AT-006.
- **Report and observation dates:** the Stack Overflow question
  [“Why is bool a subclass of int?”](https://stackoverflow.com/questions/8169001/why-is-bool-a-subclass-of-int)
  was opened 2011-11-17, remained active through 2018-01-23, and was reviewed
  for this feature on 2026-08-12.
- **Context and symptom:** a Python user found that an `isinstance(value, int)`
  branch serialized a boolean as an integer. The same long-lived language
  behavior can silently admit `True` as `1` and `False` as `0` into stress,
  load, geometry, or property calculations.
- **Cause and mitigation:** Python deliberately defines `bool` as a subclass of
  `int` for backward compatibility. Materials numerical boundaries therefore
  SHALL reject booleans explicitly before accepting `int`/`float`, reject
  non-finite values, and preserve `fail_closed` precedence for invalid loads.
- **Limits:** the report demonstrates the type-system hazard, not an observed
  materials failure and not a source of property data. Tests remain the
  evidence that each engineering calculation boundary enforces the mitigation.

## 12. Implementation layout

```text
collections/ansible_collections/general_ludd/materials/
├── galaxy.yml
├── README.md
└── roles/
    ├── requirements_capture/
    ├── material_select/
    ├── polymer_process_plan/
    ├── metal_forming_plan/
    ├── joining_plan/
    ├── welding_plan/
    ├── machining_plan/
    ├── additive_plan/
    ├── textile_plan/
    ├── molding_plan/
    ├── strength_assess/
    ├── multiphysics_model/
    ├── tolerance_model/
    ├── failure_analyze/
    ├── manufacturing_plan/
    └── inspection_plan/
src/general_ludd/materials/
├── contracts.py
├── units.py
├── source_registry.py
├── property_store.py
├── material_selection.py
├── polymers.py
├── metals.py
├── joining.py
├── machining.py
├── additive.py
├── textiles.py
├── strength.py
├── tolerance.py
├── failure.py
├── process_planning.py
└── simulation/
    ├── protocols.py
    ├── verification.py
    ├── validation.py
    └── adapters/
tests/{unit,integration,e2e}/materials/
```

Property data SHALL be external, versioned data packages where licensing or
update cadence makes embedding inappropriate. The repository SHALL contain
schemas, small test fixtures, citations, and digests rather than copying
restricted databases.

## 13. Delivery phases

1. **MATE-P1 — Contracts and data integrity:** units, provenance, conditions,
   uncertainty, source registry, and validation fixtures.
2. **MATE-P2 — Selection and strength:** requirements, screening, trade-offs,
   analytical checks, failure modes, and test recommendations.
3. **MATE-P3 — Process experts:** polymers, metals, machining, molding,
   additive, textiles, joining, welding, and inspection.
4. **MATE-P4 — Simulation:** adapter protocols, hand-checks, solver
   verification, convergence, uncertainty, and benchmark corpus.
5. **MATE-P5 — Manufacturing integration:** route cards, tolerance/process
   capability, genealogy, quality holds, and ZDD promotion.
6. **MATE-P6 — Cross-domain validation:** chemistry interfaces, thermal/
   electronic/physics coupling, representative builds, and human approval.

Each phase SHALL be independently deployable behind a default-off capability
flag. Read-only selection MAY ship before executable process planning.

## 14. Measurable acceptance tests

### MATE-AT-001: Units and condition integrity

Property-based tests SHALL generate at least 10,000 compatible and incompatible
unit/condition combinations. Compatible conversions SHALL round-trip within
declared tolerance; incompatible dimensions or missing mandatory material
condition SHALL fail closed.

### MATE-AT-002: Material selection

For at least 100 reviewed golden problems spanning metals, polymers, ceramics,
composites, textiles, and hybrids, every hard-constraint violation SHALL be
rejected and every selected candidate SHALL expose margins, sources,
uncertainty, trade-offs, and required validation tests.

### MATE-AT-003: Process compatibility

Negative fixtures SHALL prove that thermosets are not planned as remeltable,
incompatible polymer joining is rejected, unsuitable heat treatments are
blocked, and dissimilar-material joining flags metallurgical, galvanic,
thermal-expansion, and inspection risks.

### MATE-AT-004: Welding plans

Golden cases SHALL cover fusion, cold, friction, diffusion, resistance,
ultrasonic, pressure, polymer, brazed, soldered, adhesive, and mechanical
joints. Each output SHALL identify qualification, consumable/surface controls,
hazards, likely defects, inspection, repair, and human approval. Unsafe or
underspecified cases SHALL return `insufficient_data`.

### MATE-AT-005: Manufacturing plans

Golden builds SHALL cover injection molding, extrusion, thermoforming, forging,
casting, stamping, milling, turning, powder-bed fusion, material extrusion,
directed-energy deposition, sewing, weaving, composite molding, and hybrid
assembly. Plans SHALL preserve traceability from requirements to each process
control and inspection.

### MATE-AT-006: Analytical benchmarks

Hand calculations for axial, beam, torsion, pressure, thermal expansion,
buckling, contact, fatigue, and tolerance-chain cases SHALL match independently
reviewed references within declared tolerance and SHALL identify invalid
formula applicability.

### MATE-AT-007: Simulation verification

Every solver adapter SHALL pass patch/manufactured-solution or recognized
benchmark tests, mesh/time-step convergence, unit checks, and conservation
checks where applicable. Nonconvergence or range extrapolation SHALL block a
positive verdict.

### MATE-AT-008: Validation and uncertainty

For representative experimental datasets, prediction error and uncertainty
bounds SHALL be reported without hiding outliers. Changing a decision-driving
input across its uncertainty range SHALL update candidate rank and prescribed
tests deterministically.

### MATE-AT-009: ZDD manufacturing promotion

A digital line fixture SHALL run baseline and candidate routes concurrently.
Injected drift, failed inspection, stale calibration, and model disagreement
SHALL halt promotion, retain the prior route, quarantine affected genealogy,
and verify reversion.

### MATE-AT-010: Security and safety

Tests SHALL prove sandbox containment of malicious CAD/mesh/solver inputs,
path and command-injection resistance, bounded resources, secret redaction,
default-off machine output, approval enforcement, and rejection of missing
hazard evidence.

### MATE-AT-011: Quality gate

- Overall changed-code coverage SHALL be at least 85%.
- Every changed production file SHALL be at least 75%.
- Unit, integration, solver-contract, failure-injection, and representative
  build E2E suites SHALL pass with warnings treated as errors.
- Collection build, syntax, lint, typecheck, security scan, license checks,
  source-digest validation, and documentation link checks SHALL pass.
- Numerical regression tests SHALL use stated absolute/relative tolerances and
  SHALL not be updated merely to accept changed output.

## 15. Definition of done

The feature is implemented only when all MATE acceptance tests are automated,
the cited source and practitioner registry is populated, model and process
limitations are visible in every result, machine-executable outputs remain
default-off, safety-critical cases require qualified approval, and the full
project gate is green on the exact commit proposed for merge.
