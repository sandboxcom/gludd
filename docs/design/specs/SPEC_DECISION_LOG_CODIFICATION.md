# Decision-log mining and deterministic codification

**Status: CORE, ANALYSIS API/CLI, BOUNDED OPERATOR LIFECYCLE CLI, AUTOMATIC
DURABLE LIVE REVIEW, AND SIGNED AGENT-OUTCOME CAPTURE IMPLEMENTED; DEPLOYED
PROOF PENDING**

**Scope:** Mine repeated, successful agent decisions into reviewable, versioned
decision trees that Gludd can execute without an agent/LLM call. This document
specifies the safe evidence boundary, offline learner, authenticated analysis
API, bounded operator CLI, approval lifecycle, runtime lookup, and zero-downtime
operation. The standalone core, bounded analysis and operator surfaces, and
configured live REVIEW decision point are implemented with durable same-host
multiworker state and automatic signed fallback-outcome capture; deployed proof
is not.

## 0. Implementation status (2026-10-07)

The earlier checkpoint was **CORE, ANALYSIS API, CLI, AND OPT-IN LIVE REVIEW
IMPLEMENTED; DURABLE INTEGRATION PENDING**. It is retained as status lineage,
not as the current claim. The status above supersedes it: same-host SQLite WAL
generation durability, signed agent-outcome capture, shared-PostgreSQL capture
coordination, and bounded operator lifecycle commands are implemented, while
multi-host generation state and deployed proof remain pending.

The contract, normalization, similarity, mining, export, replay evaluation,
authenticated artifact store, human-approval adapter, deterministic runtime,
ZDD rollout controller, telemetry, and service orchestration now live in
`src/general_ludd/decision_codification/` and
`tests/unit/test_decision_codification_*.py`. `DecisionLogAnalyzer` reads only
verified signed bundles and produces replay-evaluated candidates;
`DecisionResolver` returns an exact codified decision or invokes the supplied
agent fallback exactly once after typed abstention. `DecisionCodificationAdapter`
binds those capabilities to one immutable project/policy scope and is available
through explicit injection or typed default-off daemon configuration.
`POST /api/v1/decision-codification/analyze` now exposes that adapter through a
bounded authenticated, analysis-only HTTP surface, and
`gludd decision-codification analyze` provides its bounded remote client. Local
`capture`, `mine`, `approve`, `activate`, and `rollback` commands compose the
existing durable services without duplicating lifecycle rules. The in-process
return-review path resolves `DecisionKind.REVIEW` through that adapter.

The core enforces exact observed-context signatures, typed abstention,
create-only HMAC-authenticated artifacts, digest-bound human approval, stable
canary buckets, atomic generation pointers, and verified rollback. A codified
hit uses the existing deterministic rules engine and performs no model or
network call.

An end-to-end core test feeds 48 signed run bundles through analysis, human
approval, staged activation, an exact zero-fallback hit, an unseen-context
fallback, and atomic rollback. `DecisionLogAnalyzer` alone mints
`VerifiedDecisionSourceV1` after `read_verified()` succeeds.

The adapter is opt-in and disabled by default. Live REVIEW coverage proves that
exact active rules skip the reviewer while abstention and adapter failures call
it exactly once off-loop. A versioned SQLite WAL repository now shares atomic
pointer, use, rollback, revocation, drift, and application-outcome state between
same-host workers. Terminal REVIEW application feedback is idempotent and can
place a generation on immediate durable drift hold. Eligible agent fallback
outcomes can now be finalized automatically as signed two-event bundles. Their
producers share one existing database lease across hosts; multi-host state and
deployed live-traffic proof remain pending for generation serving. No production
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
| Agent decision capture | `src/general_ludd/decision_codification/capture.py`, `src/general_ludd/replay/recorder.py`, and `RunBundleStore` | Reuse the recorder's bounded capture/finalization conventions, pre-normalize, HMAC-correlate, append the decision/outcome pair, and finalize through the existing signed store. |
| Cross-host capture coordination | `src/general_ludd/event_loop/lease.py` and `BucketLeaseModel` | Reuse the unique database lease, bounded expiry, random holder identity, and exact-owner release; do not add a second lock table or pass a session into the worker thread. |
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

