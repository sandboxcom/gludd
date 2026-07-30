# Feature: Chemistry Expert Collection and Validated Laboratory/Compute Workflows

**Status: PROPOSED** | **Created: 2026-07-29** | **Target: development**

## 1. Purpose and Safety Boundary

Implement `general_ludd.chemistry`, a collection, service, and skill for
evidence-grounded chemical reasoning, computation, data analysis, protocol
drafting, and experiment review. It must connect literature, structures,
reactions, properties, spectra, simulations, inventories, instruments, and
electronic laboratory records through typed, provenance-preserving workflows.

The expert is decision support, not an autonomous laboratory operator. It cannot
procure chemicals, operate instruments, command robots, alter safety controls,
or authorize an experiment. Wet-lab outputs are drafts until an identified,
qualified human approves the exact version for the declared facility. It refuses
requests that cannot meet hazard, legal, ethical, environmental, provenance, or
validation requirements.

## 2. Feature IDs and Required Outcomes

| ID | Capability | Required outcome |
|----|------------|------------------|
| `CHEM-001` | Expert router | Route chemistry tasks by domain, risk, evidence needs, and available tools |
| `CHEM-002` | Chemical identity | Resolve structures and names without collapsing stereochemistry, isotope, salt, or mixture distinctions |
| `CHEM-003` | Literature research | Retrieve and cite primary literature, standards, databases, patents, corrections, and user issue reports |
| `CHEM-004` | Property evidence | Report measured/predicted properties with method, conditions, uncertainty, and provenance |
| `CHEM-005` | Reaction reasoning | Balance, map, classify, search, and compare reactions while preserving atom and mass accounting |
| `CHEM-006` | Protocol drafting | Produce versioned, facility-aware draft procedures with approvals and stop conditions |
| `CHEM-007` | Stoichiometry | Calculate amounts, concentrations, yield, uncertainty, units, and scale limits |
| `CHEM-008` | Safety and compatibility | Screen hazards, incompatibilities, waste, controls, and emergency requirements |
| `CHEM-009` | Inventory | Query lot, purity, location, expiry, restrictions, and chain-of-custody without autonomous procurement |
| `CHEM-010` | Cheminformatics | Validate, transform, search, and analyze chemical structures and libraries |
| `CHEM-011` | Quantum chemistry | Prepare, execute, parse, and validate electronic-structure calculations through adapters |
| `CHEM-012` | Molecular simulation | Prepare, execute, analyze, and reproduce molecular dynamics/free-energy workflows |
| `CHEM-013` | Thermodynamics/kinetics | Model equilibria, phases, rates, transport, reactors, and uncertainty |
| `CHEM-014` | Spectroscopy | Predict, process, assign, compare, and report spectra with raw-data lineage |
| `CHEM-015` | Analytical chemistry | Build calibrations, quantify analytes, validate methods, and detect out-of-range use |
| `CHEM-016` | Electrochemistry | Model and analyze cells, electrolysis, impedance, cycling, corrosion, and interfaces |
| `CHEM-017` | Process and scale-up | Evaluate heat/mass transfer, mixing, pressure, runaway, separations, and scale-dependent hazards |
| `CHEM-018` | Tool/resource discovery | Select maintained databases, libraries, engines, instruments, and helper scripts before custom code |
| `CHEM-019` | Provenance and validation | Make every reported value traceable to source, method, conditions, code, and raw artifact |
| `CHEM-020` | Self-improvement | Stage new evidence, methods, tools, and evaluations without silently changing active behavior |
| `CHEM-021` | Observability | Expose safe, correlated progress and audit evidence for every workflow |
| `CHEM-022` | Zero-downtime delivery | Promote knowledge, models, parsers, and workflows with canary and atomic rollback |
| `CHEM-023` | Lab-on-chip campaigns | Plan, simulate, observe, and optimize approval-gated microfluidic experiments |
| `CHEM-024` | Cross-expert scientific campaigns | Compose chemistry, materials, AI/ML, automation, and instrument experts through typed handoffs |

## 3. Expert Composition

The skill is `skills/chemistry_expert/SKILL.md`. Runtime code lives under
`src/general_ludd/chemistry/`. All collection roles use the same service APIs;
Ansible task files contain orchestration rather than independent chemical logic.

| Role | Purpose |
|------|---------|
| `chemistry_research` | Search, normalize, rank, and cite chemistry evidence |
| `identity_resolve` | Resolve names, identifiers, structures, stereochemistry, isotopes, salts, and mixtures |
| `property_lookup` | Retrieve measured and predicted property values with conditions and uncertainty |
| `reaction_analyze` | Balance, map, classify, compare, and validate reactions |
| `protocol_draft` | Create an approval-gated draft protocol from evidence and facility constraints |
| `stoichiometry` | Perform unit-aware mass, mole, concentration, yield, and uncertainty calculations |
| `hazard_review` | Review hazards, incompatibilities, controls, waste, transport, and emergency information |
| `inventory_check` | Resolve permitted inventory records and lot-specific suitability |
| `cheminformatics` | Standardize, search, enumerate, descriptor, similarity, and library-quality workflows |
| `quantum_workflow` | Prepare and validate quantum-chemistry engine jobs and results |
| `molecular_simulation` | Prepare and validate molecular dynamics and free-energy jobs |
| `thermo_kinetics` | Thermodynamic, kinetic, phase, transport, and reactor calculations |
| `spectra_analyze` | Process and assign NMR, MS, IR/Raman, UV-Vis, X-ray, and related data |
| `analytical_validate` | Calibration, quantification, uncertainty, detection limits, and method validation |
| `electrochemistry` | Cell, cycling, voltammetry, impedance, electrolysis, and corrosion workflows |
| `process_scaleup` | Scale-dependent mass/heat transfer, mixing, separation, pressure, and runaway review |
| `tool_discover` | Evaluate databases, engines, libraries, formats, instrument integrations, and build helpers |
| `chemistry_refresh` | Stage evidence/method/tool/evaluation updates and run promotion gates |
| `chemistry_promote` | Shadow, canary, atomically promote, and roll back versioned expert assets |
| `lab_automation_plan` | Compile a safe, vendor-neutral protocol graph and approved adapter command plan |
| `instrument_observe` | Ingest immutable instrument state, calibration, microscopy, sensor, and deviation records |
| `campaign_optimize` | Propose bounded next experiments using declared design/active-learning methods |

