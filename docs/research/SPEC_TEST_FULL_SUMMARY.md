# Expert Collections Test Summary — Full Run

2026-07-30, all 4 new expert collections + expert security/observability/ZDD tests.

## Results by Collection

| Collection | Unit Files | Int/E2E Files | Total Files | Tests | Passed | Failed |
|-----------|-----------|---------------|-------------|-------|--------|--------|
| Materials | 15 | 3 | 18 | 456 | 456 | 0 |
| Chemistry | 13 | 3 | 16 | 380 | 380 | 0 |
| AI/ML | 12 | 3 | 15 | 319 | 319 | 0 |
| Git Release | 5 | 3 | 8 | 131 | 131 | 0 |
| Expert (security/ZDD/obs) | 3 | 0 | 3 | 106 | 106 | 0 |
| **TOTAL** | **48** | **12** | **60** | **1,392** | **1,392** | **0** |

## File-Level Detail

### Materials (18 files, 456 tests)
#### Unit (15 files)
- `tests/unit/test_materials_fixtures.py` — 53 tests (fixture shape, property invariants, source provenance, physical plausibility, record conversion)
- `tests/unit/test_materials_core.py` — core domain model tests
- `tests/unit/test_materials_contracts.py` — contract/schema tests
- `tests/unit/test_materials_source_registry.py` — source registry CRUD + freshness
- `tests/unit/test_material_selection.py` — screening, ranking, data hierarchy, strength calcs
- `tests/unit/test_materials_simulation.py` — solver adapter protocol, falsifiable plan, convergence, verification, resource bounds
- `tests/unit/test_materials_simulation_validation.py` — simulation validation tests
- `tests/unit/test_materials_metals.py` — metal-specific tests
- `tests/unit/test_materials_polymers.py` — polymer-specific tests
- `tests/unit/test_materials_joining.py` — joining process tests
- `tests/unit/test_materials_additive.py` — additive manufacturing tests
- `tests/unit/test_materials_failure.py` — failure analysis tests
- `tests/unit/test_materials_tolerance.py` — tolerance analysis tests
- `tests/unit/test_materials_process_planning.py` — process planning tests
- `tests/unit/test_materials_science.py` — general materials science tests

#### Integration (2 files)
- `tests/integration/materials/test_strength_workflow.py` — tension/bending/fatigue workflows with traceability
- `tests/integration/materials/test_selection_workflow.py` — end-to-end selection pipeline, cross-family ranking

#### E2E (1 file)
- `tests/e2e/test_materials_expert.py` — package imports, request construction, pipeline integration

### Chemistry (16 files, 380 tests)
#### Unit (13 files)
- `tests/unit/test_chemistry_fixtures.py` — 13 tests (corpus shape, formula parsing, SMILES consistency, MW, CAS, PubChem, hazards, incompatibilities, reactions)
- `tests/unit/test_chemistry_core.py` — router, identity, formula parsing, reactions, stoichiometry, safety
- `tests/unit/test_chemistry_safety.py` — risk tier ladder, hazard classes, compatibility, refusal, scale/concentration, property lookup
- `tests/unit/test_chemistry_api.py` — API request/result contracts
- `tests/unit/test_chemistry_schemas.py` — schema validation tests
- `tests/unit/test_chemistry_compute.py` — computational chemistry tests
- `tests/unit/test_chemistry_electrochem.py` — electrochemistry tests
- `tests/unit/test_chemistry_thermo.py` — thermodynamics tests
- `tests/unit/test_chemistry_promotion.py` — promotion tests
- `tests/unit/test_chemistry_cheminformatics.py` — structure validation, standardization, tautomers, substructure search, descriptors, similarity, provenance
- `tests/unit/test_chemistry_protocols.py` — protocol drafting, approval, inventory, lot suitability
- `tests/unit/test_chemistry_reactions.py` — balance, classification, amounts, concentration, yield, unit round-trip
- `tests/unit/test_chemistry_analytical.py` — calibration, LOD/LOQ, precision, accuracy, validation, outliers, blanks

#### Integration (2 files)
- `tests/integration/chemistry/test_identity_to_protocol.py` — identity → safety tier → protocol drafting loop
- `tests/integration/chemistry/test_reaction_balance.py` — reaction conservation, stoichiometry, classification

#### E2E (1 file)
- `tests/e2e/test_chemistry_expert.py` — package imports, request construction, routing

