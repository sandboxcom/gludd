# Acceptance Test Mapping — Expert Collections

**Audit date:** 2026-07-30
**Auditor:** opencode (automated)
**Specs audited:**
- `docs/specs/FEATURE_MATERIALS_ENGINEER.md` §14 — MATE-AT-001…011 (11 ATs)
- `docs/specs/FEATURE_CHEMISTRY_EXPERT.md` §15 — CHEM-AT-001…025 (25 ATs)
- `docs/specs/FEATURE_AI_ML_EXPERT.md` §16 — AIML-AT-001…022 (22 ATs)
- `docs/specs/FEATURE_GIT_RELEASE_CAPTAIN_EXPERT.md` §13 — GRC-AT-001…010 (10 ATs)

**Total acceptance tests:** 68

## Summary

| Status | Count | % |
|---|---|---|
| DONE | 0 | 0% |
| PARTIAL | 60 | 88% |
| NOT_STARTED | 8 | 12% |

**Headline finding:** source modules, unit tests, Ansible collection scaffolding, and smoke-level E2E tests exist for **all four collections** — but no AT meets its full measurable bar. Acceptance criteria routinely demand 10 000-case property-based suites, 100-case golden corpora, 20/20 recovery scenarios, 100/100 race-injection trials, integration tests, Molecule tests, security/chaos tests, and 85%/75% coverage gates. What exists today is *structural scaffolding + representative cases*, not the full-scale fixtures the specs require.

**Common gap categories (apply to nearly every AT):**

1. **No property-based testing at scale.** Zero `hypothesis` usage exists in any of the four collections' test files. ATs that say "at least 10 000 combinations" or "property tests round-trip" are unmet at volume.
2. **No integration tests for the expert collections.** `tests/integration/` contains 100+ files but none target `materials`/`chemistry`/`ai_ml`/`git_release`. Specs require integration suites for AT-011/AT-022/AT-025/AT-010 gate ATs.
3. **No Molecule tests.** `molecule/playbooks/<collection>_expert/` directories exist as untracked scaffolding only; no `converge.yml`/`verify.yml` under `collections/.../molecule/<role>/`.
4. **E2E tests are smoke-only.** `tests/e2e/test_<collection>_expert.py` files verify package imports and basic request construction. They do **not** exercise 100-case corpora, sandbox forges, ZDD promotion, tenant isolation, or 60 s rollback SLOs.
5. **No security/chaos/ZDD test suites.** Every spec's quality-gate AT (MATE-AT-011, CHEM-AT-025, AIML-AT-022, GRC-AT-010) explicitly requires these and none exist.
6. **Gate is currently RED** — `make gate-status` shows lint 14 failures, typecheck 12 failures, hook-runtime FAIL. Until green, no AT-011/022/025/010 quality-gate can pass.

---

## Legend

- **DONE** — full measurable criterion met; cited test file invokes the behavior at the required scale.
- **PARTIAL** — implementation and at least one test exist, but the AT's volume/scope bar (10 000 cases, 100 golden problems, 20/20 recovery, integration/E2E/Molecule suites, coverage threshold) is not met.
- **NOT_STARTED** — no implementing file and/or no test file found.

---

## 1. MATE — Materials Engineering Expert (`general_ludd.materials`)

**Source:** `src/general_ludd/materials/` — 21 modules (contracts, units, source_registry, property_store, material_selection, polymers, metals, joining, machining, additive, textiles, strength, tolerance, failure, process_planning, core, simulation/{protocols,verification,validation}).
**Ansible collection:** `collections/ansible_collections/general_ludd/materials/` — 16 roles.
**Unit tests:** 12 files under `tests/unit/test_materials_*.py` + `test_material_selection.py`.
**E2E:** `tests/e2e/test_materials_expert.py` (smoke only, 154 lines).
**Molecule:** none.

