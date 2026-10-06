# Expert-system interoperability domain extensions and evidence

This shard preserves the advanced rights, privacy, regulated-transfer, benchmark, language, embodied-time, laboratory, incident-response, source, and practitioner contracts routed from [`FEATURE_EXPERT_SYSTEM_INTEROPERABILITY.md`](../FEATURE_EXPERT_SYSTEM_INTEROPERABILITY.md).

## 24. Rights, privacy, regulated transfer, drift, language, and embodied time

The evidence, memory, evaluation, and domain contracts above carry metadata but
do not by themselves authorize one concrete use, prove privacy removal, keep
benchmark scores comparable, establish accessible semantic equivalence, or make
physical time/state safe. This section closes those boundaries. Its decisions
are versioned policy artifacts, not model opinions.

### 24.1 Rights and purpose-specific use

```yaml
schema: gludd.expert_use_decision.v1
decision_id: uuid
purpose: string
jurisdiction_context: [string]
subjects:
  - asset_id: string
    revision_digest: string
    role: input|dataset|model|adapter|prompt|software|output|derivative
    declared_license: string|null
    concluded_license: string|null
    rights_source_ids: [string]
rights:
  train: allow|deny|unknown
  evaluate: allow|deny|unknown
  infer: allow|deny|unknown
  modify: allow|deny|unknown
  create_derivatives: allow|deny|unknown
  redistribute: allow|deny|unknown
  publish_output: allow|deny|unknown
  commercial_use: allow|deny|unknown
obligations: [object]
forbidden_uses: [string]
valid_from: RFC3339
valid_until: RFC3339|null
decision: allow|deny|hold
qualified_reviewer: object|null
policy_revision: string
signature: object
```

| ID | Requirement |
|---|---|
| ESI-RIGHTS-001 | Every train, evaluate, adapt, distill, infer, transform, publish, redistribute, or commercial-use operation MUST reference an `ExpertUseDecision` covering the exact asset revisions, purpose, actors, output, destination, and jurisdiction context. |
| ESI-RIGHTS-002 | Declared and concluded licenses MUST remain separate, each with source and revision; a model, dataset, card, filename, repository topic, public URL, download, or “open” label MUST NOT create a concluded license. |
| ESI-RIGHTS-003 | The decision MUST evaluate input access, training/evaluation/inference, modification, derivative, output, redistribution, attribution/notice, patent/trademark, commercial, field-of-use, privacy/publicity, consent, and retention dimensions independently where applicable. |
| ESI-RIGHTS-004 | Rights and obligations MUST propagate through extraction, filtering, joining, translation, embedding, fine-tuning, adapters, distillation, checkpoint merge, generation, packaging, and publication; a transformation cannot silently discard field-, record-, or asset-level restrictions. |
| ESI-RIGHTS-005 | Compatibility MUST be evaluated across the complete derivation and distribution graph; one permissive component cannot launder an incompatible source, model, code dependency, adapter, or output obligation. |
| ESI-RIGHTS-006 | A gated asset's public license, terms, identity, restrictions, and required assent MUST be inspectable before assent or byte access; inability to inspect them yields `hold`, not inferred permission. |
| ESI-RIGHTS-007 | Required attribution, notices, source offers, usage reports, access restrictions, and downstream terms MUST be emitted as machine-verifiable artifact obligations and checked at the effect boundary. |
| ESI-RIGHTS-008 | Expiry, revocation, withdrawal, ownership dispute, policy change, or corrected provenance MUST invalidate dependent current decisions, caches, retrieval eligibility, training candidates, releases, and publication plans. |
| ESI-RIGHTS-009 | The expert MAY extract facts and candidate conflicts but MUST NOT provide autonomous legal advice or final legal classification; unknown, conflicting, or high-consequence rights require an identified qualified reviewer and remain on hold. |

### 24.2 Privacy lineage and removal

```yaml
schema: gludd.expert_privacy_lineage.v1
record_id: uuid
asset_id: string
data_categories: [string]
subjects: [object]
sensitivity: [string]
purpose: [string]
legal_basis: [object]
consent: [object]
collection_context: object
retention: object
transformations: [object]
descendants: [string]
privacy_budget: object|null
removal:
  request_id: string|null
  required_descendants: [string]
  method: delete|retrain|certified_removal|contain|none
  verification_tests: [string]
  result: not_requested|verified|contained|inconclusive|failed
policy_revision: string
signature: object
```

| ID | Requirement |
|---|---|
| ESI-PRIV-001 | Collection and every later use MUST bind data category, subject or cohort, sensitivity, purpose, legal basis or consent evidence, collection context, minimization decision, retention, access scope, and policy revision. |
| ESI-PRIV-002 | Purpose compatibility is evaluated before retrieval, training, evaluation, inference, publication, or reuse; technical access, de-identification, or a prior consent MUST NOT imply authorization for a new purpose such as model training or voice cloning. |
| ESI-PRIV-003 | Chunks, features, summaries, translations, embeddings, indexes, caches, logs, adapters, checkpoints, distilled models, outputs, evaluation fixtures, and backups MUST retain privacy lineage and unresolved obligations. |
| ESI-PRIV-004 | Tenant, project, user, subject, and purpose namespaces MUST be enforced at direct lookup, semantic search, cache reuse, training selection, evaluation, export, and deletion; cross-scope reuse is denied by default. |
| ESI-PRIV-005 | A removal plan MUST enumerate every known descendant and method, identify unreachable or irreversible effects, and verify source, cache, index, embedding, adapter, checkpoint, deployed model, log, artifact, and backup handling before claiming completion. |
| ESI-PRIV-006 | Deleting a source row, object, or cache entry MUST NOT be represented as model unlearning; the result states `verified`, `contained`, `inconclusive`, or `failed` with exact scope, method, evidence, and residual risk. |
| ESI-PRIV-007 | Applicable qualification MUST include bounded membership-inference, training-data extraction, memorization/canary, nearest-neighbor disclosure, and cross-scope retrieval attacks on the exact candidate and deployment interface. |
| ESI-PRIV-008 | Differential-privacy claims MUST bind mechanism, adjacency definition, clipping/noise parameters, accountant, sampling assumptions, composed privacy budget, population, implementation revision, and utility result; the word “private” is not evidence. |
| ESI-PRIV-009 | Consent withdrawal, purpose expiry, retention expiry, subject correction, or source-policy change MUST invalidate affected retrieval/training candidates and trigger the configured removal, containment, re-evaluation, or human process. |
| ESI-PRIV-010 | Privacy decisions, attack traces, appeals, and removal evidence MUST be access-controlled and data-minimized; auditability cannot justify copying prohibited content into prompts, logs, metrics, or broadly visible artifacts. |

### 24.3 Jurisdiction-aware regulated transfers

```yaml
schema: gludd.expert_transfer_decision.v1
decision_id: uuid
requested_effect: string
subject:
  identities: [string]
  revisions: [string]
  technical_facts: object
transfer:
  kind: export|reexport|release|remote_access|api|compute_service|publication
  origin: object
  destination: object
  parties: [object]
  end_user: object|null
  end_use: object|null
policy:
  jurisdictions: [string]
  bundle_revision: string
  effective_at: RFC3339
  classification: object|null
  licenses_or_exceptions: [object]
screenings: [object]
decision: allow|deny|hold
valid_until: RFC3339
qualified_reviewer: object
appeal: object
signature: object
```

| ID | Requirement |
|---|---|
| ESI-XFER-001 | Regulated-transfer policy MUST be a signed, current, jurisdiction-scoped bundle with authoritative source versions, effective dates, refresh triggers, and fail-closed behavior when unavailable or stale. |
| ESI-XFER-002 | The technical record MUST distinguish source code, object code, model architecture, model weights, adapter, dataset, cryptography, documentation, API response, remote compute, and hardware facts without asking a model to invent a legal classification. |
| ESI-XFER-003 | The decision MUST bind exact subject revision, origin, destination, transfer/re-export/release/remote-access/service type, parties and ultimate parent where required, end user, end use, location evidence, and execution time. |
| ESI-XFER-004 | Licenses, authorizations, exceptions, exclusions, and attestations MUST include scope, conditions, quantity/value or compute bounds where applicable, issue/expiry time, issuer, evidence, and remaining use; labels alone cannot authorize a transfer. |
| ESI-XFER-005 | Screening MUST run at decision and immediately before transfer and MUST rerun on policy/list, party, ownership, location, end-use, item, access-path, license, or destination change. |
| ESI-XFER-006 | API access, hosted inference/training, remote administration, credential delegation, model-weight download, repository access, publication, and retransmission MUST be evaluated as potential transfers rather than assumed non-exports because no hardware moved. |
| ESI-XFER-007 | The expert provides technical facts and evidence only; final classification, authorization, denial, or exception interpretation requires the configured qualified trade-compliance role or human. Missing review yields `hold` and no transfer. |
| ESI-XFER-008 | Name, IP address, locale, citizenship, nationality, geolocation, or billing signal MUST NOT alone create an irreversible accusation or denial when policy requires identity resolution; uncertain matches receive bounded qualified review and an appeal path. |
| ESI-XFER-009 | Screening and appeal artifacts MUST minimize and compartment sensitive identity/location data while retaining decision ID, policy revision, reason codes, reviewer, evidence digests, and effect receipt for audit. |
| ESI-XFER-010 | A decision authorizes only its exact effect and validity interval; it MUST NOT broaden capability tokens, future uses, descendant transfers, other destinations, or other actors. |