High-risk tasks always compose `hazard_review` before `protocol_draft`,
`quantum_workflow`, `molecular_simulation`, or `process_scaleup` can return an
actionable artifact.

## 4. Chemical Data Model and Interfaces

### 4.1 Chemical entity

```json
{
  "schema_version": "1.0",
  "entity_id": "uuid",
  "kind": "compound|mixture|polymer|material|reaction|biomolecule",
  "names": [{"value": "string", "type": "preferred|systematic|synonym", "source_id": "uuid"}],
  "structure": {
    "representation": "smiles|cxsmiles|inchi|molfile|cif|sequence|composition",
    "value": "string",
    "canonicalizer": "capability-id@version",
    "stereochemistry": "specified|partial|unknown",
    "isotopes": "specified|natural|unknown",
    "charge": 0
  },
  "components": [{"entity_id": "uuid", "fraction": 0.0, "basis": "mass|mole|volume", "uncertainty": 0.0}],
  "identifiers": [{"scheme": "string", "value": "string", "source_id": "uuid"}],
  "validation": [{"check": "string", "status": "pass|fail|warning"}]
}
```

Canonicalization never erases the submitted representation. Tautomers,
protomers, conformers, stereoisomers, isotopologues, salts, solvates, and
mixtures are related records, not silently interchangeable strings.

### 4.2 Chemistry request

```json
{
  "schema_version": "1.0",
  "request_id": "uuid",
  "tenant_id": "string",
  "task": "identity|research|property|reaction|protocol|stoichiometry|hazard|inventory|compute|spectra|analytical|electrochemistry|process|lab_campaign",
  "entities": ["entity-id"],
  "inputs": [{"uri": "artifact://...", "sha256": "hex", "media_type": "string"}],
  "conditions": [{"name": "temperature", "value": 298.15, "unit": "K", "uncertainty": 0.1}],
  "facility_profile_id": "string|null",
  "constraints": {
    "deadline_s": 300,
    "budget_usd": 0,
    "data_classification": "public|internal|confidential|restricted",
    "allowed_tools": ["capability-id"],
    "allowed_licenses": ["SPDX-id"]
  },
  "approval_token": null
}
```

### 4.3 Chemistry result

```json
{
  "schema_version": "1.0",
  "request_id": "uuid",
  "run_id": "uuid",
  "status": "succeeded|degraded|refused|failed|awaiting_approval",
  "summary": "string",
  "values": [{"name": "string", "value": 0.0, "unit": "string", "uncertainty": 0.0, "conditions": ["condition-id"], "method_id": "string"}],
  "artifacts": [{"uri": "artifact://...", "sha256": "hex", "media_type": "string"}],
  "citations": [{"source_id": "uuid", "locator": "string", "claim_ids": ["string"]}],
  "safety": {"risk_tier": "low|moderate|high|prohibited", "review_id": "uuid", "approvals": ["uuid"]},
  "verification": [{"check": "mass_balance", "status": "pass|fail|not_run", "artifact_uri": "artifact://..."}],
  "limitations": ["string"],
  "errors": [{"code": "stable.code", "retryable": false, "message": "safe string"}]
}
```

Every numerical result carries units, conditions, method, uncertainty or an
explicit reason it is unavailable, and provenance. The system rejects unitless
inputs where physical meaning depends on units.

### 4.4 Interchange and handoff contracts

Quantities use UCUM codes and retain the original unit string; quantity-kind and
dimension mappings may use a pinned QUDT vocabulary. Structure interchange
supports source-preserving InChI, SMILES/CXSMILES, SDF/Molfile, CIF, and declared
domain formats. Analytical adapters declare exact support for JCAMP-DX, AnIML,
mzML/mzQC, and vendor formats rather than treating an extension as proof of
compatibility. Round trips compare identity, stereo/isotope/charge, units,
dimensions, arrays, calibration, method metadata, and raw checksums.

Cross-expert work uses a versioned handoff with producer/consumer capability and
schema digests, entity and artifact IDs/hashes, UCUM/QUDT/ontology versions,
conditions, assumptions, uncertainty, evidence, safety tier, approvals,
delegated permissions, deadline/lease, retry/idempotency rules, and
compensation/safe-state action. Free text cannot carry a quantity, identity,
approval, executable instruction, or actuation authority that is absent from
the typed fields.

## 5. Knowledge and Resource Registries

Immutable registries cover:

- chemical entities, identifier mappings, structures, mixtures, lots, and purity;
- property observations and predictions with methods and conditions;
- reactions, atom mappings, procedures, yields, and negative results;
- hazards, incompatibilities, controls, exposure limits, waste, and transport;
- spectra and instrument/raw-data artifacts;
- computational methods, basis/force fields, engine adapters, and reference cases;
- literature, standards, databases, patents, corrections/retractions, code, and
  representative long-lived forum/issue reports;
- facility capabilities, approved instruments, policies, and qualified reviewers.

Each record includes stable ID/version, SHA-256, source locator, license/use
rights, creator/importer, timestamps, supersedes/retraction links, validation
state, uncertainty, and policy decision. Conflicting values remain distinct and
are compared by conditions and evidence quality; the newest value does not
automatically win.