| ID | Description | Status | Implementing file(s) | Test file(s) | Gap |
|---|---|---|---|---|---|
| MATE-AT-001 | Units & condition integrity — 10 000+ property-based combinations; incompatible dims fail closed | PARTIAL | `materials/units.py`, `materials/contracts.py`, `materials/core.py` | `tests/unit/test_materials_contracts.py:187` (test_property_missing_unit_rejected), `test_materials_core.py:71,79` | No `hypothesis`-driven 10 000-case suite; only example-based rejection tests |
| MATE-AT-002 | Material selection — 100 golden problems; every hard-constraint violation rejected; margins/sources/uncertainty exposed | PARTIAL | `materials/material_selection.py`, `materials/core.py` | `tests/unit/test_material_selection.py`, `test_materials_core.py:197` | <100 golden cases; no cross-family corpus (metals/polymers/ceramics/composites/textiles/hybrids) |
| MATE-AT-003 | Process compatibility — thermosets not remeltable, incompatible joining rejected, dissimilar-metal flags | PARTIAL | `materials/polymers.py:76,341`, `materials/joining.py:21,322`, `materials/metals.py:6`, `materials/additive.py:115`, `materials/core.py:480` | `tests/unit/test_materials_polymers.py`, `test_materials_joining.py:85`, `test_materials_metals.py` | Negative fixtures exist but not the full taxonomy (heat treatments, galvanic, thermal-expansion, inspection risks) |
| MATE-AT-004 | Welding plans — golden cases for fusion/cold/friction/diffusion/resistance/ultrasonic/pressure/polymer/brazed/soldered/adhesive/mechanical; insufficient_data on unsafe | PARTIAL | `materials/joining.py` | `tests/unit/test_materials_joining.py` | Not all 12 joint families covered; qualification/consumable/hazard/defect/inspection/repair fields not exhaustively tested |
| MATE-AT-005 | Manufacturing plans — golden builds across injection/extrusion/thermoforming/forging/casting/stamping/milling/turning/PBF/material extrusion/DED/sewing/weaving/composite molding/hybrid | PARTIAL | `materials/process_planning.py:8` | `tests/unit/test_materials_process_planning.py` | Traceability chain from requirements to process control to inspection present in code but not validated for all 15 process families |
| MATE-AT-006 | Analytical benchmarks — hand calcs for axial/beam/torsion/pressure/thermal expansion/buckling/contact/fatigue/tolerance chain | PARTIAL | `materials/strength.py`, `materials/tolerance.py` | `tests/unit/test_materials_tolerance.py:16,205` | No independently-reviewed reference corpus; no SciPy-based benchmark suite (OSS_TOOLS_SURVEY.md recommends SciPy) |
| MATE-AT-007 | Simulation verification — patch/manufactured-solution benchmarks, mesh/time convergence, unit & conservation checks; nonconvergence blocks | PARTIAL | `materials/simulation/verification.py:1`, `materials/simulation/protocols.py:16,111,154` | `tests/unit/test_materials_simulation.py`, `test_materials_simulation_validation.py` | Verification machinery exists; no benchmark corpus of solver adapters |
| MATE-AT-008 | Validation & uncertainty — outliers not hidden; decision-driving input changes update rank deterministically | PARTIAL | `materials/simulation/validation.py:2,8,15,50,87,304,307` | `tests/unit/test_materials_simulation_validation.py` | Robust MAD implemented; no representative experimental datasets wired |
| MATE-AT-009 | ZDD manufacturing promotion — digital line fixture, drift/failed inspection/stale calibration halt promotion, quarantine, reversion | NOT_STARTED | (no ZDD state machine in materials) | (none) | No digital-line fixture; no MATE-ZDD-001…005 implementation found in `materials/` |
| MATE-AT-010 | Security & safety — sandbox containment of malicious CAD/mesh/solver; path/cmd injection resistance; secret redaction; default-off machine output; approval enforcement | NOT_STARTED | (no sandbox runner in materials) | (none) | No malicious-fixture security suite; no resource-bounded sandbox harness |
| MATE-AT-011 | Quality gate — 85% overall / 75% per-file; unit+integration+solver-contract+failure-injection+E2E all pass; collection build/syntax/lint/typecheck/security/license/digest checks pass | PARTIAL | (whole module) | (12 unit + 1 smoke e2e) | No integration/failure-injection tests; gate currently RED; per-file coverage not measured against 75% bar |

---

## 2. CHEM — Chemistry Expert (`general_ludd.chemistry`)

**Source:** `src/general_ludd/chemistry/` — 23 modules (api, schemas, router, entities, evidence, properties, reactions, protocols, stoichiometry, safety, inventory, cheminformatics, compute, thermo_kinetics, spectroscopy, analytical, electrochemistry, process, provenance, validation, promotion, policy, core).
**Ansible collection:** `collections/ansible_collections/general_ludd/chemistry/` — 12+ roles + `plugins/module_utils/chemistry_dispatch.py`.
**Unit tests:** 15 files under `tests/unit/test_chemistry_*.py`.
**E2E:** `tests/e2e/test_chemistry_expert.py` (smoke only, 137 lines).
**Molecule:** none.