### 3.1 Bounded authenticated analysis API and CLI

`POST /api/v1/decision-codification/analyze` calls `analyze_decision_logs` and
accepts `DecisionAnalysisRequest`; successful calls return
`DecisionAnalysisResponse`. The existing daemon `auth_and_stats_middleware`
remains outside the router, so authentication and authorization run before
analysis. The route then requires equality among the request `project_id`, the
authenticated project claim, and the injected adapter's project. The adapter's
immutable policy binding preserves an exact project and policy scope; callers
cannot select or widen the policy through the wire contract. Scope mismatches
return a generic not-found response and never call the analyzer.

The request permits 1-256 unique safe run IDs in a body of at most 64 KiB. It
also requires bounded project identity, SHA-256 training-recipe and dependency-
lock digests, timezone-aware creation and expiry with a positive lifetime of no
more than 366 days, 1-1,000,000 maximum uses, and an estimated 0-10,000,000
tokens per call. Strict unknown-field rejection excludes activation, approval,
credential, and key fields. The route passes only validated values to the
already bounded `DecisionLogAnalyzer` worker thread.

The response contains at most 128 candidate summaries. The safe projections
`DecisionCandidateSummary` and `DecisionRejectionSummary` contain only digests,
counts, and closed rejection enums. Project IDs, run IDs, events, normalized
evidence, exported rules, receipt bodies, credentials, and exception text are
not serialized. Validation and analysis errors are generic and content-free.

`gludd decision-codification analyze` constructs that exact request. It requires
an explicit `--project`, 1-256 repeated `--run-id` values, SHA-256
training-recipe and dependency-lock digests, timezone-aware `--created-at` and
`--expires-at` values with a positive lifetime of at most 366 days,
`--maximum-use-count` from 1-1,000,000, and
`--estimated-tokens-per-call` from 0-10,000,000. The command reuses
`GLUDD_AUTH_PSK` for existing project-bound daemon authentication, refuses
redirects, and applies a fixed request timeout. It enforces a 128 KiB response
cap while streaming, validates `DecisionAnalysisResponse`, and prints safe
summaries only.

The API does not approve or activate candidates, write lifecycle receipts, or
move an active pointer; the remote `analyze` CLI likewise exposes analysis only.
That subcommand accepts no credential or key argument and has no lifecycle, key,
artifact, or evidence surface.

### 3.2 Bounded local lifecycle CLI

The local commands load one enabled `DecisionCodificationConfig` from a regular,
non-symlink YAML file of at most 128 KiB. The supplied `--project` and
`--policy-digest` must equal its immutable bindings before any replay, artifact,
or pointer operation. The shared construction function returns the same
`RunBundleStore`, `DecisionArtifactStore`, `DurableGenerationStore`,
`RolloutController`, and `DecisionCodificationAdapter` used by the daemon. YAML
contains only environment-variable names for signing keys. Key values never
become CLI arguments, JSON output, or exception diagnostics.

The commands preserve separation of authority:

1. `capture` only verifies and summarizes an existing automatically captured
   signed bundle. It cannot accept event payloads or produce manual evidence.
2. `mine` reuses `DecisionAnalysisRequest`, the adapter, the 128-candidate output
   cap, and create-only authenticated artifact storage. Exact reruns are
   idempotent; conflicting content under a digest is refused.
3. `approve` loads an exact candidate and holdout report, requires project,
   policy, kind, source, authorization-evidence, expiry, use, rollout-plan, and
   confirmation bindings, and delegates to `DecisionApprovalService`. The
   supplied identity must equal `GLUDD_DECISION_APPROVER_ID`; storage retains
   only its project-scoped HMAC. The resulting generation is installed only in
   shadow.
4. `activate` takes expected candidate and current receipt digests, appends an
   authorized promotion, and delegates to `RolloutController.promote`. That
   controller permits exactly one next approved stage in the immutable plan, so the CLI
   cannot skip an intermediate canary or replace a concurrently changed head.
5. `rollback` uses the same stale-head guards, appends a rollback receipt, and
   delegates to `RolloutController.rollback`. One durable CAS restores the newest
   compatible generation from verified history or removes the pointer so all new
   traffic abstains.