A research source also records canonical URL/DOI/repository, source class,
publisher/authors, published/updated/accessed times, snapshot digest or locator,
release/commit, SPDX expression or terms URI, separate index/train/quote/
redistribute rights, robots/terms outcome, supported claim IDs, correction and
retraction state, replication evidence, freshness deadline, last revalidation,
and tombstone reason. Unknown rights default to citation-only metadata and
exclude content from training or redistribution.

## 6. Literature Research and Self-Improvement

`chemistry_research` searches primary literature, reviews, standards bodies,
official databases, patents, vendor instrument documentation, reference data,
source repositories, correction/retraction feeds, and user forums/issues.
Search results are evaluated for identity match, experimental/computational
method, conditions, uncertainty, sample provenance, reproducibility, recency,
authority, independence, and licensing.

All retrieved content is untrusted. Documents, data files, notebooks, and
repository examples cannot modify prompts, policy, permissions, approval state,
or active expert assets. Macros and downloaded code are never executed during
ingestion.

`chemistry_refresh`:

1. Generates queries from knowledge gaps, conflicts, stale sources, failed
   workflows, new citations, user requests, and benchmark regressions.
2. Fetches evidence into a quarantined content-addressed store.
3. Validates identity, units, conditions, license, provenance, malware status,
   duplicates, corrections, and retractions.
4. Produces a signed proposal mapping evidence to exact knowledge/method/test
   changes.
5. Runs fixed and newly proposed evaluation cases without modifying production.
6. Requires qualified human approval for safety, procedure, executable,
   computational-method, or policy changes.
7. Shadows and canaries an immutable snapshot, then atomically promotes or
   rolls back.

The expert cannot approve its own proposal. A rejected or retracted source is
retained as negative evidence so it is not reintroduced on the next refresh.

Research queries derive from ambiguous identities, conflicting values, stale
safety evidence, unsupported method conditions, failed validation slices, new
citations, and explicit capability gaps. Each bounded pass searches at least one
primary-literature index, official database/standard, maintained code or issue
tracker, and correction/retraction channel; reconciles DOI, structure, registry,
sample, method, and version identities; seeks contrary evidence; and stops at a
declared time, source-count, novelty, or confidence budget. Results distinguish
measurement, database assertion, calculation, prediction, model inference,
operator report, and unresolved conflict.

Security/safety sources and retractions refresh within 24 hours, API/tool
records within 7 days, and papers/standards/reference data within 30 days unless
a stricter source-specific deadline applies. Stale evidence remains visible but
cannot solely support a high-risk protocol or safety decision. A source rights
change or retraction opens an impact graph over entities, properties, protocols,
datasets, models, evaluations, and active aliases.

## 7. Computational Chemistry

### 7.1 Tool selection

The collection integrates maintained engines and libraries through adapters
rather than reimplementing mature chemistry algorithms. Selection records task
fit, method/fidelity, supported elements and boundary conditions, license,
maintenance/security history, reproducibility, file formats, platform and
accelerator support, citation requirements, validation corpus, and exit path.

Executable inputs run in a sandbox with pinned engine/container digest, denied
network by default, explicit mounts, resource/time/output limits, and sanitized
environment. Unsafe serialized objects and unreviewed plugin code are refused.

### 7.2 Cheminformatics

Required workflows include parse/validate, standardization without source loss,
tautomer/protomer/stereoisomer enumeration, substructure and similarity search,
descriptors/fingerprints, conformers, reaction transforms, library filters,
duplicate analysis, and structure/file conversion. Each transform records the
tool/version, parameters, warnings, and parent/child entity relation.

### 7.3 Quantum chemistry

The adapter contract covers geometry, charge/multiplicity, method, basis,
effective core potential, solvent/environment, relativistic treatment, grid,
dispersion, convergence, excited-state/property requests, resources, and restart
files. Results preserve full engine output plus parsed energies, structures,
frequencies, populations, orbitals/properties, convergence, and diagnostics.

Validation checks include electron/spin consistency, geometry sanity,
convergence, imaginary frequencies relative to job intent, energy/unit
consistency, symmetry, basis/method compatibility, and reference calculations.
An unconverged job never yields an unqualified property.

### 7.4 Molecular simulation

The workflow records topology, force field/version, parameter provenance,
coordinates, boundary conditions, ensemble, thermostat/barostat, constraints,
time step, seed, equilibration, production, sampling, analysis, and hardware.
It supports classical/ab-initio/coarse-grained MD and permitted free-energy
methods through capability adapters.

Validation includes topology/charge, missing parameters, minimization,
temperature/pressure/density stability, energy drift, sampling/convergence,
replicate agreement, finite-size effects, and uncertainty. Trajectories and
checkpoints are content-addressed and restartable.

### 7.5 Thermodynamics, kinetics, and process models

Models must identify equations of state/activity/fugacity treatment, property
sources, phases, reactions, transport correlations, reactor/separation model,
initial/boundary conditions, solver tolerances, sensitivity, and uncertainty.
The expert verifies conservation of mass, atoms, charge, and energy where
applicable, dimensional consistency, limiting cases, phase stability, and
solver convergence.

Scale-up results explicitly consider mixing, mass/heat transfer, surface/volume
effects, pressure, gas evolution, accumulation, exotherm/runaway, relief,
materials compatibility, separations, emissions, and waste. A lab-scale
procedure cannot be linearly scaled into an executable plan.

## 8. Experimental and Analytical Workflows

### 8.1 Protocol drafts

A protocol draft contains: objective, evidence, exact entity/lot identities,
quantities with units/uncertainty, equipment and calibration, facility controls,
ordered operations, parameter ranges, sampling, in-process checks, stop
conditions, quench/workup, waste streams, emergency actions, expected results,
deviations, approver roles, and immutable version digest.

Changes after approval invalidate approval. Execution-facing export requires a
facility profile, current inventory and safety review, trained operator identity,
and approval token bound to the exact digest. This specification does not grant
Gludd authority to transmit the protocol to equipment.