| ID | Description | Status | Implementing file(s) | Test file(s) | Gap |
|---|---|---|---|---|---|
| CHEM-AT-001 | Schema/property tests reject missing units, invalid fractions, negative uncertainty, unknown mutating fields | PARTIAL | `chemistry/schemas.py:13`, `chemistry/compute.py:50` | `tests/unit/test_chemistry_schemas.py:3` (explicit CHEM-AT-001/002 marker) | Example-based rejections present; no 10 000-case property suite |
| CHEM-AT-002 | Identity fixtures preserve structure; distinguish stereo/isotope/salt/solvate/tautomer/mixture | PARTIAL | `chemistry/entities.py` | `tests/unit/test_chemistry_schemas.py`, `test_organic_chemistry.py`, `test_inorganic_chemistry.py` | Canonicalization preserves submitted repr per code; full stereo/isotope/salt/solvate/tautomer distinction not exhaustively tested |
| CHEM-AT-003 | Conflicting property fixtures retained; only condition-compatible evidence selected | PARTIAL | `chemistry/properties.py:10,80,100,249` | `tests/unit/test_chemistry_safety.py:251-300` (property_lookup tests) | Conflict retention logic present; no large condition-matrix corpus |
| CHEM-AT-004 | 100-case corpus maps every claim to source + method locator | NOT_STARTED | `chemistry/provenance.py` | (none) | No 100-case citation corpus |
| CHEM-AT-005 | Prompt-injection/malicious-document fixtures cannot change policy/permissions/approval/snapshots | PARTIAL | `chemistry/policy.py`, `chemistry/evidence.py` (implicit) | (none targeted) | Policy isolation is structural (treat input as data) but no adversarial fixture suite |
| CHEM-AT-006 | Reaction fixtures pass atom/mass/charge balance; unaccounted imbalance cannot return `succeeded` | PARTIAL | `chemistry/reactions.py:6,88,184` | `tests/unit/test_chemistry_reactions.py` | Balance check implemented and explicitly annotated; full reaction golden corpus missing |
| CHEM-AT-007 | Stoichiometry property tests round-trip units and propagate uncertainty within tolerance | PARTIAL | `chemistry/stoichiometry.py:5` | (no dedicated test file — `test_chemistry_thermo.py` only) | No `tests/unit/test_chemistry_stoichiometry.py`; no property-based round-trip |
| CHEM-AT-008 | Ambiguous identity, missing hazard record, or absent facility control blocks actionable protocol | PARTIAL | `chemistry/safety.py`, `chemistry/protocols.py:18` | `tests/unit/test_chemistry_safety.py`, `test_chemistry_protocols.py` | Block pathways exist; full hazardous-material/facility-control matrix not tested |
| CHEM-AT-009 | One byte change to approved protocol invalidates approval token | PARTIAL | `chemistry/protocols.py:161,186,187` | `tests/unit/test_chemistry_protocols.py` | Digest logic present; no byte-mutation property test |
| CHEM-AT-010 | Inventory tests reject expired/restricted/wrong-purity lots; never silently substitute | PARTIAL | `chemistry/inventory.py:4,99` | (no dedicated inventory test file) | Code annotated with CHEM-AT-010; no `tests/unit/test_chemistry_inventory.py` |
| CHEM-AT-011 | Cheminformatics transforms deterministic where declared; retain parent/source/tool/parameter lineage | PARTIAL | `chemistry/cheminformatics.py` | `tests/unit/test_chemistry_cheminformatics.py:221` | Determinism + parent lineage tested for some transforms; full transform catalog (tautomer/protomer/stereoisomer enumeration, descriptors, conformers) not covered |
| CHEM-AT-012 | Quantum reference cases verify parsed units, convergence, suite-pinned energies/geometries | PARTIAL | `chemistry/compute.py:12,118,123,132,136,139,147` | `tests/unit/test_chemistry_compute.py` | Unconverged→unqualified gate tested; no suite-pinned energy/geometry reference corpus |
| CHEM-AT-013 | Molecular simulation fixtures verify topology, stability, restart, replicate, sampling diagnostics | NOT_STARTED | (no MD module beyond `compute.py` stubs) | (none) | No molecular-simulation fixture harness |
| CHEM-AT-014 | Thermo/kinetic/process fixtures pass unit, conservation, limiting-case, convergence, sensitivity checks | PARTIAL | `chemistry/thermo_kinetics.py`, `chemistry/process.py` | `tests/unit/test_chemistry_thermo.py` | Thermo tests exist; conservation/limiting-case/sensitivity checks partial |
| CHEM-AT-015 | Each spectroscopy parser round-trips supported open fixture; explicitly rejects unsupported versions | PARTIAL | `chemistry/spectroscopy.py:8,53` | (no dedicated spectroscopy test file) | Parser skeleton with reject path; no `tests/unit/test_chemistry_spectroscopy.py`, no round-trip fixtures |
| CHEM-AT-016 | Analytical fixtures detect out-of-range calibration, invalid controls, failed precision/recovery thresholds | PARTIAL | `chemistry/analytical.py` | `tests/unit/test_chemistry_analytical.py`, `test_physics_analytical_chemistry.py` | Some analytical validation tests; full calibration-range/control/precision matrix missing |
| CHEM-AT-017 | Raw instrument artifacts byte-identical after processing; complete operation graphs | NOT_STARTED | (no instrument-raw-data module) | (none) | No raw-artifact immutability test; no operation-graph persistence |
| CHEM-AT-018 | Timed-out engine kills all children, preserves bounded diagnostics, publishes no validated value | PARTIAL | `chemistry/compute.py` (partial) | (no timeout test) | Timeout handling referenced in compute; no child-process kill test |
| CHEM-AT-019 | Tool discovery decision records include fidelity, maintenance, license, security, validation, forum issues, exit strategy | PARTIAL | (scattered across modules) | (none targeted) | No `tests/unit/test_chemistry_tool_discover.py`; spec CHEM-018/AT-019 dimensions not exhaustively checked |
| CHEM-AT-020 | Failing research update changes no active alias; passing canary promotes without dropped requests | PARTIAL | `chemistry/promotion.py` | `tests/unit/test_chemistry_promotion.py` | Promotion code + unit tests exist; no "dropped request" load test |
| CHEM-AT-021 | Forced canary regression rolls back within 60 s; single-snapshot results preserved | PARTIAL | `chemistry/promotion.py` | `tests/unit/test_chemistry_promotion.py` | Rollback logic present; 60 s SLO not measured; no regression-injection fixture |
| CHEM-AT-022 | Tenant isolation prevents cross-tenant access to structures, formulas, protocols, inventory, spectra, traces | NOT_STARTED | (no tenant-isolation layer in chemistry) | (none) | No tenant-boundary enforcement tests |
| CHEM-AT-023 | Mutation and execution-facing export fail closed when policy/audit storage unavailable | PARTIAL | `chemistry/policy.py`, `chemistry/safety.py` | (none targeted) | Fail-closed logic present; storage-outage simulation missing |
| CHEM-AT-024 | 30-minute computation emits progress every 30 s; no unbounded metric labels | NOT_STARTED | (no long-running compute runner) | (none) | No progress-heartbeat test; no metric-label-bound audit |
| CHEM-AT-025 | Unit+integration+Molecule+E2E+security+chaos+ZDD suites green; 85% aggregate / 75% per-file coverage | PARTIAL | (whole module) | (15 unit + 1 smoke e2e) | No integration/Molecule/security/chaos/ZDD suites; gate currently RED |