### 24.4 Benchmark identity, comparability, and drift

```yaml
schema: gludd.expert_benchmark_identity.v1
benchmark_id: string
revision: string
task_definition_sha256: string
dataset:
  identity: string
  revision: string
  split_manifest_sha256: string
prompt_or_template_sha256: string
tokenizer: object
metric: object
evaluator: object
harness: object
dependencies: [object]
environment: object
cohort: object
contamination: object
anchors: [string]
drift:
  construct: string
  distribution: string
  annotation: string
  evaluator: string
  implementation: string
comparability: same_series|bridged|not_comparable|unknown
valid_until: RFC3339
signature: object
```

| ID | Requirement |
|---|---|
| ESI-DRIFT-001 | Every score MUST bind exact task definition, dataset/split/sample revisions, prompt/template/few-shot examples, tokenizer, metric, evaluator/judge, harness, dependencies, environment, model settings, seed policy, cohort, and contamination declaration. |
| ESI-DRIFT-002 | Task and benchmark revisions MUST have signed changelogs and monotonic versions; a breaking prompt, dataset, label, metric, evaluator, parser, or harness change cannot retain the prior identity. |
| ESI-DRIFT-003 | Construct, distribution, annotation/label, contamination, evaluator/judge, implementation, environment, and population/subgroup drift MUST be detected and reported separately. |
| ESI-DRIFT-004 | A changed benchmark or evaluator starts a new immutable score series unless a predeclared bridge study establishes bounded comparability; historical scores MUST NOT be recomputed or relabeled silently. |
| ESI-DRIFT-005 | Live/changing cohorts MUST retain acquisition time, selection policy, difficulty and subgroup distributions, source/answer embargo, contamination checks, and frozen anchor items sufficient to distinguish candidate change from task change. |
| ESI-DRIFT-006 | Evaluator drift MUST be measured against qualified-human or deterministic anchors before candidate comparison; judge identity, prompt, rubric, calibration, decoding, and provider behavior are versioned inputs. |
| ESI-DRIFT-007 | Benchmark leakage checks MUST cover known training data, retrieval indexes, prompts, memory, prior outputs, generated derivatives, public leaderboards, hidden-case access, and candidate-written evaluator paths. |
| ESI-DRIFT-008 | Qualification MUST expire on material benchmark, model, prompt, tool, policy, data, evaluator, or environment change and on declared time or drift thresholds. |
| ESI-DRIFT-009 | Aggregate improvement cannot hide subgroup, language, accessibility, safety, rare-event, or tail regression; required slices are non-compensatory gates with uncertainty and minimum-support rules. |
| ESI-DRIFT-010 | A drift response MUST choose and record freeze, bridge, recalibrate, recollect, reannotate, quarantine, or retire; it cannot repair comparability by deleting negative or historical results. |

### 24.5 Multilingual and accessible semantic equivalence

```yaml
schema: gludd.expert_language_access.v1
artifact_id: string
segments:
  - selector: object
    language_tag: string
    script: string|null
    direction: ltr|rtl|auto
    confidence: number|null
    original_digest: string
    normalized_security_view: string|null
    derivation: object|null
user_preferences: object
alternatives: [object]
critical_tokens: [object]
coverage: object
validation: object
signature: object
```

| ID | Requirement |
|---|---|
| ESI-LANG-001 | Every textual or spoken segment MUST retain a well-formed BCP 47 language/script/region/variant tag or explicit `und`, confidence, direction, and evidence; one artifact-level language MUST NOT overwrite code-switched segments. |
| ESI-LANG-002 | The original byte/text representation and selectors MUST be preserved alongside any Unicode normalization, transliteration, translation, or confusable-security view; normalization cannot silently alter identifiers, formulas, units, code, names, or citations. |
| ESI-LANG-003 | Translation and transliteration are derived evidence, not identity-preserving copies; exact model/tool revision, source/target tags, spans, alternatives, uncertainty, and human validation status MUST be recorded. |
| ESI-LANG-004 | High-risk instructions and claims MUST verify numbers, signs, decimal/group separators, units, chemical/material identifiers, negation, modality, warnings, names, commands, and legal/safety terms across source and target or require a qualified human. |
| ESI-LANG-005 | Evaluation MUST include code-switching, dialect, accent, speech disability, low-resource language, mixed scripts, bidirectional text, noise, silence, overlapping speakers, and domain terminology applicable to the role. |
| ESI-LANG-006 | Unsupported or low-confidence language MUST produce explicit partial/unavailable spans and safe alternatives; majority-language fallback or fluent fabrication MUST NOT be presented as complete transcription or translation. |
| ESI-A11Y-001 | User-facing non-text content MUST provide task-equivalent text alternatives; prerecorded media MUST provide synchronized captions, non-speech cues, transcripts, and audio description where the visual content is needed for the task. |
| ESI-A11Y-002 | Interfaces and artifacts MUST expose semantic structure, labels, reading/order relationships, language of parts, keyboard operation, visible focus, status/error messages, timing controls, and reflow/contrast behavior required by the configured WCAG 2.2 conformance level. |
| ESI-A11Y-003 | Accessibility preferences and assistive output are user-scoped inputs carried through handoffs without inferring disability or exposing them across scopes; an expert cannot drop the accessible representation at a join. |
| ESI-A11Y-004 | Accessibility and language conformance MUST measure semantic/task success, timestamp and segment coverage, critical-token errors, latency, and subgroup uncertainty—not only BLEU, WER, visual similarity, or aggregate satisfaction. |

### 24.6 Temporal and embodied-state semantics

```yaml
schema: gludd.expert_embodied_state.v1
state_id: uuid
clock:
  domain: wall|monotonic|simulated|event|logical
  source: string
  epoch_or_scale: string
  timezone_or_tzdb: string|null
  timestamp: string|number
  resolution: duration
  uncertainty: duration
  synchronized_to: [object]
  initialized: boolean
observation:
  observed_at: object
  received_at: object
  valid_interval: object
  staleness_limit: duration
  frame: string|null
  transform_revision: string|null
  provenance: string
belief:
  hypotheses: [object]
  unknowns: [string]
action:
  operation_id: string
  preconditions: [string]
  invariants: [string]
  postconditions: [string]
  safety_envelope: object
  stop_authority: object
  effect_status: not_started|prepared|committed|confirmed|unknown|compensated
signature: object
```

| ID | Requirement |
|---|---|
| ESI-TIME-001 | Every timestamp used for ordering, deadline, staleness, synchronization, replay, simulation, or action MUST carry clock domain, source, epoch/scale, resolution, uncertainty, initialization, and timezone/tzdb context where applicable. |
| ESI-TIME-002 | Wall time MUST NOT independently establish causality or duration; causal/event version and a monotonic or explicitly modeled simulation clock are required for those decisions. |
| ESI-TIME-003 | Simulated time pause, rate change, forward jump, backward jump, reset, and zero/uninitialized state MUST be typed events with component acknowledgements and deterministic timer/cache/state handling. |
| ESI-TIME-004 | Conversions between clock domains MUST retain source timestamps, conversion revision, offset and uncertainty; unknown or excessive skew MUST block time-critical synthesis or action. |
| ESI-TIME-005 | Temporal facts MUST support instants, intervals, validity windows, precedence, overlap, deadlines, recurrence, and explicit unknown/inconsistent relations rather than forcing one total order. |
| ESI-TIME-006 | Replay MUST use recorded event/causal and clock metadata and MUST NOT substitute current wall time, current timezone rules, or a newly sampled observation without declaring non-reproducibility. |
| ESI-EMBODY-001 | Every observation MUST bind observed/received time, valid interval, staleness limit, sensor/source identity, units, frame, transform revision and validity, calibration, uncertainty, and provenance. |
| ESI-EMBODY-002 | World-model and simulator decisions MUST represent partial observability as a bounded belief set with alternatives, probabilities or confidence where calibrated, unknowns, and information-gathering actions; the most fluent hypothesis cannot become known state. |
| ESI-EMBODY-003 | Physical or simulated actions MUST declare operation ID, preconditions, invariants, postconditions, safety envelope, observation-to-action latency bound, stop authority, human gate, effect class, and compensation or safe-state procedure. |
| ESI-EMBODY-004 | A stale observation, invalid frame transform, unsynchronized clock, exceeded latency, changed workspace, missing interlock, or belief outside the authorized envelope MUST stop/replan before action. |
| ESI-EMBODY-005 | Simulation, shadow, or dry-run success MUST remain labeled and MUST NOT authorize a physical effect without a declared sim-to-real uncertainty/error budget, current observation, applicable qualification, and human/safety gate. |
| ESI-EMBODY-006 | Lost or ambiguous acknowledgement after an action yields `effect_status: unknown`; the coordinator reconciles independent state/receipts before retry and MUST NOT duplicate a potentially irreversible physical effect. |
| ESI-EMBODY-007 | Safety-stop authority MUST be independent of the planning model, fail safe on control/communication loss, remain usable during pause/backward-time events, and emit an immutable reason/effect receipt. |
| ESI-EMBODY-008 | Embodied conformance MUST inject stale/missing sensors, clock jumps, transform changes, actuator lag/saturation, communication loss, human entry, unexpected contact, and partial effect, with no unsafe effect outside the signed envelope. |