### 8.2 Instrument and raw-data lineage

Instrument adapters are read-only by default. Raw files are immutable and retain
instrument ID, software/firmware version, method file digest, calibration and
maintenance status, operator, sample/lot, acquisition time, timezone, and
checksums. Processing creates child artifacts with an operation graph; it never
overwrites raw data.

### 8.3 Spectroscopy and analytical validation

Supported families include NMR, MS, IR/Raman, UV-Vis/fluorescence, X-ray
diffraction/scattering, chromatography, elemental/thermal analysis, microscopy,
and electrochemical measurements. Each parser declares supported vendor/open
formats and fails explicitly on unsupported versions.

Analysis records preprocessing, calibration, baseline, alignment, peak
detection/integration/fitting, assignment/search, standards, blanks, controls,
replicates, and uncertainty. Quantification requires range-appropriate
calibration, residual review, detection/quantitation limit method, recovery,
precision, specificity, robustness, and outlier policy. Extrapolation outside a
validated range is visibly flagged and cannot return `succeeded`.

### 8.4 Electronic laboratory record

Each workflow emits a tamper-evident record linking request, protocol version,
approvals, entities/lots, raw artifacts, transformations, tools, parameters,
environment, observations, deviations, results, failures, citations, and
signatures. Timestamps are normalized without discarding original timezone.
Corrections append signed amendments; they do not rewrite history.

### 8.5 Lab-on-chip and automated campaign protocol

Gludd separates scientific intent, vendor-neutral protocol, adapter translation,
human arming, commanded state, observed state, and derived result. Chemistry
owns entity, compatibility, hazard, waste, and scientific-validity decisions.
The AI/ML expert may optimize a bounded design; materials and process experts
may assess chip, seal, tubing, solvent, temperature, pressure, adsorption, and
fabrication compatibility. No expert inherits another expert's authority.

The required state machine is `draft -> simulated -> safety_reviewed ->
facility_approved -> reserved -> calibrated -> armed -> executing -> paused |
aborted | completed -> safe_state -> reconciled`. Only a qualified human can
create the short-lived arm token bound to protocol, adapter, device, facility,
safe domain, and expiry. Restart after pause or communication loss requires
observed-state reconciliation and a new token when policy demands it.

The campaign record declares objective and response variables, factors and safe
domain, chip/consumable/lot maps, reagent identity/purity, volume and dead-volume
budgets, coordinate frame, pressure/flow/temperature limits, mixing and
residence-time model, calibration, blanks/controls/replicates, sensor range and
sampling, carryover/cleaning, waste and containment, uncertainty target,
acceptance and stopping rules, maximum experiments/material/time, and
compensation/safe-state actions.

A pinned adapter may translate the approved plan to SiLA 2, Opentrons, DropBot,
Fluigent, or another validated capability. Simulation is mandatory before a
hardware export, but never proves hardware safety. During execution, every
bounded command has preconditions, acknowledgement timeout, idempotency key,
maximum retry, observed postcondition, and immutable event. Bubble, clog, leak,
pressure/temperature/volume excursion, sensor drift/saturation, image anomaly,
contamination, missed heartbeat, calibration expiry, or interlock change pauses
the campaign and triggers the predeclared safe state without model improvisation.

An active learner or Bayesian optimizer may rank the next candidate only inside
the approved safe domain and must expose acquisition method, uncertainty,
constraints, excluded regions, and stop reason. It cannot widen the domain,
approve, arm, transmit, suppress a failed control, or convert simulation output
into an observation. Final reconciliation accounts for material, samples,
waste, deviations, raw data, derived data, approvals, instrument state, and
safe-state evidence.

### 8.6 Cross-expert scientific campaign

The conductor compiles an acyclic capability graph and validates every handoff
before dispatch. A representative materials-discovery graph may compose:
literature/identity and hazard review; materials compatibility; computational
chemistry and multiphysics simulation; protocol design; lab-on-chip execution;
microscopy/spectroscopy interpretation; uncertainty-aware statistical analysis;
and candidate selection. Parallel results join only after units, identities,
conditions, fidelity, provenance, safety, and uncertainty invariants pass.

Admission calculates maximum cost, sample/waste, privilege, and time. Fan-out,
retry, and recursion are bounded; cancellation propagates; leases are released;
partial, refused, degraded, skipped, and compensated nodes remain explicit. A
failed domain expert cannot be replaced by a general model's guess. High-impact
claims require an independent validator, and a candidate-generating expert
cannot be its own safety reviewer, evaluator, or approver.

## 9. Chemical Safety, Security, and Failure Behavior

Risk classification occurs before detailed workflow generation and again after
identity resolution. Policy considers acute/chronic toxicity, exposure route,
flammability, explosivity, pyrophoricity, oxidizing/reducing behavior,
water/air reactivity, pressure, radiation, biological hazard, incompatibility,
scale, concentration, temperature, energy, waste, environmental persistence,
transport, legal controls, and facility capability.