### AI/ML (15 files, 319 tests)
#### Unit (12 files)
- `tests/unit/test_ai_ml_core.py` — schema contracts, expert router, evidence store, answer, tool discovery
- `tests/unit/test_ai_ml_evidence.py` — immutability, content addressing, deduplication, citation, license validation, retraction, prompt injection isolation
- `tests/unit/test_ai_ml_reasoning.py` — state machine, step artifacts, numerical answer, independent checks, retrieval service, retrieval metrics
- `tests/unit/test_ai_ml_adaptation.py` — adapter manifest, base digest binding, training plan, safe stop, benchmark results, evaluation harness
- `tests/unit/test_ai_ml_registries.py` — record construction, publish/alias/tombstone/supersede
- `tests/unit/test_ai_ml_datasets.py` — manifest contract, validation, data card, format selection, research discovery
- `tests/unit/test_ai_ml_vision.py` — classification/detection/segmentation/OCR contracts, image edit records
- `tests/unit/test_ai_ml_speech.py` — ASR/TTS contracts, consent, word error rate, provenance
- `tests/unit/test_ai_ml_world_models.py` — environment contract, rollout evaluation, simulator adapter, simulation run
- `tests/unit/test_ai_ml_images.py` — image model tests
- `tests/unit/test_ai_ml_accelerators.py` — accelerator/hardware tests
- `tests/unit/test_ai_ml_distillation.py` — distillation tests

#### Integration (2 files)
- `tests/integration/ai_ml/test_research_to_answer.py` — question→research→answer pipeline, injection resistance, budget enforcement, numerical verification gate
- `tests/integration/ai_ml/test_evidence_lifecycle.py` — fetch/store/retrieve, deduplication, retraction, correction, tenant scoping

#### E2E (1 file)
- `tests/e2e/test_ai_ml_expert.py` — package imports, request construction, routing

### Git Release (8 files, 131 tests)
#### Unit (5 files)
- `tests/unit/test_git_release_evidence.py` — evidence collection (dirty tree, detached HEAD, SHA format)
- `tests/unit/test_git_release_contracts.py` — repo evidence, release plan, release verdict, assess repo
- `tests/unit/test_git_release_state.py` — state machine (discover→assess→stage→canary→promote→release), rollback, deployment strategies
- `tests/unit/test_git_release_helpers.py` — discover helpers, authority ranking, score evidence
- `tests/unit/test_git_release_provenance.py` — build provenance, verification, dependency digests, freshness, registry

#### Integration (2 files)
- `tests/integration/git_release/test_zdd_lifecycle.py` — full ZDD happy path, canary regression, rollback, telemetry blocks
- `tests/integration/git_release/test_assess_to_plan.py` — assess repo → discover → rank → build → release plan

#### E2E (1 file)
- `tests/e2e/test_git_release_expert.py` — package imports, assess repo on project root, evidence contracts

### Expert Cross-Cutting (3 files, 106 tests)
- `tests/unit/test_expert_security.py` — materials/chemistry/AI-ML/git-release security (prompt injection, path traversal, shell injection, safe serialization, builder identity)
- `tests/unit/test_expert_observability.py` — observability tests
- `tests/unit/test_expert_zdd.py` — ZDD cross-cutting tests

## Bugs Fixed

| File | Test | Bug | Fix |
|------|------|-----|-----|
| `tests/unit/test_materials_fixtures.py` | `test_source_ids_are_unique_per_publisher_revision` | Assertion that source_id must be globally unique; multiple materials share same handbook volume legitimately | Changed assertion to verify source-id → (publisher, revision) consistency |
| `tests/unit/test_chemistry_fixtures.py` | `test_formulas_match_smiles` | Sucrose SMILES `OC[C@H]1OC...` had C:11 O:10 but formula C12H22O11 has C:12 O:11 — one C and one O missing | Replaced SMILES in `tests/fixtures/chemistry_data.py:72` with `C(C1C(C(C(C(O1)OC2(C(C(C(O2)CO)O)O)CO)O)O)O)O` and in `src/general_ludd/chemistry/core.py:243` |
| `tests/unit/test_ai_ml_world_models.py` | 2x `ModuleNotFoundError: general_ludd.security.safe_diskcache` | Stale `.pyc` bytecode in `src/general_ludd/models/response_cache.py` referenced deleted module | Resolved by subsequent test run (bytecode refresh). The `response_cache.py` no longer imports `safe_diskcache`. |
| `tests/unit/test_expert_security.py` | `test_helper_catalog_has_no_subprocess_or_shell_invocation` | Grep of raw source matched `"subprocess"` in docstring "Discovery is filesystem-only (no subprocess, no shell)" | Replaced naive string match with AST parse that extracts only import names and call-site function names |