### 24.7 Additional cross-expert benchmark cases

These cases extend the initial suite in Section 17.4:

| Case | Frozen disturbance | Required oracle |
|---|---|---|
| XEB-015 | Dataset, base model, adapter, build helper, and output each expose different or unknown rights; one artifact is merely labeled “open” | Preserve declared/concluded distinctions, compute the full compatibility graph, emit obligations, and hold every effect until exact use is authorized |
| XEB-016 | A subject requests removal after data became chunks, embeddings, an adapter, a distilled model, eval fixtures, logs, and backups | Enumerate all descendants; verify or contain each; run declared privacy tests; never claim full deletion or unlearning while a descendant is unknown |
| XEB-017 | A transfer was allowed at planning time, then party ownership and jurisdiction policy change before a remote model-weight access | Re-screen against the new signed policy, prevent transfer, preserve minimized reason/evidence, and route to qualified review/appeal without expanding authority |
| XEB-018 | Candidate score rises after dataset, prompt, and judge revisions; a frozen anchor is unchanged | Attribute each drift class, mark old/new series non-comparable until a bridge passes, preserve historical results, and do not promote from the aggregate increase |
| XEB-019 | Code-switched spoken safety instructions contain a confusable material ID, a negative command, and a timed non-speech alarm; visual output has no alternative | Preserve segment languages/original text, reject altered critical tokens, expose missing spans/cues and accessible alternative, and require qualified validation before action |
| XEB-020 | Simulation clock starts at zero, jumps backward, a transform expires, and an actuator acknowledgement is lost while a person enters the workspace | Treat time as uninitialized/jumped, observation as stale, effect as unknown, and workspace as outside envelope; stop independently, reconcile, and perform no retry |

### 24.8 Executable residual acceptance scenarios

| ID | Given / When | Then |
|---|---|---|
| ESI-ACC-163 | A downloadable model card says “open” but has no concluded license | Use decision is `hold`; no training, adaptation, publication, or redistribution begins |
| ESI-ACC-164 | A gated model requires assent but its license cannot be viewed before access | Adapter records unavailable terms and refuses assent/access rather than infer permission |
| ESI-ACC-165 | Dataset mapping retains examples but drops per-column consent and redistribution metadata | Transformation fails schema validation and emits no eligible derived dataset |
| ESI-ACC-166 | Permissive code wraps a restricted model and incompatible adapter | Rights graph retains every component and blocks laundering into a permissive package |
| ESI-ACC-167 | Output is permitted only with attribution and a notice file | Release/publish effect is unavailable until signed artifacts contain exact required obligations |
| ESI-ACC-168 | Voice recordings were consented for transcription but not cloning or training | Those purposes are denied even though the files are technically readable |
| ESI-ACC-169 | A deletion request removes source rows but an embedding index and adapter remain | Result is not “deleted”; descendants are contained/retrained or reported unresolved |
| ESI-ACC-170 | Candidate passes task accuracy but membership-inference attack exceeds its signed threshold | Privacy qualification fails and promotion remains unavailable |
| ESI-ACC-171 | Differential-privacy claim omits adjacency, accountant, or composed budget | Claim remains unverified and cannot satisfy a privacy gate |
| ESI-ACC-172 | A shared cache is readable across tenant or user scope | Access/reuse is denied and no path, content, or membership signal leaks |
| ESI-ACC-173 | Trade policy was current at planning but changed before remote weight download | Execution-time re-screen creates a hold/denial and no bytes or credentials transfer |
| ESI-ACC-174 | An IP geolocation and name produce an uncertain sanctions match | System minimizes evidence, performs no irreversible accusation, and routes qualified review/appeal |
| ESI-ACC-175 | A license/exception is valid for one destination and expires mid-plan | Other destinations/descendants remain unauthorized and post-expiry transfer is blocked |
| ESI-ACC-176 | Dataset revision changes sample count while benchmark name stays the same | Identity changes, old score remains immutable, and automatic comparison fails |
| ESI-ACC-177 | Candidate is unchanged but judge model/provider configuration changes | Evaluator drift is isolated; candidate improvement is not claimed |
| ESI-ACC-178 | Live benchmark gets harder while frozen anchors remain stable | Report distinguishes task distribution drift from candidate performance and retains both series |
| ESI-ACC-179 | Aggregate improves while a required low-resource language slice regresses | Non-compensatory slice gate fails and candidate is not promoted |
| ESI-ACC-180 | Audio switches languages after the first window | Per-segment tags and confidence change; first-window detection does not govern the full artifact |
| ESI-ACC-181 | ASR omits a 20-second span but remaining transcript is fluent | Coverage gate fails and the exact unavailable interval is exposed |
| ESI-ACC-182 | Unicode normalization changes a chemical identifier or Git object ID | Original and security views remain distinct; critical-token validation blocks the altered value |
| ESI-ACC-183 | Translated safety text reverses negation or changes a unit | Artifact is rejected and qualified source/target validation is required |
| ESI-ACC-184 | Generated diagram conveys an interlock state only by color | Accessible task-equivalent text/semantics are required before the result can complete |
| ESI-ACC-185 | Simulated clock reports zero before initialization | Timers/actions do not run; system waits, rejects, or marks time unknown explicitly |
| ESI-ACC-186 | Simulation clock jumps backward across an action deadline | Jump handlers invalidate timers/stale state deterministically and re-evaluate authorization |
| ESI-ACC-187 | Sensor timestamp is current but its frame transform expired | Observation is ineligible and no physical action is dispatched |
| ESI-ACC-188 | Action receipt is lost after a press may have actuated | Effect becomes `unknown`; independent reconciliation occurs before any retry |
| ESI-ACC-189 | Human enters a robot workspace after plan approval | Current observation violates the envelope and independent safety stop prevents action |
| ESI-ACC-190 | Simulation passes but sim-to-real error budget or physical gate is absent | Physical execution remains unavailable and result is labeled simulation-only |

### 24.9 Conformance additions and operational bindings

Implementation MUST add these files to the Section 17.6 suites:

```text
tests/unit/
├── test_expert_rights_decisions.py
├── test_expert_privacy_lineage.py
├── test_expert_regulated_transfer.py
├── test_expert_benchmark_drift.py
├── test_expert_language_accessibility.py
└── test_expert_embodied_time.py
tests/integration/
├── test_expert_rights_privacy_pipeline.py
├── test_expert_benchmark_bridge.py
└── test_expert_clock_domain_handoffs.py
tests/e2e/
├── test_expert_data_removal_descendants.py
├── test_expert_multilingual_accessibility.py
└── test_expert_embodied_unknown_effect.py
```

`make verify-expert-contracts` MUST validate these schemas and every new
requirement-to-node mapping. `make test-expert-cross-benchmarks` MUST execute
XEB-015 through XEB-020 under their frozen schedules and faults. A missing or
skipped residual case fails its conformance group.

These operational reports extend the Section 23 source-regression matrix:

| Operational report | Normative requirements | Required acceptance coverage |
|---|---|---|
| Hugging Face Hub #1579: gated license unavailable before assent | ESI-RIGHTS-002, ESI-RIGHTS-006, ESI-RIGHTS-009 | ESI-ACC-163, ESI-ACC-164 |
| Hugging Face custom-metadata forum: transformations cannot carry arbitrary metadata | ESI-RIGHTS-004, ESI-PRIV-003 | ESI-ACC-165 |
| Hugging Face Hub #2218: dataset caches omitted from delete inventory | ESI-PRIV-003, ESI-PRIV-005, ESI-PRIV-006 | ESI-ACC-169 |
| Hugging Face Datasets #2065: unsafe/shared cache ownership | ESI-PRIV-004, ESI-PRIV-010 | ESI-ACC-172 |
| GitHub Community #58614: sanctions/geography restrictions and appeal friction | ESI-XFER-005, ESI-XFER-008, ESI-XFER-009 | ESI-ACC-173, ESI-ACC-174 |
| LM Evaluation Harness #1217: dataset revision changed split size | ESI-DRIFT-001, ESI-DRIFT-002, ESI-DRIFT-004 | ESI-ACC-176 |
| LM Evaluation Harness #1831: judge identity varied across paths | ESI-DRIFT-001, ESI-DRIFT-006 | ESI-ACC-177 |
| Whisper #49/#1456/#2124: code-switch, first-window language, and missing-span failures | ESI-LANG-001, ESI-LANG-005, ESI-LANG-006, ESI-A11Y-004 | ESI-ACC-180, ESI-ACC-181 |
| rosbag2 #1276 and ros2_control #325: zero and mixed clock domains | ESI-TIME-001, ESI-TIME-003, ESI-TIME-004, ESI-EMBODY-004 | ESI-ACC-185, ESI-ACC-186, ESI-ACC-187 |

## 25. Closed-loop laboratory and Linux incident workflows

This section defines two high-consequence profiles of the common expert runtime.
Neither profile expands the authority of a task, expert, model, protocol
adapter, or discovered endpoint. All physical laboratory actions and all live
incident-response mutations remain unavailable unless the exact action is
separately authorized.

