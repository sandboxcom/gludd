# Decision-log mining and deterministic codification

**Status: CORE IMPLEMENTED; INTEGRATION PENDING**

**Scope:** Mine repeated, successful agent decisions into reviewable, versioned
decision trees that Gludd can execute without an agent/LLM call. This document
specifies the safe evidence boundary, offline learner, approval lifecycle,
runtime lookup, and zero-downtime operation. The standalone core is implemented;
daemon/event-loop activation and durable multiworker integration are not.

## 0. Implementation status (2026-10-06)

The contract, normalization, similarity, mining, export, replay evaluation,
authenticated artifact store, human-approval adapter, deterministic runtime,
ZDD rollout controller, telemetry, and service orchestration now live in
`src/general_ludd/decision_codification/` and
`tests/unit/test_decision_codification_*.py`. `DecisionLogAnalyzer` reads only
verified signed bundles and produces replay-evaluated candidates;
`DecisionResolver` returns an exact codified decision or invokes the supplied
agent fallback exactly once after typed abstention.

The core enforces exact observed-context signatures, typed abstention,
create-only HMAC-authenticated artifacts, digest-bound human approval, stable
canary buckets, atomic generation pointers, and verified rollback. A codified
hit uses the existing deterministic rules engine and performs no model or
network call.

An end-to-end core test feeds 48 signed run bundles through analysis, human
approval, staged activation, an exact zero-fallback hit, an unseen-context
fallback, and atomic rollback. `DecisionLogAnalyzer` alone mints
`VerifiedDecisionSourceV1` after `read_verified()` succeeds.

The single-writer R4 integration remains: recorder emission, daemon/event-loop
invocation and terminal outcome feedback, durable database repositories and
migration, permissions, config, and CLI/API are not yet wired. No production
traffic is claimed to use this core today.

## 1. Outcome and non-goals

The feature turns repeated decisions into deterministic rules while preserving
abstention as the safe default:

1. Read only complete, integrity-verified decision evidence.
2. Convert it to a content-safe normalized decision envelope.
3. Group comparable contexts and synthesize a small candidate decision tree.
4. Evaluate the exported rules against held-out historical evidence.
5. Require immutable human approval of the exact candidate digest.
6. Roll out in shadow and ZDD canary stages.
7. Use deterministic runtime lookup for an exact active scope; use agent/LLM
   fallback for every miss, ambiguity, stale rule, or safety refusal.

This is not autonomous policy invention. It must not codify high-risk actions,
permissions, spend-limit increases, destructive operations, or decisions whose
outcome cannot be verified. It does not learn from raw chat text, explanations,
prompts, secrets, or unverified legacy captures. It never fuzzy-matches at
runtime.

## 2. Existing components to reuse

The implemented core composes repository components, and the remaining
integration must continue doing so rather than introduce a second event,
storage, policy, or rollout stack.

| Need | Existing owner | Reuse decision |
|---|---|---|
| Strict event shape and canonical JSON | `src/general_ludd/replay/schema.py` | Extend the replay event taxonomy through its owner; use the same strict Pydantic and canonical-JSON conventions. |
| Verified source evidence | `src/general_ludd/replay/store.py` | Mine only `RunBundleStore.read_verified()` output. Never scan arbitrary logs or bypass bundle verification. |
| Agent decision capture | `src/general_ludd/replay/recorder.py` | Emit normalized decision-source events at the capture boundary after canonical redaction. |
| Content redaction | `src/general_ludd/security/redaction.py` | Apply before normalization and record counts, never rejected content. |
| Learned procedure lifecycle | `src/general_ludd/memory/procedural.py` | Reuse project scoping, success/failure feedback, and procedural-memory concepts; add a stricter rule artifact rather than placing executable code in free-form `steps`. |
| Deterministic execution | `src/general_ludd/rules/engine.py` | Compile approved tree leaves to its bounded condition/action vocabulary; do not build another runtime policy interpreter. |
| Human workflow | `src/general_ludd/approval/gate.py` | Reuse the human-todo path, but bind approval to an immutable artifact digest and fail closed when persistence is unavailable. |
| Canonical HMAC | `src/general_ludd/integrity/store.py` | Reuse canonical JSON, domain-separated signing, and fail-closed verification for local artifacts and receipts. |
| Gradual rollout | `src/general_ludd/feature_flags/engine.py` | Reuse staged rollout concepts and deterministic entity bucketing; rule activation still needs its own digest-bound generation pointer. |
| Metrics | `src/general_ludd/replay/telemetry.py` | Follow its injected, no-throw backend and closed-label design. |