| Condition | Required behavior |
|-----------|-------------------|
| Ambiguous chemical identity | Stop actionable work; request disambiguation with candidate records |
| Missing current hazard or incompatibility evidence | Refuse protocol/scale-up; research may continue |
| Facility lacks required control or trained approver | Return `refused`, never suggest bypassing the control |
| Prohibited or unauthorized high-risk request | Refuse actionable detail, record policy decision, offer safe high-level context |
| Inventory lot expired, restricted, or wrong purity | Exclude lot and require review; never substitute silently |
| Unit, concentration, scale, or condition ambiguity | Fail validation before calculation/protocol generation |
| Atom/mass/charge/energy imbalance | Mark result failed unless an explicitly modeled open-system term explains it |
| Engine nonconvergence or parser uncertainty | Preserve diagnostics; do not report a value as validated |
| Instrument calibration/maintenance invalid | Quarantine quantitative result and require review |
| Source retracted or identity mismatch | Exclude from supporting evidence and open impact review |
| Data/license/provenance gap | Quarantine artifact; block training, publication, and promotion |
| Prompt injection or malicious file | Treat as data, quarantine as needed, never change policy or execute content |
| Audit/policy service unavailable | Fail closed for mutation and execution-facing export |
| Partial job/timeout | Terminate children, preserve bounded artifacts, mark incomplete, release resources |
| New snapshot regression | Keep active alias and emit failed-promotion event |
| Device heartbeat, acknowledgement, or state mismatch | Stop commands, enter the declared safe state, expire the arm token, reconcile observations |
| Bubble, clog, leak, drift, saturation, or interlock change | Pause/abort at the configured threshold; quarantine affected samples and results |
| Cross-expert schema, unit, identity, or fidelity mismatch | Refuse the handoff and preserve both records; never coerce or guess |
| Active learner proposes outside safe domain | Reject the point, stop the campaign, and open a policy/optimizer regression |

Secrets, personal data, unpublished formulations, sample identities, and
regulated records are encrypted and tenant-isolated. Logs contain stable IDs and
safe error codes, not sensitive formulas, structures, protocols, or raw spectra.

## 10. Validation Strategy

Every workflow declares validators before execution. At minimum:

- identity: round-trip parsing, formula/charge/isotope/stereo checks, independent
  identifier cross-check when available;
- reactions: atom mapping, elemental/mass/charge balance, stoichiometric limits,
  selectivity/yield definitions, condition matching;
- numerical work: units, significant figures, uncertainty propagation,
  conservation, limiting cases, convergence, sensitivity;
- computation: engine/version/input digest, reference cases, replicate or
  alternative-method comparison appropriate to claimed fidelity;
- experiments: controls, blanks, standards, calibration, replicate plan,
  acceptance range, deviation handling;
- evidence: claim-to-source locator, identity and condition match, correction
  and retraction check, license and access date;
- ML predictions: applicability domain, held-out evaluation, calibration,
  uncertainty, leakage and distribution-shift checks.

Validation status is `validated`, `provisional`, `invalid`, or `not_applicable`;
only `validated` results may support execution-facing artifacts.

## 11. Zero-Downtime Delivery

Chemical knowledge snapshots, identifier maps, property/reaction indexes,
parsers, computational adapters, evaluation suites, and safety rules are
immutable and independently versioned. Promotion uses build, offline validation,
shadow read, stable-hash canary, metric comparison, atomic alias swap, and
automatic rollback while the prior version remains warm.

Requirements:

- no accepted request is dropped during promotion;
- each result uses exactly one declared snapshot set;
- in-flight requests finish on the versions recorded at admission;
- safety-policy updates may tighten immediately but cannot loosen without
  approval and canary evidence;
- rollback begins within 60 seconds of a hard threshold breach;
- the prior two known-good versions remain recoverable;
- schema migrations use expand/migrate/contract and preserve old readers until
  their supported lifetime ends.

## 12. Observability

Every request emits correlated, structured events for routing, identity,
evidence retrieval, safety classification, approvals, tool selection, job
phases, artifacts, validators, promotion, rollback, and terminal status.
Long-running operations emit progress at least every 30 seconds.

Required metrics:

- identity ambiguity and resolution rate;
- evidence age, citation coverage, conflict, correction, and retraction rate;
- hazard-review tier, missing-control, refusal, and approval latency;
- job queue/runtime, convergence, retry, timeout, resource, and cost;
- atom/mass/charge/energy balance failures and unit-validation failures;
- parser version/unsupported-format/error rate;
- calibration, control, replicate, and quantitative-validation outcomes;
- snapshot/canary version, regression delta, rollback and recovery time;
- provenance completeness and orphan-artifact count.

Metric labels are bounded. Chemical structures, formulas, lot IDs, protocol
text, source URLs, sample names, and artifact digests are not metric labels.

## 13. Implementation Sequence

| Phase | Feature IDs | Deliverables |
|-------|-------------|--------------|
| A | 001-004, 018-022, 024 | Schemas, entity/evidence registries, typed expert DAGs, router, policies, research staging, audit, ZDD aliases |
| B | 005-010 | Reaction, protocol, stoichiometry, safety, inventory, and cheminformatics workflows |
| C | 011-013 | Quantum, molecular simulation, thermodynamics, kinetics, and validation adapters |
| D | 014-017 | Spectroscopy, analytical, electrochemistry, process/scale-up, and ELN lineage |
| E | 023-024 and all | Lab campaign/cross-expert integration, Molecule, safety/security/chaos tests, canary/rollback rehearsal |

All mutation and experimental-export capabilities are disabled by default.
Phases land as independently reversible changes and do not alter the current
release or existing provider configuration.

## 14. File Plan

```text
collections/ansible_collections/general_ludd/chemistry/
├── galaxy.yml
├── README.md
├── roles/<role>/{defaults,tasks}/main.yml
└── molecule/<role>/{converge,verify}.yml
skills/chemistry_expert/SKILL.md
src/general_ludd/chemistry/
├── api.py
├── schemas.py
├── router.py
├── entities.py
├── evidence.py
├── properties.py
├── reactions.py
├── protocols.py
├── stoichiometry.py
├── safety.py
├── inventory.py
├── cheminformatics.py
├── compute.py
├── thermo_kinetics.py
├── spectroscopy.py
├── analytical.py
├── electrochemistry.py
├── process.py
├── lab_automation.py
├── campaign.py
├── orchestration.py
├── provenance.py
├── validation.py
├── promotion.py
└── policy.py
tests/unit/chemistry/
tests/integration/chemistry/
tests/e2e/test_chemistry_expert.py
```

## 15. Acceptance Tests