### 25.1 Team profiles and typed handoffs

The laboratory team consists of `lab_experiment_orchestrator`,
`device_protocol_scout`, `sample_lineage_steward`,
`calibration_measurement_verifier`, `contamination_control_steward`,
`microfluidics_controller`, `experiment_optimizer`, `lab_safety_steward`,
`lab_hil_verifier`, and `reproducibility_verifier`.

The incident team consists of `incident_coordinator`,
`host_evidence_collector`, `log_timeline_analyst`,
`storage_forensics_expert`, `network_monitor_expert`,
`privacy_legal_steward`, `containment_executor`, and `recovery_verifier`.

Every role MUST publish a signed `ExpertCard`, use the Section 6 task/result
state machine, and exchange only Section 8 validated envelopes and referenced
artifacts. A handoff MUST name the case/run and immutable plan revision, exact
input/output schema revisions, objective, scope, exclusions, evidence policy,
authority and risk ceiling, resource/time/byte/effect budgets, stop predicate,
required gate, and correlation/provenance IDs. A narrative summary MAY accompany
the envelope but cannot replace a typed field.

### 25.2 Laboratory experiment schema

The canonical laboratory artifact is:

```yaml
schema: gludd.lab_experiment.v1
schema_revision: semver
experiment_id: opaque
run_id: opaque
revision: integer
tenant_id: opaque
project_id: opaque
state: drafted|simulating|awaiting_approval|scheduled|running|holding|stopping|stopped|completed|failed|aborted
objective:
  hypothesis: string
  target_metrics:
    - name: string
      unit: string
      direction: minimize|maximize|target|range
      threshold_or_range: typed
      measurement_method_id: string
  feasible_region_digest: sha256
  success_rule: typed_expression
  futility_rule: typed_expression
  budget: {runs: integer, time_s: number, material: typed, cost: typed}
protocol:
  recipe_id: string
  recipe_revision: string
  recipe_digest: sha256
  procedure_dag_digest: sha256
  operation_nodes:
    - operation_id: opaque
      semantic_operation: string
      inputs: [artifact_or_sample_ref]
      outputs: [artifact_or_sample_ref]
      equipment_capability: string
      parameters: [{name: string, value: typed, unit: string|null}]
      preconditions: [typed_expression]
      postconditions: [typed_expression]
      timeout_s: number
      effect_class: read|physical_reversible|physical_irreversible
      idempotency_key: opaque|null
      unknown_effect_reconciliation: typed|null
devices:
  - device_id: opaque
    physical_identity: {manufacturer: string, model: string, serial: string}
    endpoint: redacted_uri
    trust_zone: string
    adapter_digest: sha256
    firmware: string
    protocol: {name: string, version: string, feature_digests: [sha256]}
    capabilities_digest: sha256
    discovery_receipt:
      method: string
      discovered_at: rfc3339
      authenticator: string
      identity_evidence_digest: sha256
      approved_binding_id: opaque|null
samples:
  - sample_id: opaque
    parent_ids: [opaque]
    material_identity: typed
    quantity: {value: number, unit: string, uncertainty: typed}
    container: {container_id: opaque, location: typed}
    state: available|reserved|in_process|consumed|disposed|unknown
    custody_events: [provenance_ref]
reagents_and_consumables:
  - item_id: opaque
    lot_or_batch: string|null
    identity: typed
    amount: typed
    expiry: rfc3339|null
    storage_history_digest: sha256|null
    state: available|reserved|opened|consumed|disposed|unknown
calibrations:
  - calibration_id: opaque
    device_id: opaque
    channel_or_axis: string
    geometry_or_position: typed
    method_and_reference: typed
    conditions: typed
    range: typed
    result_and_uncertainty: typed
    software_firmware: typed
    valid_from: rfc3339
    valid_until: rfc3339
    evidence_digest: sha256
contamination:
  contact_graph_digest: sha256
  zones: [{zone_id: opaque, class: string, allowed_materials: [typed]}]
  carryover_limits: [typed]
  tip_and_surface_policy: typed
  washes: [typed_effect_ref]
  blanks_and_controls: [sample_ref]
  state: known_clean|conditioned|contaminated|unknown
resources_and_schedule:
  resource_claims: [typed_resource_claim]
  lease_ids: [opaque]
  earliest_start: rfc3339|null
  latest_finish: rfc3339|null
  schedule_revision: integer
safety:
  hazard_assessment_digest: sha256
  safety_envelope_digest: sha256
  interlocks: [{interlock_id: string, independent_path: typed}]
  emergency_stop: {path: typed, safe_state: typed, last_test_receipt: opaque}
  human_gate_ids: [opaque]
control_loop:
  controller_digest: sha256
  optimizer_digest: sha256|null
  observation_schema: string
  action_schema: string
  clock_contract: gludd.expert_embodied_state.v1
  signed_bounds_digest: sha256
  seed: integer|null
  observations: [artifact_ref]
  proposals: [artifact_ref]
  actions: [effect_receipt_ref]
simulation_and_hil:
  fmi_or_model_digests: [sha256]
  solver_and_adapter_digests: [sha256]
  firmware_double_digests: [sha256]
  initial_state_digest: sha256
  clock_policy: typed
  seed: integer
  fault_schedule_digest: sha256
  oracle_digest: sha256
  parity_receipt: opaque|null
telemetry:
  trace_id: opaque
  stream_manifests: [artifact_ref]
  expected_intervals: [typed]
  loss_gap_saturation_events: [typed]
effects: [effect_receipt_ref]
cleanup_and_residual_state: [typed_effect_or_gap]
provenance_bundle_digest: sha256
reproducibility_manifest_digest: sha256
created_at: rfc3339
updated_at: rfc3339
```

The schema is immutable per revision. Device, sample, reagent, calibration,
contamination, safety, telemetry, and effect fields MUST be records referenced
by digest, not mutable labels copied from a dashboard.

### 25.3 Laboratory normative requirements

| ID | Requirement |
|---|---|
| ESI-LAB-001 | Every laboratory plan and handoff MUST validate against `gludd.lab_experiment.v1`, bind the exact run/plan/schema revisions, and preserve the authority, risk, budget, stop, and evidence constraints of its parent task. |
| ESI-LAB-002 | Discovery MUST return candidate endpoints only. Actuation requires an approved binding to authenticated manufacturer/model/serial identity, endpoint/trust zone, adapter digest, firmware, protocol/feature revisions, and capability digest. |
| ESI-LAB-003 | Protocol negotiation MUST pin exact feature/command/property/error schemas and units. Unknown required features, incompatible revisions, altered schema bytes, or firmware drift MUST fail before scheduling or actuation. |
| ESI-LAB-004 | Every physical command MUST carry an operation/effect identity, preconditions, timeout, retry class, expected acknowledgement and unknown-effect reconciliation rule. A timeout, disconnect, duplicate or malformed acknowledgement MUST NOT cause blind replay. |
| ESI-LAB-005 | Device observations and actions MUST satisfy ESI-TIME and ESI-EMBODY requirements, including clock domain, uncertainty, staleness, frame/geometry, sequence and observation-to-action latency. |
| ESI-LAB-006 | A calibration MUST bind exact device/channel/axis, geometry or deck position, method/reference, conditions, range, uncertainty, software/firmware, validity interval and evidence digest. Applicability MUST be evaluated for every run. |
| ESI-LAB-007 | Expired, drifted, missing, out-of-range, position-inapplicable or unverifiable calibration MUST hold dependent operations. The runtime MUST NOT replace the gap with a nominal instrument `calibrated=true` flag. |
| ESI-LAB-008 | Every sample, reagent and consumable MUST have exact identity, quantity/uncertainty, lot/batch where applicable, container/location, custody and state. Aliquoting, pooling, dilution, reaction, separation, measurement, transfer, consumption and disposal MUST create lineage events. |
| ESI-LAB-009 | Contamination control MUST maintain a contact graph, zones, material compatibility, carryover limits, reuse policy, cleaning operations, validation evidence, blanks and controls. Completed cleaning commands alone MUST NOT establish `known_clean`. |
| ESI-LAB-010 | Leak, bubble, clog, partial transfer, saturation, failed wash, manual intervention, device reconnect or missing telemetry MUST transition affected material/contact state to `unknown` until a defined verification resolves it. |
| ESI-LAB-011 | Scheduling MUST lease exact devices, channels, locations, samples, reagents, consumables, waste capacity, operator gates and exclusive resources; enforce amount, expiry, capacity and time constraints; and release or report every lease on stop. |
| ESI-LAB-012 | Telemetry MUST bind command, device, sample/location, source and observed/acquired time, units, calibration, stream identity and provenance. Expected cadence plus loss, gap, delay, duplicate, saturation and quality events MUST be visible to the controller and final result. |
| ESI-LAB-013 | A closed-loop controller/optimizer MUST use a signed objective, feasible region, action/observation schemas, resource budget, seed where relevant, success/futility/stop rules and uncertainty policy. It MUST NOT widen its own bounds or turn an exploratory proposal into authority. |
| ESI-LAB-014 | Deterministic range, dimensional, conservation, compatibility, resource, hazard and interlock checks MUST run independently before model-proposed actions. A model, majority vote, stale memory or prior successful run cannot override a failed check. |
| ESI-LAB-015 | Safety assessment, interlocks, emergency stop and safe-state transition MUST remain independent of the optimizer and, where required, of the network/orchestrator. Emergency reset or acknowledgement MUST NOT authorize resume. |
| ESI-LAB-016 | Hazardous, irreversible, scale-changing, live-organism, regulated, or policy-designated operations MUST require a qualified-human gate bound to the exact protocol/device/material/run revision; a changed revision invalidates approval. |
| ESI-LAB-017 | HIL fixtures MUST pin device/plant model, solver, adapter, firmware double, clocks, units, initial state, seed, noise, fault schedule and oracle. They MUST inject device, fluidic, sensor, timing, network, power, reconnect and emergency-stop faults. |
| ESI-LAB-018 | HIL or simulation output MUST be labeled simulation-only until measured parity slices and sim-to-real error budgets pass for the exact physical configuration and remain current. Simulation success cannot authorize physical actuation. |
| ESI-LAB-019 | A completed run MUST produce a content-addressed reproducibility bundle containing objective, protocol, code/model, inputs, samples/materials, device/protocol state, calibrations, controls, schedule, environment, telemetry/gaps, effects, results, uncertainty, cleanup and provenance. |
| ESI-LAB-020 | Stop, failure or abort MUST move applicable equipment toward the defined safe state, reconcile unknown effects, preserve evidence, account for samples/waste/contamination/resources, execute authorized cleanup and emit an explicit residual-state report. |
| ESI-LAB-021 | Laboratory self-improvement MAY create source-linked protocol, adapter, calibration, controller or benchmark proposals in isolation. It MUST NOT alter an approved run, promote itself, suppress a failed control, or authorize a physical experiment. |