Outputs are strict digest/count/enum/stage/epoch projections. Inputs contain no
prompt, response, rationale, feature map, rule body, credential value, or raw
evidence. Errors collapse to fixed content-free diagnostics. Existing bounds on
run IDs, events, candidates, five rollout stages, 256 receipt links, capture
quota/scan count, use count, and durable lock timeout bound time, memory, disk,
and contention.

ZDD rollout deploys the default-off command surface first, enables one exact
scope, mines and approves into shadow, then advances one observed stage per
command. Rollback names the exact current head and never requires a worker
restart. If compatible history cannot verify, removing the pointer preserves the
agent/LLM fallback. Operational rollback disables or removes the configuration
after in-flight commands finish; immutable artifacts remain audit evidence and
cannot reactivate themselves.

### 3.3 Durable reuse-observability receipt

The configured adapter injects `DecisionReuseObservability` at the real
`DecisionResolver` seam. One resolution writes exactly one aggregate update:
exact-rule hits increment avoided agent/LLM calls, while typed abstentions each
increment exactly one of the fallback calls and one closed reason counter. Integer
microsecond count/sum/max fields retain latency; candidate changes increment
rule-version changes, and policy, integrity, ambiguity, or active-hold reasons
increment drift events. The call accepts no prompts, context, decisions,
correlation IDs, features, model output, or free-form labels.

`DurableGenerationStore` reuses its WAL, bounded busy timeout, and `BEGIN
IMMEDIATE` writer boundary. A singleton row binds the file to the exact project
and policy. Fixed-cardinality tables contain at most five kind totals and the
Cartesian subset of five kinds by the twelve closed fallback reasons; there is
no per-request retention. Counters are bounded signed 64-bit integers and an
individual latency is capped at 300 seconds before persistence.

`gludd decision-codification status` reloads and validates every aggregate,
then verifies any current rule bundle and lifecycle receipt through
`RolloutController`. Its five sorted summaries expose only counts, integer
latency, closed reasons, candidate/receipt digests, stage, epoch, and drift-hold
state. The canonical receipt is at most 64 KiB, SHA-256 digest-bound, and
HMAC-authenticated with `DecisionArtifactStore`; an altered scope, count,
version, digest, or tag fails verification.

Metrics remain the existing closed-label, no-throw secondary signal. A durable
write, timer, or metrics failure is swallowed only after preserving the actual
decision or single fallback, while status fails closed and never fabricates a
receipt. Rollout is default-off. ZDD enablement deploys schema and readers first,
then enables one exact binding; rollback removes the observer/config binding and
leaves aggregate rows inert without changing deterministic rollback or the
agent/LLM fallback.

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

New live evidence separates the immutable decision from terminal proof. A
decision event contains policy, bounded features, and the closed action. A later
`decision.outcome` event in the same signed bundle binds the store-computed
decision-event digest to one closed outcome plus bounded terminal IDs and
gate/status digests. This avoids a self-referential digest in the decision
payload. The analyzer accepts exactly one same-project, same-correlation,
non-backdated outcome link. Missing, duplicate, conflicting, or orphan links are
content-free refusals, never partial evidence. The global 100,000-event analysis
ceiling also bounds this join.

`DecisionOutcomeRecorder` is enabled only when typed `capture_identity`
configuration supplies exact source/runtime/model identity. It accepts no model
request parameters and
persists no raw capture/task identifier: both become domain-separated HMAC
correlation fields. Under one cross-process capture lock it proves the bounded
retention quota, appends the decision, appends its outcome link, and finalizes the
manifest with the configured replay signature. The identity is idempotent only
for exact payload/correlation equality; reuse with changed content is a conflict.
An incomplete or unsigned bundle is never repaired into training evidence.

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

### 9.1 Opt-in live REVIEW integration

The in-process return-review decision point supplies `DecisionKind.REVIEW` only
when `DecisionCodificationAdapter` was explicitly injected. The adapter remains
opt-in and disabled by default; otherwise the established reviewer path is
unchanged. An exact REVIEW hit skips the reviewer. Any abstention or adapter
error invokes the reviewer exactly once off-loop through the event loop's
bounded worker execution.