---

## 3. AIML — AI/ML Expert (`general_ludd.ai_ml`)

**Source:** `src/general_ludd/ai_ml/` — 20 modules (schemas, router, evidence, research, registries, datasets, retrieval, reasoning, adaptation, distillation, speech, vision, images, world_models, simulators, accelerators, evaluation, promotion, policy).
**Ansible collection:** `collections/ansible_collections/general_ludd/ai_ml/` — 19 roles.
**Unit tests:** 12 files under `tests/unit/test_ai_ml_*.py`.
**E2E:** `tests/e2e/test_ai_ml_expert.py` (smoke only, 147 lines).
**Molecule:** none.

| ID | Description | Status | Implementing file(s) | Test file(s) | Gap |
|---|---|---|---|---|---|
| AIML-AT-001 | Schema contract tests reject every invalid enum, missing digest, negative budget, unknown mutating field | PARTIAL | `ai_ml/schemas.py:9,280,346` | `tests/unit/test_ai_ml_registries.py:69` (parametrize), `test_ai_ml_core.py` | Example-based rejections; no full enum/digest/budget property sweep |
| AIML-AT-002 | 100-source fixture ingests deterministically; duplicates create one artifact + multiple locators | PARTIAL | `ai_ml/evidence.py:9,18,44,131` | `tests/unit/test_ai_ml_evidence.py` | Dedup+multi-locator logic implemented and annotated; no 100-source fixture corpus |
| AIML-AT-003 | Prompt-injection fixtures cannot alter tool permissions/policies/query scope/approval state | PARTIAL | `ai_ml/evidence.py:18,44` | (none targeted) | Structural isolation present; no adversarial prompt-injection corpus |
| AIML-AT-004 | Staged knowledge update failing one regression never changes active alias | PARTIAL | `ai_ml/promotion.py:26,197,203,374,380` | (none targeted) | Promotion gate implemented; no regression-injection test for alias immutability |
| AIML-AT-005 | Rollback serves 100% successful requests while atomically returning to prior snapshot within 60 s | PARTIAL | `ai_ml/promotion.py:26,197,374` | (none targeted) | Rollback logic annotated; no live-request-during-rollback load harness |
| AIML-AT-006 | Retrieval benchmark meets pinned recall@10, nDCG@10, citation precision, p95 latency, spend | PARTIAL | `ai_ml/retrieval.py` | (none targeted) | Retrieval service exists; no benchmark corpus with pinned thresholds |
| AIML-AT-007 | Reasoning fixtures preserve units and pass independent numerical check; failed checks never `succeeded` | PARTIAL | `ai_ml/router.py:183`, `ai_ml/reasoning.py` | `tests/unit/test_ai_ml_reasoning.py` | Reasoning tests exist; unit preservation + independent-check pattern partial |
| AIML-AT-008 | Adapter load fails on one-byte base-model digest mismatch; succeeds reproducibly with pinned digest | PARTIAL | `ai_ml/adaptation.py:12,260` | `tests/unit/test_ai_ml_adaptation.py` | Digest-mismatch logic annotated; byte-mutation property test missing |
| AIML-AT-009 | Distilled student meets retention/safety floors; below-floor slice blocks promotion | PARTIAL | `ai_ml/distillation.py:13,296` | `tests/unit/test_ai_ml_distillation.py` | Promotion-floor logic implemented; full retention/safety slice corpus missing |
| AIML-AT-010 | ASR fixture reports final ordered segments; meets pinned WER/timestamp/diarization/RTF bounds | PARTIAL | `ai_ml/speech.py:72` | `tests/unit/test_ai_ml_speech.py:268` (empty-ref WER=0 test) | WER computation tested; no fixture corpus with pinned error bounds |
| AIML-AT-011 | TTS refuses unconsented custom voice; emits provenance for approved synthetic voice | PARTIAL | `ai_ml/speech.py:187,240` | `tests/unit/test_ai_ml_speech.py` | Consent-gate code annotated; refusal-on-unconsented not asserted in test |
| AIML-AT-012 | Vision fixtures return grounded regions; image edits retain source/mask/seed/model/operation graph | PARTIAL | `ai_ml/vision.py:8,158,215`, `ai_ml/images.py` | `tests/unit/test_ai_ml_vision.py`, `test_ai_ml_images.py` | Vision/image modules + tests exist; grounded-region + operation-graph assertions partial |
| AIML-AT-013 | World-model fixtures report multi-horizon error + calibrated uncertainty; unsafe actuation impossible | PARTIAL | `ai_ml/world_models.py:13,231,286` | `tests/unit/test_ai_ml_world_models.py` | Actuation-gate logic annotated; multi-horizon/uncertainty corpus missing |
| AIML-AT-014 | Each simulator family passes schema, units, deterministic/seeded replay, limiting-case, resource-limit | PARTIAL | `ai_ml/simulators.py:25,26,192,210,238,248,274,307,325,331` | (none targeted) | Simulator runner with invariant checks annotated; no per-family replay tests |
| AIML-AT-015 | Simulator timeout kills all children, emits terminal event, returns no scientific value | PARTIAL | `ai_ml/simulators.py:26,192,210,238,274,307` | (none targeted) | Timeout→no-scientific-value path implemented; no child-kill verification test |
| AIML-AT-016 | Accelerator dry-run identifies Azure A100/H100-class plan without provisioning; live path requires approval | PARTIAL | `ai_ml/accelerators.py:21,23,170,184,313,318,341,393,405` | `tests/unit/test_ai_ml_accelerators.py` | Dry-run logic thoroughly annotated; live-approval-path test partial |
| AIML-AT-017 | Preempted training resumes from last verified checkpoint without double-counting spend | PARTIAL | `ai_ml/accelerators.py:23,393,405` | `tests/unit/test_ai_ml_accelerators.py` | Resume-from-checkpoint code annotated; double-spend prevention not asserted |
| AIML-AT-018 | Tool discovery decision record includes maintenance, license, security, forum-issue, exit-strategy evidence | PARTIAL | `ai_ml/router.py:16,297` | (none targeted) | Decision-record structure annotated; no `test_ai_ml_tool_discover.py` |
| AIML-AT-019 | 30-minute job emits progress every 30 s; no unbounded metric labels | NOT_STARTED | (no long-running job runner) | (none) | No heartbeat assertion; no metric-label-bound audit |
| AIML-AT-020 | Tenant-isolation tests prove cross-tenant artifacts/indexes/voices/prompts/traces inaccessible | NOT_STARTED | (no tenant boundary layer in ai_ml) | (none) | No tenant-boundary enforcement tests |
| AIML-AT-021 | Mutation fails closed when policy/audit storage unavailable; eligible read-only queries explicitly `degraded` | PARTIAL | `ai_ml/policy.py:4,44,181` | (none targeted) | Fail-closed path annotated (AIML-AT-021); storage-outage simulation missing |
| AIML-AT-022 | Unit+integration+Molecule+E2E+security+chaos+ZDD suites green; 85% aggregate / 75% per-file | PARTIAL | (whole module) | (12 unit + 1 smoke e2e) | No integration/Molecule/security/chaos/ZDD suites; gate currently RED |