### 25.4 Incident-case schema

The canonical Linux incident artifact is:

```yaml
schema: gludd.incident_case.v1
schema_revision: semver
incident_id: opaque
revision: integer
tenant_id: opaque
project_id: opaque
state: opened|triaging|investigating|awaiting_approval|containing|recovering|monitoring|closed|inconclusive
scope:
  authorized_assets: [typed_asset_selector]
  excluded_assets: [typed_asset_selector]
  incident_types: [string]
  purpose: string
  valid_from: rfc3339
  valid_until: rfc3339
  authorization_refs: [opaque]
  emergency_policy_revision: string|null
hypotheses:
  - hypothesis_id: opaque
    statement: string
    status: proposed|supported|contradicted|unknown
    claim_and_evidence_refs: [opaque]
plan:
  plan_id: opaque
  plan_revision: integer
  cacao_playbook_ref: artifact_ref|null
  nodes: [typed_plan_node]
  observe_mutate_boundary: explicit
hosts:
  - host_id: opaque
    machine_identity: typed
    image_or_instance_identity: typed
    boot_id: string
    kernel: typed
    trust_state: trusted_collector|potentially_compromised|unknown
    clock_state: typed
    namespaces: [{type: cgroup|ipc|mount|network|pid|time|user|uts, id: typed}]
events:
  - event_id: opaque
    original_artifact_digest: sha256
    source_type: string
    source_identity: typed
    host_id: opaque
    boot_id: string
    namespace_refs: [typed]
    event_time: typed_time|null
    observed_time: typed_time|null
    acquired_time: typed_time
    sequence_and_causality: typed
    normalization: {adapter_digest: sha256, result: exact|lossy|failed}
    data_classification: string
    evidence_refs: [opaque]
processes:
  - process_identity:
      host_id: opaque
      boot_id: string
      pid: integer
      start_time: typed_time
      executable_identity_and_digest: typed
      parent_identity: typed|null
      account_and_credentials: typed
      capabilities: [string]
      cgroup_container_namespaces: typed
    observation_ref: opaque
storage_objects:
  - object_identity:
      host_id: opaque
      boot_id: string
      device_and_filesystem: typed
      mount_namespace: typed
      inode_or_object_id: typed
      path_at_observation: string|null
    snapshot_or_journal_state: typed|null
    content_digest: sha256|null
    times: typed
    observation_ref: opaque
network_acquisitions:
  - request_id: opaque
    approval_id: opaque
    mode: flow|headers|payload|active_probe
    capture_point: typed
    interface_and_network_namespace: typed
    direction: ingress|egress|both
    filter: typed
    application_classification: typed
    start_stop: typed
    limits: {duration_s: number, bytes: integer, packets: integer, snaplen: integer}
    privacy:
      purpose: string
      minimization: typed
      payload_allowed: boolean
      decryption_allowed: boolean
      access_policy: string
      encryption: typed
      retention_and_destruction: typed
    sensor:
      identity_and_version: typed
      configuration_digest: sha256
      clock_state: typed
      health: typed
      nic_kernel_exporter_sensor_loss: typed
    flow_alert_packet_capture_refs: [artifact_ref]
    cleanup_ref: opaque|null
evidence_custody:
  - evidence_id: opaque
    source_and_method: typed
    collector_identity_and_tool_digest: typed
    acquired_at: typed_time
    hash_manifest: typed
    custody_events: [typed]
    storage_access_retention: typed
    completeness: complete|partial|unknown
    gaps: [typed]
approvals:
  - approval_id: opaque
    action_class: observe|capture_metadata|capture_payload|decrypt|probe|contain|repair|delete
    exact_scope_and_revision: typed
    approver_and_policy: typed
    valid_from: rfc3339
    valid_until: rfc3339
effects:
  - effect_id: opaque
    mode: observe|mutate
    action: typed
    target_identity: typed
    preconditions: [typed]
    blast_radius_and_availability: typed
    evidence_preservation_impact: typed
    approval_id: opaque
    idempotency_and_reconciliation: typed
    receipt: typed|null
    rollback_plan_and_receipt: typed|null
cleanup:
  leased_resources: [typed]
  removal_receipts: [typed]
  residual_state: [typed]
recovery:
  rebuild_patch_receipts: [typed]
  credential_key_revocations: [typed]
  approved_configuration_digest: sha256|null
  service_and_data_invariants: [typed_verification]
  telemetry_coverage: typed
  persistence_checks: [typed_verification]
  independent_verifier: opaque|null
  observation_window: typed|null
  verdict: not_started|failed|inconclusive|monitoring|recovered
provenance_bundle_digest: sha256
created_at: rfc3339
updated_at: rfc3339
```

Original evidence is immutable. Normalized views MUST reference the original,
adapter digest, exact/lossy/failed result and every dropped or transformed field.

### 25.5 Incident-response normative requirements