`rapidfuzz` is a direct dependency and provides bounded similarity with explicit
cutoffs. `scikit-learn` 1.9.0 is now declared directly in the applicable project
dependency sets and locked; the miner does not rely on an accidental transitive
install.

Use `sklearn.cluster.AgglomerativeClustering` with complete linkage over a
precomputed distance matrix and `sklearn.tree.DecisionTreeClassifier` for the
offline proposal only. Runtime consumes strict JSON rules, not a Python model.
Do not add Open Policy Agent, a vector database, another audit service, or a
second rules runtime in v1. Open Policy Agent's decision-log design informs the
privacy and replay contract, but an OPA sidecar would duplicate Gludd's existing
replay and rules infrastructure.

## 3. Data flow and trust boundaries

```text
signed replay bundles
        |
        v
allowlist normalizer --reject--> content-free rejection metric
        |
        v
normalized envelopes -> bounded similarity groups -> candidate tree
        |                                           |
        +---------------- offline replay -----------+
                                                    |
                                                    v
                                      digest-bound human approval
                                                    |
                                                    v
                                  shadow -> 1% -> 10% -> 50% -> 100%
                                                    |
                     exact match -------------------+--- no match/refusal
                         |                                      |
                         v                                      v
               deterministic rule                     agent/LLM fallback
```

The source, proposal, approval, and activation boundaries are separate. A miner
can propose but cannot approve. An approver can approve only an exact digest. A
runtime worker can execute only an active, verified generation. An agent cannot
write directly to the active pointer.

## 4. Normalized decision envelope

`DecisionEnvelopeV1` is frozen, strict, canonical JSON. Unknown fields and
non-finite numbers are rejected. Its maximum canonical size is 16 KiB.

| Field | Contract |
|---|---|
| `schema` | Literal `gludd.decision-envelope/v1`. |
| `envelope_id` | SHA-256 of the canonical envelope without this field. |
| `source_run_id` | Safe replay run ID; provenance only, never a feature. |
| `source_event_digest` | Digest of the verified source event. |
| `source_bundle_digest` | Digest of the verified manifest and ordered events. |
| `project_id` | Existing bounded project identifier; partitions every query. |
| `decision_kind` | Closed enum: `review`, `policy`, `budget`, `routing`, or `reconcile`. |
| `feature_schema` | Digest-addressed allowlist/normalizer version. |
| `policy_digest` | Exact policy or configuration generation used for the decision. |
| `occurred_at` | UTC evidence time; excluded from model features except a coarse age bucket. |
| `exact_guards` | Sorted closed-map fields that must match exactly. |
| `features` | At most 64 sorted, bounded categorical or bucketed numeric values. |
| `decision` | One closed action from the decision-kind registry. |
| `verified_outcome` | `success`, `failure`, `reverted`, `unknown`, or `unsafe`. |
| `outcome_evidence` | Content-free gate/status digests and terminal event IDs. |
| `redaction` | Counts and bounded categories, never source values. |

### 4.1 Allowed features

Each decision kind owns a versioned feature registry. Initial safe candidates
include work type, queue, risk band, resource profile, provider class, operation
class, bounded status, known policy flags, retry bucket, cost bucket, latency
bucket, and whether required evidence exists. Numeric values are converted to
named ranges before persistence. Missing is a first-class value and never means
false.

The following are never features: raw prompt/response/rationale, audit notes,
file content, arbitrary file paths, URLs, hostnames, IP addresses, user names,
email addresses, authorization material, tool output, exception text, free-form
tags, or identifiers supplied by a model. When a stable relationship genuinely
requires an identifier, persist a project-keyed HMAC plus a type prefix, never a
plain hash susceptible to dictionary recovery.

Normalization is allowlist-first, then canonical redaction, then bounds checks.
A field that is unknown, overlong, unredactable, or outside the closed vocabulary
rejects the envelope. It is not silently dropped because omission could make two
materially different contexts appear equal.