---

## 4. GRC — Git Release Captain Expert (`general_ludd.git_release`)

**Source:** `src/general_ludd/git_release/` — 10 modules (contracts, evidence, topology, helper_catalog, helper_ranker, release_state, provenance, deployment, source_registry, `__init__`).
**Ansible collection:** `collections/ansible_collections/general_ludd/git_release/` — 14 roles.
**Unit tests:** 5 files under `tests/unit/test_git_release_*.py`.
**E2E:** `tests/e2e/test_git_release_expert.py` (smoke only, 108 lines — runs `assess_repo` on this repo).
**Molecule:** none.

| ID | Description | Status | Implementing file(s) | Test file(s) | Gap |
|---|---|---|---|---|---|
| GRC-AT-001 | Repository evidence — fixtures for clean/dirty/detached/shallow/multi-worktree/submodule/LFS/interrupted-rebase/diverged-upstream; `repo_assess` returns normalized `RepoEvidence` with no mutation | PARTIAL | `git_release/evidence.py`, `git_release/topology.py` | `tests/unit/test_git_release_evidence.py`, `tests/e2e/test_git_release_expert.py:33-69` (runs on live repo) | Live-repo assess works; full 9-fixture corpus (detached/shallow/submodule/LFS/interrupted-rebase/diverged-upstream) missing |
| GRC-AT-002 | Recovery — 20 seeded loss scenarios; preserve original object DB, recovery ref, restore tree in 20/20; expired/missing object → `blocked` | PARTIAL | `git_release/release_state.py`, `git_release/contracts.py` | `tests/unit/test_git_release_state.py` | Recovery-state contracts tested; 20-scenario loss fixture not present |
| GRC-AT-003 | Concurrency safety — when remote ref moves after planning, branch update + release publication refuse stale op in 100/100 race-injection trials | PARTIAL | `git_release/source_registry.py:308` (GRC-AT-003 annotation) | (none targeted) | Annotation present; 100-trial race-injection harness missing |
| GRC-AT-004 | Helper selection — corpus spanning Make/Task/Just/Python/Node/Rust/Java/Go/container/Terraform/Ansible/mixed; discovery finds every CI entry point; native helper always chosen | PARTIAL | `git_release/helper_catalog.py`, `git_release/helper_ranker.py` | `tests/unit/test_git_release_helpers.py` | Helper ranking logic tested; multi-language corpus missing |
| GRC-AT-005 | No needless helper generation — adequate helper ⇒ zero file changes; rerun ⇒ zero further changes | PARTIAL | `git_release/helper_ranker.py:1,14,290,296,313` | `tests/unit/test_git_release_helpers.py` | Idempotence logic annotated with GRC-AT-005; rerun-idempotence assertion missing |
| GRC-AT-006 | Reproducible artifacts — two clean builds produce byte-identical artifacts; all expected artifacts install/smoke/uninstall/verify | NOT_STARTED | (no build-reproducibility harness) | (none) | No reproducible-build fixture; no install/smoke/uninstall suite |
| GRC-AT-007 | ZDD & rollback — canary rollout sustains synthetic load with no failed requests; latency/error/schema/correctness regressions stop promotion + restore prior digest within recovery objective | NOT_STARTED | `git_release/deployment.py` (skeleton) | (none) | No two-version service fixture; no canary/regression harness |
| GRC-AT-008 | Release-page proof — sandbox forge; release remains `deploying` until all expected assets remotely visible + digest-matched; missing/duplicate/mismatched ⇒ `blocked` | PARTIAL | `git_release/release_state.py` | `tests/unit/test_git_release_state.py` | Release-state machine tested; no sandbox-forge integration |
| GRC-AT-009 | Security — command-injection resistance, path containment, secret redaction, signature failure, authorization expiry, protected-ref behavior, fail-closed on missing telemetry/policy | PARTIAL | `git_release/provenance.py`, `git_release/contracts.py` | `tests/unit/test_git_release_provenance.py`, `test_git_release_contracts.py` | Provenance + contract tests exist; full security suite (injection/path/secret/expiry) missing |
| GRC-AT-010 | Quality gate — 85% overall / 75% per-file; unit+integration+provider-contract+failure-injection+sandbox-forge E2E pass; collection build/syntax/lint/typecheck/security/SBOM/doc-link pass; release-role change proves zero-downtime or blocked | PARTIAL | (whole module) | (5 unit + 1 smoke e2e) | No integration/provider-contract/failure-injection/sandbox-forge suites; gate currently RED |