Only three codified actions map into `TaskDecision`:
`approve -> complete`, `request_changes -> needs_more_work`, and
`reject -> failed`. The resolver represents fallback-only `blocked`,
`manual_hold`, `ignore_duplicate`, and unknown reviewer decisions as the
conservative non-approval action `reject`, while returning the reviewer's
original `TaskDecision` unchanged. An invalid codified action or missing digest
attribution fails closed to review.

The path keeps stable idempotency inputs: the return ID derives stable
correlation and side-effect IDs, `return-review:{return_id}` and
`task-decision:{return_id}`. The bounded feature projection never infers risk,
and managed self-improvement refuses codified resolution during normalization.
Audit records add content-free attribution only: resolution source, candidate
and decision-receipt digests, and closed fallback or normalization reasons,
never return summaries or feature values.

### 9.2 Automatic configuration and durable worker state

`DecisionCodificationConfig` is strict, immutable, default-off, and secret
indirect. An enabled block requires one bounded project ID, exact policy digest,
replay/artifact/state paths, active replay key ID, a replay-key-ID to environment
name map, artifact-key environment name, rollout-key environment name, and a
bounded busy timeout. Key bytes never appear in YAML or the model dump.

At daemon construction, enabled configuration builds `RunBundleStore`,
`DecisionArtifactStore`, `DurableGenerationStore`, `RolloutController`,
`DecisionRuntime`, and `DecisionCodificationAdapter`. Explicit test/operator
injection takes precedence. Missing secrets or invalid/unusable state fail closed
before the event loop starts; disabled configuration leaves behavior unchanged.

`DurableGenerationStore` is a versioned SQLite database in WAL mode for
same-host workers. Readers use independent connections. Writers use short
`BEGIN IMMEDIATE` transactions and a bounded busy timeout. CAS epochs, current
pointers, rollback history, revocation, drift holds, issued application IDs,
maximum-use enforcement, and outcomes are committed in the same database, so a
second process cannot observe a process-local generation or overrun the use
bound. Network-filesystem and multi-host safety are explicitly out of scope
until a PostgreSQL implementation lands.

### 9.3 Cross-host producer coordination

Automatic REVIEW capture derives one opaque HMAC-derived lease key from the
private capture/task scope. Before offloading replay I/O, the event loop acquires
the existing unique database lease in `bucket_leases` with a fresh content-free
holder and a 60-second recovery lease. It holds that claim transaction through
the storage operation and performs an exact-owner release afterward. On a shared
PostgreSQL application database, the unique index makes a competing host wait or
fail before writing; after release, a retry can acquire and the deterministic
recorder returns the exact prior receipt. A busy, lost, malformed, or unavailable
lease fails closed without changing the already chosen task outcome.

Every participating host must also use the same shared replay root with atomic
publication visibility. The database lease serializes writers; it does not merge
host-local replay copies. A deployment without both shared boundaries may not
claim cross-host capture coordination.

The `AsyncSession` is used sequentially for acquire and release on the event-loop
task. It is never passed to the worker thread or used concurrently. The existing
SQLite deployment retains same-host behavior; only deployments sharing
PostgreSQL may claim cross-host capture coordination. Generation pointers remain
on the separately documented same-host store.

### 9.4 Hermetic producer-to-reuse proof

The integration proof drives 32 bounded, successful fallback reviews through
the real `EventLoop`, database lease, `DecisionOutcomeRecorder`, signed replay
store, analyzer, approval service, rollout controller, and runtime. It spreads
independent task evidence across four UTC days, verifies every signed two-event
bundle, activates only the exact human-approved digest, and proves that one
equivalent live request makes no new reviewer call. Raw return/task identifiers
are absent from serialized evidence. The fixture caps replay storage at 4 MiB,
limits retention scanning to 64 entries, and uses no network or model process.

The same proof makes conflict, context mismatch, receipt expiry, and disabled
configuration observable. Conflict preserves the immutable first bundle and the
already applied task result; mismatch and expiry each abstain to exactly one
reviewer call; disabled composition keeps the pre-feature review path. It proves
the local seam, not production deployment. ZDD still deploys readers first,
keeps `capture_identity` absent until shared storage is ready, activates only
after approval, and rolls producers back before readers or generation state.

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