| ID | Measurable acceptance criterion |
|----|---------------------------------|
| `CHEM-AT-001` | Schema/property tests reject missing units, invalid fractions, negative uncertainty, and unknown mutating fields |
| `CHEM-AT-002` | Identity fixtures preserve submitted structure and distinguish stereo, isotope, salt, solvate, tautomer, and mixture cases |
| `CHEM-AT-003` | Conflicting property fixtures retain both observations and select only condition-compatible evidence |
| `CHEM-AT-004` | Every factual claim/value in a 100-case corpus maps to exact source and method locators |
| `CHEM-AT-005` | Prompt-injection and malicious-document fixtures cannot change policy, permissions, approval, or active snapshots |
| `CHEM-AT-006` | Reaction fixtures pass atom/mass/charge balance; an unaccounted imbalance cannot return `succeeded` |
| `CHEM-AT-007` | Stoichiometry property tests round-trip units and propagate uncertainty within suite-pinned tolerance |
| `CHEM-AT-008` | An ambiguous identity, missing hazard record, or absent facility control blocks actionable protocol output |
| `CHEM-AT-009` | Changing one byte of an approved protocol invalidates its approval token |
| `CHEM-AT-010` | Inventory tests reject expired/restricted/wrong-purity lots and never silently substitute |
| `CHEM-AT-011` | Cheminformatics transforms are deterministic where declared and retain parent/source/tool/parameter lineage |
| `CHEM-AT-012` | Quantum reference cases verify parsed units, convergence, and suite-pinned energies/geometries |
| `CHEM-AT-013` | Molecular simulation fixtures verify topology, stability, restart, replicate, and sampling diagnostics |
| `CHEM-AT-014` | Thermo/kinetic/process fixtures pass unit, conservation, limiting-case, convergence, and sensitivity checks |
| `CHEM-AT-015` | Each spectroscopy parser round-trips its supported open fixture and explicitly rejects unsupported versions |
| `CHEM-AT-016` | Analytical fixtures detect out-of-range calibration, invalid controls, and failed precision/recovery thresholds |
| `CHEM-AT-017` | Raw instrument artifacts remain byte-identical after every processing workflow and have complete operation graphs |
| `CHEM-AT-018` | A timed-out engine kills all child processes, preserves bounded diagnostics, and publishes no validated value |
| `CHEM-AT-019` | Tool discovery decision records include fidelity, maintenance, license, security, validation, forum issues, and exit strategy |
| `CHEM-AT-020` | A failing research update does not change any active alias; a passing canary promotes without dropped requests |
| `CHEM-AT-021` | Forced canary regression initiates rollback within 60 seconds and preserves single-snapshot results |
| `CHEM-AT-022` | Tenant isolation prevents cross-tenant access to structures, formulas, protocols, inventory, spectra, and traces |
| `CHEM-AT-023` | Mutation and execution-facing export fail closed when policy/audit storage is unavailable |
| `CHEM-AT-024` | A 30-minute computation emits progress at least every 30 seconds and leaves no unbounded metric labels |
| `CHEM-AT-025` | Unit, integration, Molecule, E2E, security, chaos, and ZDD suites are green with >=85% aggregate and >=75% per Python file coverage |
| `CHEM-AT-026` | Source-record property tests reject absent dates, unresolved rights, missing claim links, invalid freshness, and mutable snapshots |
| `CHEM-AT-027` | A retraction or safety-source correction is detected within 24 hours and blocks every impacted high-risk protocol and alias |
| `CHEM-AT-028` | Identity reconciliation across InChI, PubChem, source structure, and a second authority preserves stereo/isotope/salt distinctions and exposes conflicts |
| `CHEM-AT-029` | Non-tetrahedral and unsupported stereochemistry fixtures are preserved or explicitly refused through RDKit/Open Babel round trips, never silently flattened |
| `CHEM-AT-030` | NIST/reference-data outage uses a pinned permitted snapshot or alternate authoritative source, marks degradation, and never drops conditions or uncertainty |
| `CHEM-AT-031` | AnIML, mzML/mzQC, JCAMP-DX, SDF, and CIF fixtures round-trip all adapter-declared identity, unit, method, array, and provenance fields |
| `CHEM-AT-032` | Lab-on-chip dry run validates simulation, compatibility, calibration, approval binding, command/observation separation, timeout, abort, safe state, and reconciliation with no actuation |
| `CHEM-AT-033` | Bubble, clog, leak, drift, saturation, missed-heartbeat, and interlock fixtures pause at pinned thresholds and quarantine affected results |
| `CHEM-AT-034` | Changing protocol, adapter, device, facility, safe domain, or expiry invalidates the arm token; replayed commands execute at most once |
| `CHEM-AT-035` | Active-learning fixtures stay inside the approved domain and material/time/waste budget and cannot approve, arm, transmit, widen constraints, or suppress controls |
| `CHEM-AT-036` | Cross-expert fixtures reject graph cycles, schema/unit/entity/fidelity mismatch, privilege escalation, expired leases, unbounded fan-out, and self-verification |
| `CHEM-AT-037` | Cancelling a campaign reaches every child, releases device/facility leases, executes compensation once, accounts for materials/waste, and records one terminal state per node |
| `CHEM-AT-038` | A full simulated campaign links protocol, approvals, lots, commands, observations, raw/derived data, deviations, uncertainty, and safe state in a validated RO-Crate/PROV record |

## 16. Research Integration Gate

Section 17 satisfies the initial serialized research baseline, not implementation
selection. Before status changes from `PROPOSED`, the implementation branch must
refresh every selected record, pin exact versions/commits and dependency
digests, resolve redistribution and model-training rights, reproduce the
relevant parser, calculation, simulation, or device dry run in the target
environment, and attach artifacts to the acceptance tests. Named tools and
formats remain candidates until those checks pass.

## 17. Cited Research Baseline

### 17.1 Record conventions