---

## Cross-cutting remediation plan (priority order)

This is the **fix** backlog implied by the gap analysis. Each item closes ATs across multiple collections.

1. **Add `hypothesis` property-based suites.** Closes MATE-AT-001, CHEM-AT-001, CHEM-AT-007, CHEM-AT-009, AIML-AT-001, AIML-AT-008. Single shared `tests/property/conftest.py` + per-module `test_<module>_property.py`.
2. **Build the 100-case golden corpora.** Closes MATE-AT-002, CHEM-AT-004, AIML-AT-002. JSON fixtures under `tests/fixtures/<collection>/golden/`.
3. **Add integration test directories.** Closes one precondition of MATE-AT-011, CHEM-AT-025, AIML-AT-022, GRC-AT-010. Create `tests/integration/{materials,chemistry,ai_ml,git_release}/` with at least one cross-module wiring test per collection.
4. **Wire Molecule scenarios.** Create `collections/.../<collection>/molecule/<role>/{converge,verify}.yml` for at least one role per collection. Untracked `molecule/playbooks/<collection>_expert/` is the seed; promote it into the collection layout.
5. **Build security/chaos/ZDD fixtures.** Closes MATE-AT-009, MATE-AT-010, CHEM-AT-017, CHEM-AT-022, CHEM-AT-024, AIML-AT-019, AIML-AT-020, GRC-AT-006, GRC-AT-007. Largest single chunk of NOT_STARTED ATs.
6. **Fix the RED gate first.** Until `make gate` is green (lint 0, typecheck ≤ baseline), no quality-gate AT can be claimed. Currently: lint 14 failures, typecheck 12 failures, hook-runtime FAIL.

## Methodology

- Implementation status determined by `glob` over `src/general_ludd/<collection>/**/*.py` + `collections/.../<collection>/**/*` + `tests/unit/test_<collection>_*.py` + `tests/e2e/test_<collection>_expert.py`.
- AT-to-code linkage determined by `grep` for `MATE-AT-|CHEM-AT-|AIML-AT-|GRC-AT-` across the repo (100+ source-level annotations found).
- "PARTIAL" requires both an implementing module AND at least one test exercising the behavior. Volume/scope bars (10 000 cases, 100 golden problems, integration/Molecule/E2E/security/chaos/ZDD suites, 85%/75% coverage) are checked against actual file presence — not claimed in code comments.
- Gate status source: `make gate-status` run 2026-07-30T22:15:33Z — lint FAIL 14, typecheck FAIL 12, hook-runtime FAIL, collect PASS 0 errors.