The implemented REVIEW feedback path carries the issued application's canonical
digest through `CodifiedDecision` and `DecisionResolution`. Once the downstream
side effect returns, it writes a strict `DecisionApplicationOutcomeV1` off-loop.
Known outcomes require a terminal event identifier and evidence digest; unknown
outcomes must claim neither. A row is accepted only for a previously reserved
application, identical retries are no-ops, and conflicting retries fail closed.
The newest 100 outcomes from seven days form the bounded feedback window. One
unsafe result or a failure rate above 1% writes a durable drift hold immediately.

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

Replay schema expansion follows the same no-downtime discipline. Deploy readers
that accept `decision.outcome`, then deploy producer code with `capture_identity`
absent, then verify every host shares the migrated PostgreSQL `bucket_leases`
table and the shared replay root with atomic publication visibility before adding
exact identity/retention configuration. Producer rollback comes first: remove
`capture_identity`, allow exact-owner release or the bounded 60-second recovery
lease, finalize or quarantine in-flight bundles, then remove reader support.
Existing signed bundles are immutable and must never be rewritten
for downgrade compatibility. Capture failures do not change the active generation
or runtime fallback, and the already chosen task outcome is unchanged, so this
migration remains outside the serving decision path.

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

One service mining run is bounded to 10,000 bundles, 100,000 eligible events, 64
features per envelope, 128 values per feature, 16 KiB per envelope, 128
candidates, depth 4, 16 leaves, 31 nodes, 60 CPU seconds, and 512 MiB working
memory. Pairwise similarity partitions larger than 5,000 rows are split by exact
signature or refused; no unbounded quadratic matrix is allocated. The job is
namespaced, observable, cancellable, and never runs in the request path.

The HTTP analysis boundary is narrower: a 64 KiB body, 256 unique safe run IDs,
and 128 candidate summaries. Timestamp lifetime, maximum-use, and token-estimate
parameters retain the bounds in section 3.1. API validation happens before the
worker thread is started.

Producer capture reserves at most 128 KiB per two-event bundle, limits private
identifier inputs to 1,024 UTF-8 bytes, bounds retention to 1-366 days, and scans
at most 1-10,000 store entries. One 60-second database lease with a 49-character
random holder bounds cross-host admission; publication and quota enforcement
share one cross-process store lock. A busy/lost lease, truncated scan, incomplete
size accounting, lock timeout, or unmet quota refuses the new capture rather
than silently dropping older records or writing unsigned evidence.

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
- live REVIEW tests for exact-hit bypass, one off-loop fallback, conservative
  action mapping, managed self-improvement refusal, and content-free attribution;
- CLI tests for bounded request construction, existing authentication, response
  size/schema validation, config-file admission, capture/mining projections,
  exact approval scope, one-stage activation, stale-head refusal, ZDD rollback,
  safe output, and fixed content-free errors;
- multiworker atomic pointer, cross-process use reservation, idempotent outcome
  recording, canary bucketing, in-flight generation binding, ZDD promotion, and
  rollback tests;
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
- **DLC-AC-13:** The analysis API authenticates before work, enforces exact
  request/claim/adapter project equality and the adapter's policy binding, and
  never invokes analysis on a scope mismatch.
- **DLC-AC-14:** API requests and responses remain bounded and content-safe;
  the endpoint exposes no evidence, executable rule, approval, activation, or
  key material.
- **DLC-AC-15:** An injected live REVIEW exact hit skips the reviewer;
  abstention or error invokes it exactly once off-loop, and the default-disabled
  path remains unchanged.
- **DLC-AC-16:** The proposal-only `analyze` CLI preserves API bounds and
  existing auth, enforces its response cap, validates safe summaries, and
  exposes no lifecycle, key, artifact, or evidence input.
- **DLC-AC-17:** When typed decision-codification configuration is enabled, the
  daemon constructs the live adapter automatically from environment-indirected
  secrets and one durable, project-scoped state path; the default remains off.
- **DLC-AC-18:** Exact-hit applications are issued atomically across workers and
  terminal outcomes are idempotent, content-free, and able to place the current
  generation on an immediate safety or bounded failure-rate drift hold.
- **DLC-AC-19:** Local operator commands accept only bounded identifiers,
  digests, closed enums, and a bounded secret-indirect config; require exact
  project/policy/head and explicit human authorization; and delegate immutable
  approval, one-stage promotion, and rollback-or-disable to existing services.