### 4.2 Eligible evidence

Only signed, complete, schema-supported bundles returned by
`RunBundleStore.read_verified()` are training evidence. The eligible decision
events are `review.decided`, `policy.decided`, `budget.decided`, and
`reconcile.decided`; a future routing event must be added through the replay
schema owner. Legacy, unsigned, incomplete, corrupt, held, cross-project, and
missing-policy-digest records remain inspectable but are excluded from mining.

A decision counts once per root task/correlation family. Retries, duplicated
events, replayed runs, and child attempts cannot inflate support. A success is
eligible only after a terminal gate or application outcome binds back to the
same decision and source digest. `unknown` is never treated as success.

## 5. Similarity and grouping

Similarity is an offline discovery aid, not an execution permission.

1. Partition by project, decision kind, feature schema, policy compatibility,
   risk band, action vocabulary, and every exact guard.
2. Serialize the remaining features as sorted `key=typed-value` tokens. Do not
   run a natural-language preprocessor.
3. Compute a complete pairwise matrix with RapidFuzz normalized similarity.
   Inputs are sorted by envelope digest. A score below `92.0` is not similar.
4. Use complete-link agglomerative clustering with the precomputed distance.
   Complete linkage prevents a chain of weak neighbors from joining contexts
   whose endpoints are materially different.
5. Re-run with reversed input and a fixed permutation. Cluster membership,
   member ordering, and the canonical cluster digest must be identical or the
   mining run fails closed.

RapidFuzz's documented tie order follows input index, so stable digest ordering
is mandatory. Do not use `extractOne`; a long-lived feature request notes that a
single-result limit can hide equally good matches. Exact envelope signatures are
grouped without fuzzy scoring and always take precedence.

## 6. Minimum support, confidence, and tree synthesis

Default candidate floors are deliberately conservative:

- minimum support: 16 independent eligible envelopes;
- at least eight distinct root tasks, three UTC days, and two source-agent
  identities when identity evidence exists;
- minimum confidence: `0.95` for the majority decision;
- Wilson 95% lower bound at least `0.80`;
- verified success rate at least `0.99` and zero `unsafe` outcomes;
- no decision conflict under identical exact guards;
- only low- or medium-risk, allowlisted, reversible actions.

Per-kind configuration may raise these floors. Lowering them requires its own
human-approved configuration receipt and may never permit high-risk or
irreversible automation.

The offline learner one-hot encodes the closed feature registry with a sorted
vocabulary. It trains `DecisionTreeClassifier` with `splitter="best"`,
`random_state=0`, `max_depth=4`, `max_leaf_nodes=16`, and
`min_samples_leaf=16`. Inputs are boolean/categorical one-hot values, so every
exported split must be exactly the expected `0.5` threshold. Any other threshold
or unsupported operator rejects the candidate.

The estimator is never persisted. Pickle, joblib, cloudpickle, and executable
model formats are forbidden. The exporter walks the fitted tree and emits a
strict `DecisionRuleBundleV1` containing:

- schema, project, kind, feature-schema, policy-compatibility, and risk scope;
- ordered nodes with stable IDs, feature IDs, `eq`/`not_eq` operators, and
  closed values;
- leaf decision, support, confidence, outcome counts, and abstain marker;
- source-corpus, training-recipe, dependency-lock, evaluator-report, and
  candidate digests;
- creation, expiry, and maximum-use bounds;
- a default `abstain` leaf.

The same training corpus is fitted in digest order, reverse order, and a fixed
permutation. All three exports must have the same canonical digest. Tree paths
are then flattened to the existing `Rule` condition vocabulary in
`src/general_ludd/rules/engine.py`. Overlapping paths, unsupported actions,
unreachable leaves, empty conditions, and a non-abstaining default are errors.

This deliberately reuses mature clustering and tree implementations rather than
writing a custom classifier. The strict exporter, safety policy, and digest
contract are Gludd-specific business logic.

## 7. Offline replay evaluation

Evaluation uses the exported rules and the real runtime adapter, not estimator
predictions.