All records below were accessed on 2026-07-30. `Screened` means the linked
landing page/metadata showed no correction or retraction notice during this
research pass; it is not a permanent integrity guarantee. `N/A` is used for
living standards, databases, documentation, or code where retraction does not
apply. `Citation only` means publisher/site terms govern content and no training
or redistribution right is inferred. Reproduction was not attempted in this
documentation-only pass (`repro: no`) and is a hard implementation gate.

### 17.2 Identity, reference data, safety, and interchange

| Source ID | Source, steward, publication/update date | Supports | Rights, integrity, reproduction |
|-----------|------------------------------------------|----------|---------------------------------|
| `CHEM-SRC-PUBCHEM` | [PubChem documentation](https://pubchem.ncbi.nlm.nih.gov/docs/), NCBI, rolling database/docs | `CHEM-002`-`004`, identity/property evidence | US-government/site terms; N/A; repro: no |
| `CHEM-SRC-PUGREST` | [PubChem PUG REST](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest), NCBI, rolling API docs | `CHEM-002`-`004`, programmatic retrieval | API/site terms; N/A; repro: no |
| `CHEM-SRC-NISTWB` | [NIST Chemistry WebBook](https://webbook.nist.gov/), NIST, SRD 69 rolling data | `CHEM-004`, thermochemistry/spectra | NIST/site terms; N/A; repro: no |
| `CHEM-SRC-COMPTOX` | [CompTox APIs](https://www.epa.gov/comptox-tools/computational-toxicology-and-exposure-apis-about), US EPA, rolling docs | `CHEM-004`, `CHEM-008`, exposure/toxicity evidence | US-government/site terms; N/A; repro: no |
| `CHEM-SRC-INCHI` | [InChI Technical Manual](https://www.inchi-trust.org/wp/download/104/InChI_TechMan.pdf), InChI Trust, current manual | `CHEM-002`, identity serialization | Published terms; N/A; repro: no |
| `CHEM-SRC-UCUM` | [Unified Code for Units of Measure v2.2](https://ucum.org/ucum), UCUM, 2024-06-17 | `CHEM-004`, `CHEM-007`, typed quantities | Published license terms; N/A; repro: no |
| `CHEM-SRC-QUDT` | [QUDT](https://www.qudt.org/), QUDT.org, rolling vocabulary | `CHEM-004`, quantity kind/dimension | Published vocabulary terms; N/A; repro: no |
| `CHEM-SRC-ANIML` | [AnIML overview and schemas](https://new.animl.org/overview), ASTM AnIML community, v1.0 materials | `CHEM-014`, `CHEM-015`, analytical interchange | Standard/schema terms; N/A; repro: no |
| `CHEM-SRC-MZML` | [HUPO-PSI mzML repository](https://github.com/HUPO-PSI/mzML), HUPO-PSI, rolling schema | `CHEM-014`, mass-spectrometry interchange | Repository/spec terms; N/A; repro: no |
| `CHEM-SRC-MZQC` | [mzQC specification](https://hupo-psi.github.io/mzQC/), HUPO-PSI, rolling spec | `CHEM-014`, MS quality-control interchange | Specification terms; N/A; repro: no |
| `CHEM-SRC-JCAMP` | [JCAMP-DX IR specification](https://iupac.org/wp-content/uploads/2021/08/JCAMP-DX_IR_1988.pdf), IUPAC, 1988 | `CHEM-014`, spectroscopy interchange | IUPAC terms; screened; repro: no |
| `CHEM-SRC-CIF` | [CIF specifications](https://www.iucr.org/resources/cif/spec), IUCr, living standard | `CHEM-002`, `CHEM-014`, crystallographic interchange | IUCr terms; N/A; repro: no |
| `CHEM-SRC-ALLOTROPE` | [Allotrope Framework technical reports](https://docs.allotrope.org/), Allotrope Foundation, rolling specs | `CHEM-014`, `CHEM-015`, analytical data/provenance | Foundation/spec terms; N/A; repro: no |
| `CHEM-SRC-GHS` | [GHS Revision 11](https://unece.org/transport/documents/2025/09/standards/globally-harmonized-system-classification-and-labelling), UNECE, 2025 | `CHEM-008`, hazard communication | UN publication terms; N/A; repro: no |
| `CHEM-SRC-OSHA1450` | [29 CFR 1910.1450](https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.1450), US OSHA, current regulation | `CHEM-006`, `CHEM-008`, laboratory controls | US-government terms; N/A; repro: no |
| `CHEM-SRC-CAMEO` | [CAMEO Chemicals](https://cameochemicals.noaa.gov/), NOAA, rolling data | `CHEM-008`, reactivity/incompatibility screening | US-government/site terms; N/A; repro: no |

Jurisdiction, facility policy, current SDS, and qualified review remain
mandatory. The global and US sources above are evidence inputs, not a complete
legal or safety rule set for every location.

### 17.3 Cheminformatics, computation, and provenance

| Source ID | Source, steward, publication/update date | Supports | Rights, integrity, reproduction |
|-----------|------------------------------------------|----------|---------------------------------|
| `CHEM-SRC-RDKIT` | [RDKit Book](https://www.rdkit.org/docs/RDKit_Book.html), RDKit, rolling docs | `CHEM-002`, `CHEM-010`, structure transforms | BSD project; N/A; repro: no |
| `CHEM-SRC-OPENBABEL` | [Open Babel common formats](https://openbabel.org/docs/FileFormats/Common_cheminformatics_Formats.html), Open Babel, rolling docs | `CHEM-002`, `CHEM-010`, conversion | GPL project/site terms; N/A; repro: no |
| `CHEM-SRC-PSI4` | [Psi4 manual](https://psi4.github.io/psi4docs/master/), Psi4, rolling docs | `CHEM-011`, quantum workflows | Project/site terms; N/A; repro: no |
| `CHEM-SRC-ASE` | [Atomic Simulation Environment](https://ase.gitlab.io/ase/), ASE, rolling docs | `CHEM-011`, calculator/workflow adapters | LGPL project; N/A; repro: no |
| `CHEM-SRC-OPENMM` | [OpenMM user guide](https://docs.openmm.org/latest/userguide/introduction.html), OpenMM, rolling docs | `CHEM-012`, molecular simulation | Project/site terms; N/A; repro: no |
| `CHEM-SRC-CANTERA` | [Cantera science reference](https://cantera.org/stable/reference/), Cantera, rolling docs | `CHEM-013`, kinetics/thermodynamics | Project/site terms; N/A; repro: no |
| `CHEM-SRC-PROV` | [PROV-O](https://www.w3.org/TR/prov-o/), W3C, 2013 | `CHEM-019`, provenance graph | W3C document terms; N/A; repro: no |
| `CHEM-SRC-ROCRATE` | [RO-Crate 1.3](https://www.researchobject.org/ro-crate/specification/1.3/index.html), RO-Crate Community, 2026-06-22 | `CHEM-019`, portable campaign record | Apache-2.0 specification; N/A; repro: no |
| `CHEM-SRC-BO` | [A Tutorial on Bayesian Optimization](https://arxiv.org/abs/1807.02811), Frazier, 2018 | `CHEM-020`, `CHEM-023`, bounded design | Citation only; screened; repro: no |
| `CHEM-SRC-AL-MAT` | [Bayesian active learning for materials discovery](https://arxiv.org/abs/2006.06141), Kusne et al., 2020 | `CHEM-023`, campaign selection | Citation only; screened; repro: no |

### 17.4 Lab automation and lab-on-chip

| Source ID | Source, steward, publication/update date | Supports | Rights, integrity, reproduction |
|-----------|------------------------------------------|----------|---------------------------------|
| `CHEM-SRC-SILA` | [SiLA 2 base documentation](https://sila2.gitlab.io/sila_base/), SiLA Consortium, rolling docs | `CHEM-023`, device interoperability | Project/spec terms; N/A; repro: no |
| `CHEM-SRC-SILAPY` | [SiLA 2 Python implementation](https://sila2.gitlab.io/sila_python/), SiLA Consortium, rolling docs | `CHEM-023`, adapter candidate | Project terms; N/A; repro: no |
| `CHEM-SRC-OPENTRONS` | [Opentrons Python Protocol API](https://opentrons.com/pythonapi), Opentrons, rolling docs | `CHEM-023`, protocol simulation/translation | API/site terms; N/A; repro: no |
| `CHEM-SRC-PAML` | [PAML: Protocol Activity Markup Language](https://www.biorxiv.org/content/10.1101/2022.07.05.498808v1), Myers et al., 2022 | `CHEM-023`, protocol graphs | Publisher terms; screened; repro: no |
| `CHEM-SRC-DROPBOT` | [DropBot](https://microfluidics.utoronto.ca/dropbot/), Wheeler Microfluidics Laboratory, rolling docs | `CHEM-023`, digital-microfluidic feedback | Site/project terms; N/A; repro: no |
| `CHEM-SRC-FLUIGENT` | [Fluigent SDK manual](https://store.fluigent.com/wp-content/uploads/2021/06/Fluigent-SDK-User-Manual.pdf), Fluigent, 2021 | `CHEM-023`, pressure/flow adapter evidence | Vendor terms; N/A; repro: no |
| `CHEM-SRC-OPENFLEX` | [OpenFlexure Microscope documentation](https://openflexure.org/projects/microscope/documentation), OpenFlexure, rolling docs | `CHEM-023`, image observations | Project/site terms; N/A; repro: no |
| `CHEM-SRC-DMFML` | [Machine learning for digital microfluidics](https://pubs.rsc.org/en/content/articlelanding/2023/lc/d2lc00764a), RSC authors, 2023 | `CHEM-023`, adaptive microfluidic control evidence | Citation only; screened; repro: no |

### 17.5 Operator evidence and required regression fixtures

Operator reports are untrusted observations. They establish failure hypotheses
and regression tests, not product defects or general causal conclusions.

| Evidence ID | Report and observed date | Required implementation response |
|-------------|--------------------------|----------------------------------|
| `CHEM-OPS-RDKIT-STEREO` | [RDKit issue 4851](https://github.com/rdkit/rdkit/issues/4851), tracked since 2021, observed 2026-07-30 | Capability-probe non-tetrahedral stereo; preserve source and explicitly reject unsupported transforms |
| `CHEM-OPS-OPENBABEL-WIN` | [Open Babel Windows format report](https://www.reddit.com/r/comp_chem/comments/1f811fq/), reported 2024, observed 2026-07-30 | Probe formats per pinned platform and require semantic round-trip fixtures |
| `CHEM-OPS-NIST-CONDITIONS` | [NIST provenance/condition discussion](https://www.reddit.com/r/chemistry/comments/xf0m55/), reported 2022, observed 2026-07-30 | Preserve original-paper locator and conditions; never treat a database row as context-free truth |
| `CHEM-OPS-NIST-OUTAGE` | [NIST WebBook outage report](https://www.reddit.com/r/chemistry/comments/1iq1fdc/), reported 2025, observed 2026-07-30 | Exercise permitted snapshot/alternate-source degradation and recovery |
| `CHEM-OPS-COMPTOX-AUTH` | [CompTox API access report](https://www.reddit.com/r/toxicology/comments/1mjgyjg/), reported 2025, observed 2026-07-30 | Add connector readiness/auth test and a documented non-silent degraded path |
| `CHEM-OPS-OPENTRONS` | [Opentrons field discussion](https://www.reddit.com/r/labrats/comments/10jllne/), reported 2023, observed 2026-07-30 | Simulate alignment, calibration, low-volume, API/runtime mismatch, and ambiguous device errors |