- **DLC-AC-20:** Every configured resolution aggregates one exact hit or one
  typed-abstention fallback in fixed-cardinality durable state, and `status`
  returns a bounded HMAC-authenticated receipt without request content. An
  observability failure cannot alter the decision or fallback count.

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

### Slice R4a: opt-in application binding (landed)

`DecisionCodificationAdapter` combines the verified analyzer and deterministic
resolver under one validated project/policy binding. Daemon application state
and `EventLoop` accept it only through explicit injection; the default remains
`None`. Integration coverage proves zero fallback calls for an exact active
rule, one call per tested abstention, and fail-closed invalid bindings and
unverified evidence.

### Slice R4b: bounded authenticated analysis API (landed)

The daemon now registers `POST /api/v1/decision-codification/analyze` behind its
existing authentication middleware. The strict request delegates to the
injected `DecisionCodificationAdapter`; the response projects only safe digests,
counts, and closed rejection enums. Focused tests cover authentication ordering,
exact project scope, parameter and body bounds, forbidden lifecycle fields,
content-safe errors, and the absence of approval or activation behavior.

### Slice R4c: operator CLI and opt-in live REVIEW (landed)

The remote `analyze` CLI validates the strict request, reuses existing daemon
authentication, bounds and validates the response, and prints safe summaries.
The local `capture`, `mine`, `approve`, `activate`, and `rollback` commands reuse
the configured replay/artifact/approval/rollout services with secret-indirect
configuration, exact scope and stale-head guards, shadow-first one-stage
promotion, digest-only output, and rollback-or-disable ZDD behavior. The
in-process REVIEW decision point uses an explicitly injected adapter off-loop
with exact-hit reviewer bypass, one-call fallback, stable retry identities,
conservative action mapping, managed self-improvement refusal, and content-free
attribution.

### Slice R4d: durable live-flow and producer capture (landed)

Typed default-off configuration now constructs the live adapter automatically,
with secrets resolved only from named environment variables. A versioned SQLite
WAL store provides same-host multiworker compare-and-swap, use reservation,
rollback, revocation, application issuance, and idempotent outcome recording.
The REVIEW path records success or failure after the downstream decision receipt
without changing the already-applied task result; unsafe or excessive-failure
feedback places the generation on a drift hold. Automatic signed replay capture
now writes eligible fallback decisions and terminal outcomes as bounded,
HMAC-correlated, signed two-event bundles. The existing durable execution lease
serializes those producers across hosts sharing PostgreSQL. Multi-host generation
state and deployed live-traffic proof remain. No second branch independently
creates shared migration, config, make, or daemon wiring.

### Slice R4e: durable reuse observability (landed)

`DecisionReuseObservability` records resolution counts and integer latency at
the existing resolver seam. A fixed-row `DurableGenerationStore` extension
serializes worker updates, binds one project/policy scope, and retains no
request-level identifiers or content. The local `status` command verifies the
current generation and returns a 64 KiB-bounded digest- and HMAC-authenticated
receipt. Tests cover shared-worker aggregation, exact hits, typed abstentions,
single fallbacks, avoided calls, version/drift evidence, tamper rejection, and
no-throw serving behavior.

## 17. Primary documentation and user/forum findings

Research performed 2026-10-05 and 2026-10-07:

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
- [OpenTelemetry stable log data model](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/logs/data-model.md)
  separates event name, event/observed time, and execution-context correlation.
  Gludd follows that mature structure with typed decision/outcome events and
  exact HMAC correlation rather than display-log parsing.