The split is chronological and grouped by root task: oldest 70% training, next
15% validation, newest 15% holdout, with at least five independent holdout root
tasks. If that split is impossible, no candidate is produced. A corpus digest
binds exact membership and ordering.

Each report contains:

- support and diversity counts;
- exact-match and abstention coverage;
- precision and per-leaf confidence;
- false-automation count (wrong deterministic decision instead of abstention);
- conflict, unknown-feature, and policy-mismatch counts;
- verified outcome, rollback, and safety-violation counts;
- expected agent calls and tokens avoided, clearly labeled as estimates;
- results under the historical policy digest and the current compatible policy.

Activation requires at least `0.99` holdout precision, zero false automations,
zero conflicts, zero safety violations, every leaf meeting support/confidence,
and byte-identical evaluation in two fresh processes. Missing nondeterministic
inputs, external facts, or terminal outcomes make a row ineligible rather than
guessing. Offline replay stores only the signed report and bounded counters.

## 8. Immutable human approval and lifecycle

Candidate lifecycle states are:

```text
proposed -> evaluating -> awaiting_approval -> shadow -> canary -> active
                      \-> rejected          \-> revoked/expired/rolled_back
```

The human-todo created through `src/general_ludd/approval/gate.py` shows the
decision kind, safe scope, rules, support, confidence, holdout metrics, expiry,
and digests. It does not reveal source payloads.

An immutable human approval receipt binds:

- candidate, corpus, evaluator-report, feature-schema, policy, source-code,
  dependency-lock, and training-recipe digests;
- project and decision-kind scope;
- approver identity and authorization evidence;
- approval time, expiry, risk class, rollout plan, and maximum use count;
- the previous lifecycle receipt digest.

Approval writes a create-only, HMAC-authenticated canonical record. Updating an
approval is forbidden; renewal, revocation, promotion, rollback, and expiry each
append a new chained receipt. A regenerated candidate has a new digest and
requires new approval. Missing keys, receipts, authorization, or storage cause
abstention. An agent/LLM may propose or explain a candidate but can never approve
it or widen its scope.

The daemon path uses an append-only database repository plus the existing
canonical HMAC primitive; local single-process mode may use `IntegrityStore`.
The active pointer is a compare-and-swap row keyed by project and decision kind.
It references one approved generation and preserves the previous generation for
rollback. Database migrations and shared models land once through the integration
owner, never independently on agent branches.

## 9. Deterministic runtime lookup and fallback

Runtime normalization uses the same feature-schema digest as mining. Lookup is:

1. Resolve the project/kind active pointer and verify its receipt chain and HMAC.
2. Reject expired, revoked, incompatible, over-use, or non-active generations.
3. Build exact guards and bounded features; reject unknown or missing required
   fields.
4. Select the deterministic canary bucket from
   `HMAC(project_rollout_key, correlation_id || candidate_digest)`.
5. Evaluate the exported rule through the existing rules engine.
6. Return a codified decision only when exactly one active leaf matches and all
   current safety policy checks pass.
7. Otherwise return a typed abstention reason and invoke agent/LLM fallback.

No similarity search, clustering, model object, network call, wall clock, random
number, mutable global, or free text participates in the rule decision. The
decision receipt binds input-envelope digest, rule-bundle digest, leaf ID,
result, rollout stage, and downstream side-effect ID. Repeating identical input
against the same generation is idempotent.

Fallback reasons are a closed enum: `no_active_rule`, `scope_miss`,
`normalization_refused`, `no_leaf`, `multiple_leaves`, `policy_changed`,
`expired`, `revoked`, `canary_excluded`, `drift_hold`, `integrity_failure`, and
`runtime_error`. Failures never silently select a default action.

## 10. Drift, expiry, and revocation

Every applied rule receives a terminal outcome when one becomes available.
Drift evaluation uses a bounded rolling window of 100 applications or seven
days, whichever is smaller, and compares current feature/decision distributions
to the approved report. Canary traffic receives 100% shadow agent comparison;
active traffic uses deterministic 1% shadow sampling, with a minimum of one
sample per 24 hours when traffic exists.

The generation enters `drift_hold` and falls back immediately when any of these
occur:

- one safety violation, integrity error, ambiguous match, or policy mismatch;
- verified failure rate above 1%;
- shadow disagreement above 2% after 20 comparisons;
- an unseen-feature rate above 10% over 100 lookups;
- a feature schema, action vocabulary, or incompatible policy digest changes;
- its use count or default 90-day expiry is reached;
- its source corpus is revoked or placed under an incompatible retention hold.

Revocation is an append-only receipt plus an atomic active-pointer removal; it
does not delete evidence. Emergency revocation needs no agent call. Renewal runs
the full mining, offline replay, and human-approval path on fresh evidence.

## 11. ZDD canary and rollback

All rollout changes are atomic generation swaps. Workers retain the last verified
generation until they observe a complete new pointer; they never observe a
partially written artifact.

Stages are `shadow` (0% execute), `canary` (1%), `canary_10`, `canary_50`, and
`active` (100%). Advancement requires the configured observation window, healthy
outcomes, no integrity or safety events, and a human-approved rollout plan. A
promotion receipt records the previous and next generation digests.

Rollback atomically restores the most recent unexpired compatible generation.
If none exists, it removes the pointer and all traffic uses agent/LLM fallback.
In-flight work remains bound to the generation that issued its decision; new
work sees the new pointer. Rollback must not restart workers, interrupt unrelated
tasks, or mutate the immutable candidate. This is the ZDD canary contract.

## 12. Privacy, resource limits, and observability

### 12.1 Privacy and retention

- Project authorization is checked before catalog lookup; no cross-project
  grouping or aggregate is permitted.
- Raw source payloads never enter envelopes, candidates, metrics, or approval
  screens.
- Project-keyed HMAC keys are domain-separated and rotatable. Rotation expires
  affected candidates; old keys remain verification-only for retained receipts.
- Envelopes default to 90-day retention, aggregate reports to 365 days, and
  approval/revocation receipts follow the audit retention or legal hold.
- Export requires the existing replay export capability plus a new
  `decision_codification:export` capability and emits content-safe artifacts.

### 12.2 Hard limits

One mining run is bounded to 10,000 bundles, 100,000 eligible events, 64
features per envelope, 128 values per feature, 16 KiB per envelope, 128
candidates, depth 4, 16 leaves, 31 nodes, 60 CPU seconds, and 512 MiB working
memory. Pairwise similarity partitions larger than 5,000 rows are split by exact
signature or refused; no unbounded quadratic matrix is allocated. The job is
namespaced, observable, cancellable, and never runs in the request path.

### 12.3 Closed-cardinality metrics

- `gludd_decision_codification_envelopes_total{kind,outcome}`
- `gludd_decision_codification_candidates_total{kind,outcome,reason}`
- `gludd_decision_codification_lookup_total{kind,result,stage}`
- `gludd_decision_codification_fallback_total{kind,reason}`
- `gludd_decision_codification_applications_total{kind,outcome,stage}`
- `gludd_decision_codification_drift_total{kind,reason}`
- `gludd_decision_codification_active_rules{kind,stage}`
- `gludd_decision_codification_mining_seconds{outcome}`
- `gludd_decision_codification_estimated_calls_avoided_total{kind}`

Labels never contain project IDs, rule IDs, digests, paths, models, users, or
free-form reasons. Structured logs may carry a bounded correlation digest but no
source content. Dashboards show support, abstention, precision, fallback,
failures, drift holds, current stage, expiry, and estimated avoided calls.

## 13. Failure policy

| Failure | Required behavior |
|---|---|
| Replay verification or schema failure | Exclude evidence; emit a bounded reason. |
| Redaction/normalization uncertainty | Reject the envelope. |
| Similarity or learner nondeterminism | Reject the entire mining run. |
| Insufficient support/confidence | Keep agent/LLM fallback; no approval request. |
| Evaluation regression | Reject candidate and preserve current generation. |
| Approval storage/auth failure | Remain awaiting approval; never activate. |
| Rule artifact/HMAC failure | Remove from service and fall back. |
| Multiple matching leaves | Record conflict, drift-hold generation, and fall back. |
| Metrics outage | Continue safe decisions; buffer bounded content-free counters. |
| Outcome sink outage | Stop promotion; existing stage may continue only until its bound. |
| Rollback failure | Remove active pointer and fall back. |

## 14. Testing and coverage