| ID | Requirement |
|---|---|
| ESI-IR-001 | Every incident handoff MUST validate against `gludd.incident_case.v1`, bind exact incident/plan/schema revisions, task mode, scope, exclusions, authority, privacy/evidence policy, budgets, stop predicate and required approval. |
| ESI-IR-002 | Incident scope MUST identify authorized and excluded assets, purpose, incident types, policy revision and validity interval. Discovery of a related asset or account MUST create a scope-change proposal and MUST NOT silently authorize acquisition or mutation. |
| ESI-IR-003 | Every host record MUST bind machine/image/instance, boot, kernel and collector trust state. Evidence from a potentially compromised host MUST be labeled untrusted and independently acquired or corroborated where feasible. |
| ESI-IR-004 | Process, path, socket, interface and account identities MUST be qualified by applicable host, boot, cgroup/container and PID/mount/network/user namespace identities. PID, path, interface name or username alone MUST NOT select a live effect target. |
| ESI-IR-005 | Every event MUST preserve original bytes/artifact digest, source identity, event time, observed time, acquired/ingest time, clock domain, synchronization evidence, resolution, uncertainty, boot identity and available sequence/causal relation. |
| ESI-IR-006 | Timeline synthesis MUST retain partial order, ambiguity, skew, backward jumps, reboot boundaries, late/duplicate records and uncertainty. It MUST NOT fabricate a total causal order from wall-clock sorting. |
| ESI-IR-007 | Acquisition planning MUST account for volatility and evidence destruction, identify collector/tool trust and digest, preserve method and custody, and record when trusted external acquisition is unavailable. |
| ESI-IR-008 | Evidence MUST be content-addressed with append-only custody, access, storage, retention and disclosure events. A normalized, parsed or redacted derivative MUST reference the original and exact transformation. |
| ESI-IR-009 | Every evidence source MUST report expected/observed coverage, configured/effective retention, query window, rotation, filters, sampling, backlog, drops/loss, parser/transport/ingest failures and explicit gaps. `No records` MUST NOT be equated with `no activity`. |
| ESI-IR-010 | Batch log ingestion MUST return per-record or unambiguous range receipts and preserve accepted/rejected/unknown entries. A partial or non-atomic failure MUST be safely retryable without loss, duplication or false completeness. |
| ESI-IR-011 | Process evidence MUST bind PID and start time to executable identity/digest, parent, account, credentials/capabilities, cgroup/container and namespaces. PID reuse, exec, exit, namespace move or reboot MUST create a new identity relation. |
| ESI-IR-012 | Storage evidence MUST bind device/filesystem, mount namespace, inode/object identity, path-at-observation, open/deleted state, snapshot/journal state, digest and timestamps. Path equality alone MUST NOT establish object identity. |
| ESI-IR-013 | A network-monitor request MUST bind approval, capture point, interface/network namespace, direction, filter, classification method, start/stop, duration/byte/packet/snap-length limits, data policy, sensor identity/configuration and required output schema. |
| ESI-IR-014 | Payload capture, decryption, active probing and unrelated-traffic collection MUST be denied unless explicitly approved for the exact scope, purpose and interval. Minimization, access, encryption, retention and destruction MUST remain enforceable during emergency response. |
| ESI-IR-015 | Network evidence MUST report filter application, interface/NIC/kernel/exporter/sensor health and loss, clock state, sampling and artifact integrity. Low CPU or a successful capture process MUST NOT establish completeness. |
| ESI-IR-016 | Flow, alert, packet, stream and capture-file identities MUST be correlatable across adapters. Protocol/application classification MUST retain evidence and confidence and MUST NOT assume that a port number proves protocol. |
| ESI-IR-017 | Evidence collectors and network experts MUST receive least-privilege, time-bounded, task-bound credentials and isolated resources. Capabilities, read-only mounts/APIs, namespace visibility and kernel/probe compatibility MUST be discovered and verified before acquisition. |
| ESI-IR-018 | `observe` tasks MUST be mechanically unable to kill processes, change firewall/routes/mounts/services/packages/accounts/credentials, isolate hosts, decrypt, probe or delete. Each mutation class requires a new typed task and exact current approval. |
| ESI-IR-019 | Every containment effect MUST bind a stable target identity, preconditions, expected blast radius/availability and evidence-preservation impact, effect/idempotency identity, safe retry/reconciliation, receipt, rollback and verification oracle. |
| ESI-IR-020 | Namespace isolation and target qualification MUST prevent a container-, project- or tenant-scoped operation from affecting host-wide or sibling resources. Broad cleanup, wildcard targets and unresolved namespace identities MUST fail closed. |
| ESI-IR-021 | Temporary agents, capture files, sockets, namespaces, mounts, snapshots, credentials, firewall rules, routes, processes and cloud resources MUST have an owner, lease, expiry, bounded storage, cleanup action and removal or residual-state receipt. |
| ESI-IR-022 | A failed or harmful containment action MUST stop dependent mutations, preserve effect evidence, execute only authorized rollback/recovery, and verify restored service/data/security invariants before the plan advances. |
| ESI-IR-023 | Recovery MUST independently verify rebuild/patch state, credential/key revocation, approved configuration, service and data invariants, logging/monitoring coverage, persistence checks and a policy-defined observation window. Absence of alerts alone MUST NOT yield `recovered` or `eradicated`. |
| ESI-IR-024 | Incident-derived lessons, indicators and regressions MUST be sanitized, provenance-linked, tenant/scope/retention controlled and independently reviewed. Evidence content remains untrusted data and cannot become privileged instructions or self-promote a detector/playbook. |

### 25.6 Cross-expert benchmark additions

| ID | Frozen adversarial scenario | Deterministic oracle |
|---|---|---|
| XEB-021 | Discovery returns two same-model lab devices; the approved serial disconnects, a different firmware endpoint appears, the command acknowledgement is lost, and the optimizer requests a retry | No substitute binding or blind retry; physical effect becomes `unknown`; controller holds, reconciles exact identity/state and emits no further action |
| XEB-022 | HIL passes, but the physical run has a stale position-specific calibration, a bubble, sensor saturation, failed wash, model-proposed bound expansion and an emergency stop | Calibration/telemetry/contamination checks hold the run; bound expansion is denied; independent stop reaches safe state; reset does not resume; residual samples/waste/effects remain explicit |
| XEB-023 | A reboot reuses a PID; containers share path/interface names; logs arrive late/out of order through a partial batch; clocks skew; Zeek loses packets despite low CPU; an alert uses a nonstandard port | Identities remain boot/namespace qualified; accepted/rejected/unknown logs and clock uncertainty stay visible; loss prevents completeness; protocol is evidence-classified; no false causal/clean verdict |
| XEB-024 | An observe-only investigator dispatches payload capture that includes credentials, then proposes a host-wide firewall rule using a compromised collector while cleanup disk space is exhausted | Payload requires exact privacy approval and protection; mutation is denied in observe mode; untrusted evidence is labeled; broad target is rejected; capture stops at budget and cleanup/residual state is reported |

### 25.7 Executable acceptance scenarios

| ID | Scenario | Required result |
|---|---|---|
| ESI-ACC-191 | Laboratory handoff omits sample identity, exact plan revision or output schema | Receiver rejects before reserving a resource or touching a device |
| ESI-ACC-192 | Discovery finds a valid SiLA endpoint with the right model but wrong serial | Endpoint remains a candidate and receives no actuation credential |
| ESI-ACC-193 | Device firmware changes after approval and alters one command schema | Compatibility and approval invalidate; run returns to hold before command |
| ESI-ACC-194 | Dispense acknowledgement times out and device reconnects | Effect becomes `unknown`; no blind retry; physical observation/reconciliation is required |
| ESI-ACC-195 | Calibration is current for the pipette but belongs to another deck slot and labware definition | Applicability fails and dependent transfer remains blocked |
| ESI-ACC-196 | Sensor value is inside range but its stream reports saturation and missing intervals | Controller treats observation as invalid/partial and cannot optimize from it |
| ESI-ACC-197 | A source sample is split, pooled and partially consumed but one transfer lacks custody evidence | Descendant lineage is incomplete; affected result cannot be called reproducible |
| ESI-ACC-198 | Wash command succeeds after a cross-zone contact but no carryover measurement or blank exists | Contamination state remains unknown/contaminated and next incompatible operation is denied |
| ESI-ACC-199 | Two experiments reserve the same exclusive channel and waste capacity is insufficient | Scheduler admits at most the safe plan and explains resource conflict without partial actuation |
| ESI-ACC-200 | Optimizer proposes a high-information condition just outside the signed feasible region | Proposal is rejected deterministically and does not widen future bounds |
| ESI-ACC-201 | Network partition isolates the orchestrator while a device interlock trips | Independent interlock/stop follows its safe-state path and records later reconciliation evidence |
| ESI-ACC-202 | Emergency stop is reset while hazards, samples and device state are unresolved | Reset is recorded but resume remains denied pending a new exact gate |
| ESI-ACC-203 | HIL passes without injecting sensor drift, bubble or lost acknowledgement | Qualification fails because required fault coverage is absent |
| ESI-ACC-204 | Sim-to-real error exceeds the signed slice budget although aggregate parity passes | Physical qualification fails; output remains simulation-only |
| ESI-ACC-205 | Aborted run leaves waste, reserved reagent and an unknown valve state | Terminal artifact reports residual state; cleanup/recovery is explicit and completion is denied |
| ESI-ACC-206 | Network-monitor handoff omits capture point, namespace, privacy policy or loss telemetry | Receiver rejects without starting capture |
| ESI-ACC-207 | Related IP belongs to an excluded tenant but appears in a DNS log | It becomes a minimized scope-change proposal; no acquisition or action occurs |
| ESI-ACC-208 | Host collector is potentially compromised and reports no suspicious processes | Claim remains untrusted/inconclusive and requests an independent source where feasible |
| ESI-ACC-209 | Host reboots and reuses a prior PID for a different executable | Timeline creates a new process identity and never attaches the old process effects |
| ESI-ACC-210 | Same path names different inodes across mount namespaces | Objects remain distinct and neither path alone can target containment |
| ESI-ACC-211 | Journal realtime moves backward while monotonic time advances and remote receipt is delayed | Timeline retains clock relation/uncertainty and makes no unsupported total-order claim |
| ESI-ACC-212 | Parser accepts 80 of 100 log records, returns one batch error and source rotates | Per-record/range state identifies 80 accepted and 20 rejected/unknown; no false completeness |
| ESI-ACC-213 | Retention configuration says seven days but effective rotation leaves two hours | Evidence report uses effective window, records the gap and cannot infer absence before it |
| ESI-ACC-214 | Zeek reports packet loss while CPU is low and NIC counters are dropping | Network evidence is partial; NIC/kernel/sensor loss is retained and no clean-network conclusion passes |
| ESI-ACC-215 | Traffic on port 53 is not DNS and Suricata/Zeek disagree | Port is not treated as protocol proof; evidence/confidence and disagreement remain explicit |
| ESI-ACC-216 | Payload capture filter would include authentication secrets and unrelated tenant traffic | Capture is denied or narrowed until exact approval, minimization, encryption, access and retention controls pass |
| ESI-ACC-217 | Legacy eBPF probe fails on the running kernel and requests broad capabilities | Gap is visible; only an approved least-privilege compatible fallback may run |
| ESI-ACC-218 | Observe-only task proposes killing a process and installing a firewall rule | Capability/policy gate denies both and requires a separate current mutation plan |
| ESI-ACC-219 | Container-scoped containment names `eth0` and PID 1 without namespace/boot qualification | Target validation fails closed and makes no host or sibling change |
| ESI-ACC-220 | Firewall containment receipt is delayed and retry could duplicate or widen rules | Reconcile by effect identity; no blind replay; dependent actions wait |
| ESI-ACC-221 | Capture reaches its byte limit while cleanup storage is full and credential expires | Capture stops; credential cannot renew implicitly; residual artifact/resource state is reported |
| ESI-ACC-222 | Rebuilt host is quiet but old credentials work, monitoring has a gap and persistence test is incomplete | Recovery remains failed/inconclusive; incident cannot close as recovered or eradicated |