- [OpenTelemetry Python issue 4336](https://github.com/open-telemetry/opentelemetry-python/issues/4336)
  records practitioner-observed log loss when a bounded exporter queue discarded
  old records under pressure. Gludd synchronously reserves quota and atomically
  persists or refuses; no in-memory queue can silently become training evidence.
- [Prometheus client_python issue 568](https://github.com/prometheus/client_python/issues/568)
  has remained open since 2020 after operators reported stale multiprocess metric
  files and rising Gunicorn collection CPU. Durable evidence therefore uses the
  existing shared WAL state rather than worker-local metric files.
- [Prometheus client_python issue 431](https://github.com/prometheus/client_python/issues/431)
  records a 2019 deadlock involving inherited thread locks in multiprocess mode.
  Gludd aggregates with short `BEGIN IMMEDIATE` transactions and treats its
  no-throw metrics backend as supplementary, never authoritative evidence.
- [Prometheus instrumentation guidance](https://prometheus.io/docs/practices/instrumentation/)
  calls for query count, failure, and latency signals while advising very low
  label cardinality. The reuse metrics have only closed enum labels; project,
  policy, candidate, receipt, context, and correlation values are excluded.
- [PostgreSQL index uniqueness checks](https://www.postgresql.org/docs/current/index-unique-checks.html)
  define the mature unique-index behavior: a duplicate inserter waits for an
  uncommitted conflicting transaction and then rechecks. The existing
  `bucket_leases.bucket_key` constraint supplies that cross-host serialization.
- [Kubernetes issue 23731](https://github.com/kubernetes/kubernetes/issues/23731)
  has recorded a practitioner-observed lease split brain since 2016, when a
  former leader continued acting. Gludd uses a fresh attempt holder, writes only
  inside the claim, and treats every uncertain/lost claim as no-write.
- [SQLAlchemy discussion 8554](https://github.com/sqlalchemy/sqlalchemy/discussions/8554)
  documents user-visible failures from sharing one `AsyncSession` concurrently.
  Gludd sequences acquire, worker completion, and release on one task and never
  passes the session into the worker.
- [MLflow issue 5133](https://github.com/mlflow/mlflow/issues/5133) records a
  practitioner deployment where an artifact listed a dependency that remained
  absent at serving time. Gludd binds dependency/source digests into approval
  and tests producer evidence through the same runtime seam, while retaining an
  explicit deployed-environment verification requirement.
- [Vault issue 6501](https://github.com/hashicorp/vault/issues/6501) has
  documented since 2019 the confusing precedence and inline exposure created by
  accepting both environment and command-line token material. Gludd resolves
  every key through one configured environment pointer and exposes no credential
  value flag or reflected diagnostic.
- [Kubernetes issue 61897](https://github.com/kubernetes/kubernetes/issues/61897)
  has documented since 2018 that optimistic-lock conflicts are routine when two
  actors update one resource. Gludd requires exact candidate and receipt heads
  for promotion and rollback, then treats every stale CAS as no transition.
- [Helm issue 5377](https://github.com/helm/helm/issues/5377) records atomic
  rollback timeout confusion dating to 2019. Gludd keeps a lifecycle command to
  one bounded durable CAS: rollback either restores verified compatible history
  or disables deterministic reuse while fallback stays available.
- [OPA issue 2379](https://github.com/open-policy-agent/opa/issues/2379) has
  documented since 2020 that dropping a whole JWT loses useful context while
  retaining it exposes replayable credentials. Gludd extracts an allowlisted,
  typed value before persistence and never stores the credential.
- [OPA issue 1514](https://github.com/open-policy-agent/opa/issues/1514) has
  documented since 2019 that decisions depending on time or external calls
  cannot be replayed without captured nondeterministic results. Gludd excludes
  such evidence unless the verified bundle contains the exact bounded facts.
- [SQLite forum: WAL with multiple processes](https://www.sqlite.org/forum/forumpost/65314c7f1ea8d20d)
  confirms the same database can be used by multiple processes in WAL mode, with
  one writer and concurrent readers. Gludd keeps the scope explicitly same-host
  and gives every operation its own connection.
- [SQLite forum: `BEGIN IMMEDIATE` behavior](https://sqlite.org/forum/forumpost/04ed1d235b)
  documents how a deferred read-to-write upgrade can fail with `SQLITE_BUSY`.
  Gludd begins state mutations immediately and uses a bounded busy timeout.
- [SQLite forum: multi-process WAL stalls](https://sqlite.org/forum/forumpost/49178f62e9?t=c)
  reports long-lived operational stalls and close-time checkpoint surprises.
  Gludd bounds lock waits, surfaces fixed content-free failures, and does not
  claim network-filesystem or multi-host safety.

These findings favor a small offline learner, strict exported rules, immutable
provenance, bounded same-host durable state, and pervasive abstention over
model-object serving or autonomous policy activation.