Tests follow TDD and include:

- schema/property tests for strictness, bounds, canonical digests, duplicate
  keys, non-finite numbers, identifier safety, and unknown-field rejection;
- redaction and cross-tenant adversarial tests with tokens, paths, PII, prompt
  injections, hostile Unicode, and HMAC rotation;
- golden grouping tests, stable tie tests, reversed/permuted input invariance,
  resource-limit tests, and complete-link anti-chaining tests;
- exported-tree tests proving depth/node/leaf limits, `0.5` thresholds,
  abstaining defaults, one matching leaf, and no executable serialization;
- chronological/root-task leakage tests and offline replay mutation tests;
- immutable approval, chained receipt, authorization, expiry, renewal,
  revocation, and tamper tests;
- exact lookup/fallback/idempotency tests for every refusal reason;
- multiworker atomic pointer, canary bucketing, in-flight generation binding,
  ZDD promotion, and rollback tests;
- metrics cardinality and privacy tests;
- end-to-end evidence showing repeated decisions move from model fallback to
  approved rule execution, then safely roll back without task interruption.

Changed production files require at least 75% individual coverage and the
combined suite remains at least 85%. New decision-codification modules target
90% branch coverage. Gate evidence must include collection, lint, strict typing,
unit, integration, ZDD, replay, privacy, and coverage phases.

## 15. Acceptance criteria

- **DLC-AC-01:** Only signed, complete, project-authorized replay evidence can
  produce an envelope.
- **DLC-AC-02:** Normalization never persists raw prompt, output, rationale,
  secret, PII, arbitrary identifier, or path content.
- **DLC-AC-03:** Stable input permutations yield byte-identical groups,
  candidates, reports, and digests.
- **DLC-AC-04:** Every candidate meets minimum support, minimum confidence,
  diversity, outcome, risk, and bounded-tree floors.
- **DLC-AC-05:** Offline replay on a leakage-free holdout reaches 99% precision
  with zero false automation, conflict, or safety violations.
- **DLC-AC-06:** The exact candidate digest has an immutable human approval
  receipt before any non-shadow execution.
- **DLC-AC-07:** Deterministic runtime lookup has no model/network dependency
  and returns a typed abstention for all uncertainty.
- **DLC-AC-08:** Agent/LLM fallback remains available before, during, and after
  rollout and on every integrity, drift, expiry, or revocation event.
- **DLC-AC-09:** Canary cohorts are stable, observable, project-scoped, and
  advance only through approved stages.
- **DLC-AC-10:** Rollback is atomic, restores a verified compatible generation
  or disables automation, and interrupts no unrelated task.
- **DLC-AC-11:** Metrics and logs have closed cardinality and contain no source
  content or tenant-identifying labels.
- **DLC-AC-12:** Full tests prove at least 85% aggregate and 75% per-file
  coverage, ZDD behavior, privacy, deterministic replay, and safe fallback.

## 16. Landing record and remaining ownership

Each slice lands once on a feature branch from `development`, passes its focused
tests, and is merged forward. Shared files have one integration owner.

### Slice R1: contracts and safe normalization (landed)

`src/general_ludd/decision_codification/schema.py` and `normalize.py` now provide
strict envelope/rule/report/receipt contracts, feature registries, size bounds,
content-safe extraction, and their focused tests. The learner is directly
declared and locked.

### Coding agent A: mining and offline evaluation (landed)

Own only:

- `src/general_ludd/decision_codification/similarity.py`
- `src/general_ludd/decision_codification/miner.py`
- `src/general_ludd/decision_codification/evaluate.py`
- `src/general_ludd/decision_codification/export.py`
- `tests/unit/test_decision_codification_similarity.py`
- `tests/unit/test_decision_codification_miner.py`
- `tests/unit/test_decision_codification_evaluate.py`
- `tests/unit/test_decision_codification_export.py`

These paths now deliver grouping, deterministic learner/export, corpus
digesting, offline replay, and focused tests without runtime integration.

### Coding agent B: approval, runtime, and ZDD lifecycle (landed)

Own only:

- `src/general_ludd/decision_codification/artifact_store.py`
- `src/general_ludd/decision_codification/approval.py`
- `src/general_ludd/decision_codification/runtime.py`
- `src/general_ludd/decision_codification/rollout.py`
- `src/general_ludd/decision_codification/telemetry.py`
- `tests/unit/test_decision_codification_artifact_store.py`
- `tests/unit/test_decision_codification_approval.py`
- `tests/unit/test_decision_codification_runtime.py`
- `tests/unit/test_decision_codification_rollout.py`
- `tests/unit/test_decision_codification_telemetry.py`

These paths now deliver create-only receipt storage, exact lookup and typed
abstention, closed metrics, canary state transitions, expiry/revocation, and
rollback with focused tests.

**Disjoint ownership record:** Both coding slices used the merged R1 contract.
Their production and test paths above do not overlap. The runtime consumes R1's
schemas; neither slice used shared integration files.

### Slice R3: verified-log service and fallback orchestration (landed)

`src/general_ludd/decision_codification/service.py` now supplies
`DecisionLogAnalyzer` and `DecisionResolver`. The service verifies signed
project-scoped bundles, enforces 10,000-bundle and 100,000-event bounds, mines
and evaluates candidates, chooses exact rules without a model call, and invokes
the supplied agent fallback exactly once on abstention. Its end-to-end test
covers approval, activation, a zero-fallback hit, a fallback miss, and rollback.

### Slice R4: single-writer production integration (remaining)

One integration owner alone edits shared surfaces: replay capture,
daemon/event-loop invocation and outcome feedback, database
models/repositories/migration, permissions, config, CLI/API, and make contracts.
This slice adds production integration and live-traffic ZDD evidence. No second
branch independently creates the migration, config keys, make targets, or
daemon wiring.

## 17. Primary documentation and user/forum findings

Research performed 2026-10-05:

- [RapidFuzz process documentation](https://rapidfuzz.github.io/RapidFuzz/Usage/process.html)
  defines normalized cutoffs and says equal-score results follow input order.
  Gludd therefore sorts by digest and tests permutation invariance.
- [RapidFuzz issue 432](https://github.com/rapidfuzz/RapidFuzz/issues/432)
  requests an API that returns all equally best matches. Gludd avoids
  single-result selection and computes the complete bounded matrix.
- [scikit-learn DecisionTreeClassifier documentation](https://scikit-learn.org/stable/modules/generated/sklearn.tree.DecisionTreeClassifier.html)
  notes that equal-quality splits need a fixed `random_state`. Gludd fixes it,
  constrains inputs, repeats training, and rejects digest changes.
- [scikit-learn model persistence guidance](https://scikit-learn.org/stable/model_persistence.html)
  warns that pickle-family loads can execute code and cross-version loading is
  unsupported. Gludd persists only its strict canonical rule schema.
- [scikit-learn issue 15629](https://github.com/scikit-learn/scikit-learn/issues/15629)
  has remained open since 2019 and reports a floating-point boundary changing a
  tree prediction after scaling. Gludd permits only boolean one-hot splits at
  exactly `0.5`, then evaluates the exported rules independently.
- [scikit-learn discussion 25411](https://github.com/scikit-learn/scikit-learn/discussions/25411)
  records user-visible nondeterminism from split tie-breaking even with an
  apparent seed. Gludd requires three order-invariance exports rather than
  trusting a seed alone.
- [Open Policy Agent decision-log documentation](https://www.openpolicyagent.org/docs/management-decision-logs)
  demonstrates decision IDs, masking/drop rules, bounded upload behavior, and
  nondeterministic builtin capture. Gludd adopts those evidence properties
  without adding an OPA service.
- [OPA issue 2379](https://github.com/open-policy-agent/opa/issues/2379) has
  documented since 2020 that dropping a whole JWT loses useful context while
  retaining it exposes replayable credentials. Gludd extracts an allowlisted,
  typed value before persistence and never stores the credential.
- [OPA issue 1514](https://github.com/open-policy-agent/opa/issues/1514) has
  documented since 2019 that decisions depending on time or external calls
  cannot be replayed without captured nondeterministic results. Gludd excludes
  such evidence unless the verified bundle contains the exact bounded facts.

These findings favor a small offline learner, strict exported rules, immutable
provenance, and pervasive abstention over model-object serving or autonomous
policy activation.