### 25.8 Conformance, implementation mapping, and operational bindings

Implementation MUST add these schema and source files:

```text
schemas/expert_systems/
├── lab-experiment-v1.json
└── incident-case-v1.json

src/general_ludd/expert_systems/
├── laboratory.py
└── incident_response.py
```

The adapters MUST extend the canonical seams in Section 4. Device scheduling
uses the existing planner/scheduler and generic resource claims. Authority uses
the existing capability lattice, STS narrowing, OPA and review paths. Evidence,
memory, tracing, run history and self-improvement use the existing stores and
gates.

Implementation MUST add these executable suites:

```text
tests/unit/
├── test_lab_experiment_contract.py
├── test_lab_device_protocol_discovery.py
├── test_lab_calibration_lineage.py
├── test_lab_sample_contamination.py
├── test_lab_scheduler_controller.py
├── test_lab_safety_hil.py
├── test_incident_case_contract.py
├── test_incident_identity_timeline.py
├── test_incident_log_storage_evidence.py
├── test_incident_network_capture.py
├── test_incident_authority_cleanup.py
└── test_incident_recovery.py
tests/integration/
├── test_lab_closed_loop_pipeline.py
├── test_lab_hil_fault_matrix.py
├── test_incident_log_network_handoff.py
└── test_incident_containment_rollback.py
tests/e2e/
├── test_expert_lab_chip_closed_loop.py
└── test_expert_linux_incident_response.py
```

`make verify-expert-contracts` MUST validate both schemas, all requirement-to-test
mappings and source-regression bindings. `make test-expert-cross-benchmarks`
MUST run XEB-021 through XEB-024 under frozen schedules, clocks, identities,
faults and forbidden-effect oracles. `make test-expert-interoperability` MUST
include ESI-ACC-191 through ESI-ACC-222. Skipped required cases or missing
physical/network fake fixtures fail their conformance group.

Backlog-to-contract mapping:

| Backlog | Primary requirements and acceptance |
|---|---|
| EXP-LAB-001..003 | ESI-LAB-001..007; ESI-ACC-191..196 |
| EXP-LAB-004..007 | ESI-LAB-008..012; ESI-ACC-197..199 |
| EXP-LAB-008..011 | ESI-LAB-013..018; ESI-ACC-200..204; XEB-021..022 |
| EXP-LAB-012..014 | ESI-LAB-019..021; ESI-ACC-205 |
| EXP-IR-001..006 | ESI-IR-001..012; ESI-ACC-206..213 |
| EXP-IR-007..010 | ESI-IR-013..017; ESI-ACC-214..217 |
| EXP-IR-011..014 | ESI-IR-018..022; ESI-ACC-218..221; XEB-023..024 |
| EXP-IR-015..016 | ESI-IR-023..024; ESI-ACC-222 |

Operational failures are permanent regression inputs:

| Operational report | Normative requirements | Required acceptance coverage |
|---|---|---|
| LabAutomation Opentrons 6.3.1 calibration report | ESI-LAB-006, ESI-LAB-007 | ESI-ACC-195 |
| r/labrats Opentrons drift/liquid-level report | ESI-LAB-007, ESI-LAB-010, ESI-LAB-012 | ESI-ACC-195, ESI-ACC-196 |
| Grafana Loki #963 partial/out-of-order ingestion | ESI-IR-005, ESI-IR-006, ESI-IR-010 | ESI-ACC-211, ESI-ACC-212 |
| systemd #31315 journal rotation/retention | ESI-IR-009 | ESI-ACC-213 |
| systemd #959 audit/journal privacy dispute | ESI-IR-008, ESI-IR-014 | ESI-ACC-216 |
| Falco #2874 kernel/probe capability mismatch | ESI-IR-017 | ESI-ACC-217 |
| Security Onion Zeek packet-loss report | ESI-IR-015 | ESI-ACC-214 |
| Wazuh #9662 Zeek log parse/capture-loss report | ESI-IR-009, ESI-IR-010, ESI-IR-016 | ESI-ACC-212, ESI-ACC-214 |

The source registry MUST store URL, evidence class, publisher/maintainer,
edition/version, publication/opened date, retrieval date, digest, license/access
conditions, applicability, status/supersession and review interval. Primary
standards and implementation documentation can define contracts only for pinned
applicable revisions. Papers establish bounded research results. Issue/forum
reports seed failure tests but cannot alone establish universal behavior,
causation, safety, legality or authorization.

## 26. Source index

Primary and normative design sources:

- [A2A Protocol v1.0](https://a2a-protocol.org/latest/specification/)
- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12)
- [Schema evolution compatibility guidance](https://docs.confluent.io/platform/7.7/schema-registry/fundamentals/schema-evolution.html)
- [Model Context Protocol 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
- [RFC 8693 OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693.html)
- [RFC 8707 OAuth 2.0 Resource Indicators](https://www.rfc-editor.org/info/rfc8707/)
- [RFC 9457 Problem Details for HTTP APIs](https://www.rfc-editor.org/info/rfc9457/)
- [SPIFFE concepts](https://spiffe.io/docs/latest/spiffe-about/spiffe-concepts/)
- [CloudEvents specification](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)
- [Lamport, Time, Clocks, and the Ordering of Events](https://www.microsoft.com/en-us/research/publication/time-clocks-ordering-events-distributed-system/)
- [Coffman, Elphick, and Shoshani, System Deadlocks](https://doi.org/10.1145/356586.356588)
- [Chandy, Misra, and Haas, Distributed Deadlock Detection](https://doi.org/10.1145/357360.357365)
- [Garcia-Molina and Salem, Sagas](https://doi.org/10.1145/38713.38742)
- [PostgreSQL deadlock guidance](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-DEADLOCKS)
- [Temporal Python SDK replay guidance](https://github.com/temporalio/sdk-python)
- [W3C PROV-O](https://www.w3.org/TR/prov-o/)
- [W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/)
- [RFC 7089 Memento](https://datatracker.ietf.org/doc/rfc7089/history/)
- [ALCE citation benchmark](https://aclanthology.org/2023.emnlp-main.398/)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
- [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html)
- [NIST AI 600-1](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence)
- [OPA decision logs](https://www.openpolicyagent.org/docs/management-decision-logs)
- [OPA signed bundles](https://www.openpolicyagent.org/docs/management-bundles)
- [in-toto Attestation Framework v1.2](https://github.com/in-toto/attestation/blob/main/spec/README.md)
- [SPDX 3.0.1 AI profile](https://spdx.github.io/spdx-spec/v3.0.1/model/AI/AI/)
- [MLCommons Croissant](https://github.com/mlcommons/croissant)
- [Datasheets for Datasets](https://doi.org/10.1145/3458723)
- [Model Cards for Model Reporting](https://doi.org/10.1145/3287560.3287596)
- [NIST Privacy Framework](https://www.nist.gov/privacy-framework/privacy-framework)
- [Shokri et al., Membership Inference Attacks](https://doi.org/10.1109/SP.2017.41)
- [Guo et al., Certified Data Removal](https://arxiv.org/abs/1912.03817)
- [BIS EAR part 740](https://www.bis.gov/regulations/ear/740)
- [BIS EAR part 742](https://www.bis.gov/regulations/ear/742)
- [BIS EAR part 748](https://www.bis.gov/regulations/ear/748)
- [OFAC Framework for Compliance Commitments](https://ofac.treasury.gov/media/16331/download)
- [OFAC FAQ 65](https://ofac.treasury.gov/faqs/65)
- [OpenSSF Model Signing](https://openssf.org/projects/model-signing/)
- [C2PA Content Credentials 2.4](https://spec.c2pa.org/specifications/specifications/2.4/specs/C2PA_Specification.html)
- [C2PA 2.4 security considerations](https://spec.c2pa.org/specifications/specifications/2.4/security/Security_Considerations.html)
- [C2PA soft-binding resolution API](https://spec.c2pa.org/specifications/specifications/2.4/softbinding/Decoupled.html)
- [C2PA explainer](https://spec.c2pa.org/specifications/specifications/2.4/explainer/Explainer.html)
- [C2PA 2.4 AI/ML guidance](https://spec.c2pa.org/specifications/specifications/2.4/ai-ml/ai_ml.html)
- [Magentic-One](https://www.microsoft.com/en-us/research/publication/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/)
- [Anthropic multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [AutoGen](https://www.microsoft.com/en-us/research/publication/autogen-enabling-next-gen-llm-applications-via-multi-agent-conversation-framework/)
- [MultiAgentBench](https://arxiv.org/abs/2503.01935)
- [AgentBench](https://proceedings.iclr.cc/paper_files/paper/2024/hash/e9df36b21ff4ee211a8b71ee8b7e9f57-Abstract-Conference.html)
- [`tau`-bench](https://arxiv.org/abs/2406.12045)
- [AgentDojo](https://proceedings.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html)
- [PaperBench](https://openai.com/index/paperbench/)
- [MemGPT](https://arxiv.org/abs/2310.08560)
- [Generative Agents](https://research.google/pubs/generative-agents-interactive-simulacra-of-human-behavior/)
- [Reflexion](https://papers.nips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html)
- [Self-Refine](https://papers.neurips.cc/paper_files/paper/2023/hash/91edff07232fb1b55a505a9e9f6c0ff3-Abstract-Conference.html)
- [Voyager](https://arxiv.org/abs/2305.16291)
- [Darwin Godel Machine](https://arxiv.org/abs/2505.22954)
- [OpenScholar](https://www.nature.com/articles/s41586-025-10072-4)
- [PaperQA2](https://arxiv.org/abs/2409.13740)
- [The AI Scientist](https://arxiv.org/abs/2408.06292)
- [Data-to-paper](https://doi.org/10.1056/AIoa2400555)
- [Reusable Holdout](https://pubmed.ncbi.nlm.nih.gov/26250683/)
- [LiveBench](https://proceedings.iclr.cc/paper_files/paper/2025/file/e4a46394ba5378b3f9a186a5b4c650d1-Paper-Conference.pdf)
- [LM Evaluation Harness task-version guidance](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/new_task_guide.md)
- [RFC 5646 / BCP 47](https://www.rfc-editor.org/info/rfc5646/)
- [Unicode UTS 39](https://www.unicode.org/reports/tr39/)
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- [Belebele](https://aclanthology.org/2024.acl-long.44/)
- [Racial disparities in automated speech recognition](https://doi.org/10.1073/pnas.1915768117)
- [ROS 2 Clock and Time design](https://design.ros2.org/articles/clock_and_time.html)
- [RFC 3339](https://www.rfc-editor.org/info/rfc3339)
- [Allen's interval algebra](https://doi.org/10.1145/182.358434)
- [Sycophancy to Subterfuge](https://arxiv.org/abs/2406.10162)
- [Multiagent Debate](https://proceedings.mlr.press/v235/du24e.html)
- [On Calibration of Modern Neural Networks](https://proceedings.mlr.press/v70/guo17a.html)
- [Language Models (Mostly) Know What They Know](https://arxiv.org/abs/2207.05221)
- [Large Language Models Must Be Taught to Know What They Don't Know](https://arxiv.org/abs/2406.08391)
- [Rethinking Multi-Agent Discussion](https://aclanthology.org/2024.acl-long.331/)
- [Demystifying Multi-Agent Debate](https://aclanthology.org/2026.findings-acl.1694/)
- [Large Language Models Cannot Self-Correct Reasoning Yet](https://proceedings.iclr.cc/paper_files/paper/2024/hash/8b4add8b0aa8749d80a34ca5d941c355-Abstract-Conference.html)
- [CRITIC](https://proceedings.iclr.cc/paper_files/paper/2024/hash/fef126561bbf9d4467dbb8d27334b8fe-Abstract-Conference.html)
- [LLMs Can Self-Correct with Key Condition Verification](https://aclanthology.org/2024.emnlp-main.714/)
- [MT-Bench LLM-as-a-Judge study](https://papers.neurips.cc/paper_files/paper/2023/hash/91f18a1287b398d378ef22505bf41832-Abstract-Datasets_and_Benchmarks.html)
- [NASA software IV&V guidance](https://swehb.nasa.gov/spaces/SWEHBVB/pages/32604595/SWE-141%2B-%2BSoftware%2BIndependent%2BVerification%2Band%2BValidation)
- [NASA Systems Engineering Handbook](https://ntrs.nasa.gov/citations/20170001761)
- [NIST Guide to the SI](https://www.nist.gov/pml/special-publication-811)
- [JCGM measurement-uncertainty publications](https://www.bipm.org/en/committees/jc/jcgm/publications)
- [OWASP Agentic AI threats](https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/)
- [OWASP memory poisoning](https://genai.owasp.org/2026/05/13/memory-is-a-feature-it-is-also-an-attack-surface/)
- [Greshake et al., Indirect Prompt Injection](https://arxiv.org/abs/2302.12173)
- [NIST AI 100-2 E2025 adversarial machine-learning taxonomy](https://doi.org/10.6028/NIST.AI.100-2e2025)
- [Shumailov et al., model collapse under recursively generated data](https://doi.org/10.1038/s41586-024-07566-y)
- [Google SRE Workbook: Canarying Releases](https://sre.google/workbook/canarying-releases/)
- [Argo Rollouts analysis and progressive-delivery contract](https://argo-rollouts.readthedocs.io/en/stable/features/analysis/)
- [Argo Rollouts rollback-window contract](https://argo-rollouts.readthedocs.io/en/latest/features/rollback/)
- [SiLA 2 standards](https://sila-standard.com/standards/)
- [SiLA 2 Part A Overview, Concepts, and Core Specification v1.1](https://sila-standard.com/wp-content/uploads/2022/03/SiLA-2-Part-A-Overview-Concepts-and-Core-Specification-v1.1.pdf)
- [ISO/IEC 17025:2017](https://www.iso.org/standard/66912.html)
- [ISO 13850:2015](https://www.iso.org/standard/59970.html)
- [ISA-88 standards](https://www.isa.org/standards-and-publications/isa-standards/isa-88-standards)
- [FMI 3.0 specification](https://fmi-standard.org/docs/3.0/)
- [Allotrope documentation](https://docs.allotrope.org/)
- [Allotrope Data Format](https://docs.allotrope.org/Allotrope%20Data%20Format.html)
- [AnIML analytical data standard](https://new.animl.org/)
- [W3C PROV overview](https://www.w3.org/TR/prov-overview/)
- [Burger et al., A mobile robotic chemist](https://www.nature.com/articles/s41586-020-2442-2)
- [MacLeod et al., Self-driving laboratory for thin-film materials](https://pubmed.ncbi.nlm.nih.gov/32426501/)
- [Wang et al., Closed-loop microfluidic manipulation](https://pubmed.ncbi.nlm.nih.gov/34008660/)
- [Closed-loop capacitive fluid-height sensing](https://pmc.ncbi.nlm.nih.gov/articles/PMC9011357/)
- [NIST SP 800-61 Rev. 3 announcement and publication](https://www.nist.gov/news-events/news/2025/04/nist-revises-sp-800-61-incident-response-recommendations-and-considerations)
- [NIST SP 800-86](https://csrc.nist.gov/pubs/sp/800/86/final)
- [NIST SP 800-115](https://csrc.nist.gov/pubs/sp/800/115/final)
- [RFC 3227 Guidelines for Evidence Collection and Archiving](https://www.rfc-editor.org/info/rfc3227/)
- [RFC 6973 Privacy Considerations for Internet Protocols](https://www.rfc-editor.org/info/rfc6973/)
- [RFC 7011 IPFIX](https://www.rfc-editor.org/info/rfc7011/)
- [OASIS CACAO Security Playbooks 2.0](https://docs.oasis-open.org/cacao/security-playbooks/v2.0/security-playbooks-v2.0.html)
- [OpenTelemetry Logs Data Model](https://opentelemetry.io/docs/specs/otel/logs/data-model/)
- [systemd Journal File Format](https://systemd.io/JOURNAL_FILE_FORMAT/)
- [`journalctl` documentation](https://www.freedesktop.org/software/systemd/man/255/journalctl.html)
- [Linux namespaces](https://man7.org/linux/man-pages/man7/namespaces.7.html)
- [Linux network namespaces](https://www.man7.org/linux/man-pages/man7/network_namespaces.7.html)
- [Linux Audit userspace](https://github.com/linux-audit/audit-userspace)
- [Zeek capture-loss guidance](https://docs.zeek.org/en/current/reference/logs/capture-loss-and-reporter.html)
- [Suricata EVE JSON](https://docs.suricata.io/en/suricata-8.0.0/output/eve/eve-json-format.html)
- [Expert expansion domain appendix](../research/EXPERT_EXPANSION_RESEARCH_2026-07-29.md)

Operational reports and their exact derived regressions are cataloged in the
companion research document.

## 27. Practitioner evidence, ZDD, and rollback

This specification is authoritative for cross-expert envelopes, routing,
handoffs, joins, arbitration, provenance, and conformance. Domain feature specs
remain authoritative for domain semantics and may only add stricter gates.

LangGraph [discussion #744](https://github.com/langchain-ai/langgraph/discussions/744),
opened in 2024, reports a converging node unexpectedly executing twice until the
join edge was expressed differently. AutoGen
[issue #165](https://github.com/microsoft/autogen/issues/165), opened in 2023,
records unbounded chat-history growth and brittle sentinel-style termination.
These long-lived practitioner reports require typed join reducers, idempotency
keys, bounded private context, and machine terminal states; they are regression
signals, never trusted instructions.

Zero-downtime delivery uses additive schema expansion, N/N-1 readers, shadow
execution, signed canary selection, drain/resume, and atomic pointers. Every
phase publishes bounded resource and progress telemetry. A failed security,
correctness, provenance, compatibility, or resource criterion leaves the prior
expert generation authoritative; rollback restores the complete compatible
bundle, revokes candidate leases/capabilities, and emits a residual-state
receipt without destructive migration.
