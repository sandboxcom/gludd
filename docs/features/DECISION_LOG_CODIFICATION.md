# Decision-log codification

**Status:** Core, bounded analysis API/CLI, and automatic durable live REVIEW
integration implemented; signed replay capture and deployed proof pending.

**Presentation contract:** `decision-log-codification-v1`

## Outcome and honest boundary

Decision-log codification turns repeated, verified low- or medium-risk decisions
into immutable JSON rules. A matching rule can answer without a model call. Any
uncertainty produces a typed abstention so the caller can preserve the existing
agent/LLM path.

The standalone implementation is in
`src/general_ludd/decision_codification/`: strict schemas and normalization,
offline similarity/mining/export/evaluation, authenticated artifact storage,
human approval, deterministic runtime lookup, rollout, and closed-cardinality
telemetry all have focused unit tests. `DecisionLogAnalyzer` converts verified
run bundles into evaluated candidates, while `DecisionResolver` executes an
exact codified hit or invokes the supplied agent fallback exactly once after a
typed abstention. An end-to-end core test covers signed evidence, approval,
activation, a zero-call rule hit, fallback, and rollback.
`DecisionCodificationAdapter` binds the verified reader, runtime, project, and
policy either to an explicit daemon/EventLoop injection or to the typed,
default-off `decision_codification` operator configuration. The authenticated,
analysis-only HTTP route and operator CLI expose proposal analysis without
executable artifacts or lifecycle controls. Enabled daemon configuration now
constructs the adapter automatically, shares generation state safely between
same-host workers, and records idempotent terminal application outcomes. No
deployment claim follows from that wiring: automatic signed replay capture and
deployed live-traffic proof remain pending. Avoided-call metrics therefore
remain an integration outcome rather than a deployed claim.

```text
verified replay bundle -> safe envelope -> offline candidate + replay report
                                                   |
                                                   v
                                      exact human-approved digest
                                                   |
                                                   v
                             shadow -> canary -> canary_10 -> canary_50 -> active
                                                   |
                        exact context -------------+---- uncertainty
                             |                               |
                             v                               v
                     zero-LLM rule hit             typed abstention -> agent/LLM
```

## Verified signed-bundle boundary

Training evidence starts only with a signed, complete, schema-supported replay
bundle returned by `RunBundleStore.read_verified()`. That method verifies the
signature, manifest and event digests, sequence, event count, completeness, and
safe run identity before it returns events. Unsigned, legacy, incomplete,
corrupt, held, or cross-project material is not silently downgraded into
training data.

`normalize_verified_decision_event()` additionally requires a
`VerifiedDecisionSourceV1` capability marker that binds the project, source run,
and verified bundle digest. It accepts only closed decision-event kinds,
allowlisted fields, a policy digest, a bounded feature vocabulary, and terminal
outcome evidence bound to the same decision-event digest. Unknown fields,
secrets, free text, unsafe risk bands, unredactable content, and ambiguous
outcomes return a content-free refusal.

`DecisionLogAnalyzer` mints that marker internally only after a successful
`read_verified()` result. Callers cannot provide the marker and must never
construct one from an arbitrary log or an unverified bundle. The remaining
recorder integration supplies the signed decision events; it does not weaken
this verified-read boundary.

## Bounded authenticated analysis API and CLI

`POST /api/v1/decision-codification/analyze` exposes proposal-side analysis
through the `analyze_decision_logs` handler. The existing daemon
`auth_and_stats_middleware` remains the outer boundary: authentication and
authorization run before analysis, so a missing or invalid credential never
reaches request analysis or the injected adapter. The request `project_id`, the
authenticated project claim, and the adapter's project binding must agree. The
adapter also retains its immutable policy binding, giving the call an exact
project and policy scope that the request cannot widen.

`DecisionAnalysisRequest` is strict and content bounded:

- the JSON body is at most 64 KiB and contains 1-256 unique safe run IDs;
- project and run identifiers use the existing bounded replay types;
- training-recipe and dependency-lock values are SHA-256 digests;
- creation and expiry are timezone-aware, ordered, and at most 366 days apart;
- maximum uses are 1-1,000,000 and estimated tokens per call are
  0-10,000,000; and
- unknown fields are rejected, including activation, approval, or key material.

`DecisionAnalysisResponse` projects at most 128 candidate summaries. Its
`DecisionCandidateSummary` and `DecisionRejectionSummary` values contain only
digests, counts, and closed rejection enums. They omit project and run IDs,
events, normalized evidence, rules, approval receipts, secrets, and keys.
Validation, scope, and backend failures use bounded generic errors rather than
reflecting input or exception content.

This endpoint does not approve or activate a candidate, mutate a lifecycle
pointer, or expose signing material. The API therefore adds a safe operator
analysis boundary, not autonomous decision execution.

`gludd decision-codification analyze` is the matching proposal-only operator
surface. It requires an explicit `--project`, 1-256 repeated `--run-id` values,
SHA-256 training-recipe and dependency-lock digests, timezone-aware
`--created-at` and `--expires-at` values with a positive lifetime of at most 366
days, `--maximum-use-count` from 1-1,000,000, and
`--estimated-tokens-per-call` from 0-10,000,000. It uses the existing
`GLUDD_AUTH_PSK` project-bound bearer authentication and accepts no credential or
key on the command line. The client refuses redirects, applies a fixed request
timeout, enforces a 128 KiB response cap while streaming, and validates the
strict `DecisionAnalysisResponse` before printing safe summaries only. It has no
lifecycle, key, artifact, or evidence surface.

## Offline learning is proposal-only

Similarity helps discover repeated evidence offline; it never grants runtime
permission. Inputs are project- and policy-partitioned, sorted by canonical
digest, and clustered with bounded complete-link similarity. The learner uses a
small scikit-learn decision tree, repeats training under multiple input orders,
and exports only a strict `DecisionRuleBundleV1`. The estimator itself is never
persisted or loaded in production.

Candidates require conservative support, diversity, confidence, verified
success, and holdout precision. Evaluation uses the exported rule adapter, not
the fitted estimator, and rejects false automation, conflicts, safety
violations, nondeterministic exports, or an insufficient chronological holdout.

## Exact-context abstention

Runtime is intentionally stricter than the offline grouping step:

1. `normalize_decision_context()` uses the same feature-schema digest and closed
   vocabulary as training.
2. Project, decision kind, action vocabulary, operation class, risk band,
   feature schema, policy digest, and an observed context signature must match.
3. The active candidate, receipt chain, expiry, revocation, drift state, use
   limit, and deterministic rollout cohort must all verify.
4. Exactly one non-abstaining leaf must match, and the current safety callback
   must still allow its closed action.

There is no fuzzy search, nearest neighbor, model object, network lookup, free
text, random number, or wall-clock feature in a rule decision. A miss or any
uncertainty returns `DecisionAbstentionV1` with a closed reason such as
`scope_miss`, `normalization_refused`, `policy_changed`, `canary_excluded`,
`integrity_failure`, or `runtime_error`. It never chooses a plausible default.

`DecisionRuntime.lookup()` itself does not call a model. A successful
`CodifiedDecision` is therefore a **zero-LLM hit**: it evaluates through the
existing deterministic `RuleEngine`. `DecisionResolver` owns the other half of
the standalone contract: it invokes the supplied agent/LLM fallback exactly
once for each `DecisionAbstentionV1`, validates the returned action against the
closed vocabulary, and preserves the bounded abstention reason.
`DecisionCodificationAdapter` exposes that contract through an immutable
project/policy binding. The configured live REVIEW path supplies its existing
fallback automatically and records terminal application feedback; other
decision kinds still require an explicit integration owner.

### Opt-in live REVIEW integration

The in-process return-review path now supplies `DecisionKind.REVIEW` to an
adapter provided either by explicit injection or enabled typed configuration.
The adapter remains opt-in and disabled by default; without it, the established
reviewer path is unchanged. With it, an exact REVIEW hit skips the reviewer,
while abstention or adapter error invokes the reviewer exactly once off-loop
through the event loop's bounded worker path.

The only codified REVIEW action mapping is deliberately conservative:
`approve -> complete`, `request_changes -> needs_more_work`, and
`reject -> failed`. Fallback-only reviewer decisions retain their original
`TaskDecision`; when the resolver needs a closed REVIEW action, `blocked`,
`manual_hold`, `ignore_duplicate`, and unknown values are represented as
non-approval `reject`. Invalid codified actions or missing digest attribution
fail closed to the reviewer.

The live path preserves stable idempotency inputs: retries derive stable
correlation and side-effect IDs from the return ID,
`return-review:{return_id}` and `task-decision:{return_id}`. Managed
self-improvement refuses codified resolution during normalization and uses the
existing reviewer/promotion path. Codification-specific audit data keeps
content-free attribution: decision source, candidate and receipt digests, and
closed fallback/normalization reasons only, never result summaries or feature
values.

### Durable same-host worker configuration

`DecisionCodificationConfig` is default-off and rejects incomplete enabled
configuration. A configured project and policy digest are immutable runtime
scope. Replay, artifact, and generation-state paths are explicit. YAML stores
only environment-variable names for replay, artifact-HMAC, and rollout-HMAC
keys; it never stores key material:

```yaml
decision_codification:
  enabled: true
  project_id: project-1
  policy_digest: sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
  replay_root: .gludd/replays
  artifact_root: .gludd/decision-artifacts
  state_path: .gludd/decision-state.sqlite3
  replay_key_id: primary
  replay_key_envs:
    primary: GLUDD_DECISION_REPLAY_KEY
  artifact_key_env: GLUDD_DECISION_ARTIFACT_KEY
  rollout_key_env: GLUDD_DECISION_ROLLOUT_KEY
  busy_timeout_seconds: 10
```

When enabled, `create_daemon_app()` constructs the verified replay reader,
authenticated artifact store, durable generation store, rollout controller,
runtime, and adapter. An explicitly injected adapter still takes precedence.
Missing or short keys, an incomplete scope, an invalid digest, or unusable
storage fails daemon construction with a fixed diagnostic. Disabled
configuration preserves the established reviewer path.

`DurableGenerationStore` uses a versioned SQLite schema in WAL mode. Each
operation opens its own connection; mutations acquire `BEGIN IMMEDIATE`, honor a
bounded busy timeout, and atomically publish compare-and-swap pointers,
revocations, drift holds, use reservations, rollback history, and outcomes.
Independent workers therefore see one committed generation and one idempotent
use count. This contract is for same-host workers sharing a local filesystem;
network filesystems or a multi-host cluster require a future PostgreSQL adapter
and are not claimed here.

### Terminal application feedback

Every codified hit now retains a digest-only application ID derived from the
candidate, normalized context, correlation ID, and side-effect ID. After the
live REVIEW side effect returns, the event loop records `success` or `failure`
off-loop with the candidate digest, closed kind/stage/outcome, stable application
ID, terminal event ID, and decision-receipt digest. It never records result
summaries, feature values, exception text, or user identifiers.

Outcome insertion requires a previously reserved application, is idempotent
across workers, and rejects conflicting retries. The rolling feedback window is
bounded to the newest 100 applications from seven days. One `unsafe` result or
a verified failure rate above 1% durably drift-holds the candidate, so every
worker abstains on its next lookup and invokes the established fallback exactly
once. Feedback-storage failure does not undo an already applied task decision;
it emits only a content-free exception class and leaves deployment proof
pending.

## Immutable human approval

A miner can propose a candidate but cannot activate it. Only explicit human approval
with an authorized identity can create the first receipt. The receipt binds the
exact candidate, corpus, evaluation report, feature schema, policy,
source code, dependency lock, training recipe, project, decision kind, expiry,
risk class, rollout plan, and maximum use count.

`DecisionArtifactStore` uses create-only names and domain-separated HMAC
authentication. `DecisionApprovalService` requires
`ApprovalDecision.APPROVED`, calls the scope authorizer, pseudonymizes the
approver identity with a project-bound HMAC, and fails closed on storage or
authorization errors. Promotion, renewal, revocation, expiry, and rollback are
new linked receipts; they cannot rewrite an earlier approval or widen its
immutable bindings.

## ZDD stages and atomic rollback

The approved rollout is:

```text
shadow (0%) -> canary (1%) -> canary_10 (10%) -> canary_50 (50%) -> active (100%)
```

Every promotion advances exactly one stage in the approved plan. Cohorts are
stable project-scoped HMAC buckets, so restarts and concurrent workers make the
same selection. A compare-and-swap `GenerationPointer` publishes a complete
candidate/receipt pair atomically; workers never observe a partially written
generation. Shadow performs no codified execution, while excluded canary
traffic abstains and preserves fallback.

### Atomic rollback

Rollback verifies its chained receipt, compare-and-swap expectation, expiry,
policy compatibility, and prior artifact integrity. It atomically restores the
newest eligible generation. If none is safe, it removes the pointer so all new
traffic abstains. In-flight work retains the generation that issued its
decision, and rollback does not require a worker restart or mutate immutable
artifacts. Revocation and drift hold also stop new codified hits immediately.

## Long-lived upstream and user findings

The design was shaped by reports that have remained useful across library
versions:

- [RapidFuzz #432](https://github.com/rapidfuzz/RapidFuzz/issues/432) asks for
  all equally best matches rather than one index-tied result. Gludd computes a
  complete bounded matrix, sorts by digest, and tests order invariance instead
  of using `extractOne`.
- [scikit-learn #15629](https://github.com/scikit-learn/scikit-learn/issues/15629)
  has documented since 2019 that a floating-point boundary can change a tree
  prediction after scaling. Gludd permits only one-hot boolean splits at exactly
  `0.5`, exports closed JSON rules, and evaluates that export independently.
- [scikit-learn discussion #25411](https://github.com/scikit-learn/scikit-learn/discussions/25411)
  records user-visible split nondeterminism despite an apparent seed. Gludd
  requires byte-identical exports in digest, reverse, and fixed-permutation
  orders rather than trusting `random_state` alone.
- [OPA #2379](https://github.com/open-policy-agent/opa/issues/2379) documents
  the privacy tradeoff between dropping a whole JWT and retaining replayable
  credentials. Gludd extracts only allowlisted typed facts before persistence;
  credentials and raw source content never enter an envelope.
- [OPA #1514](https://github.com/open-policy-agent/opa/issues/1514) has
  documented since 2019 that time- and network-dependent decisions cannot be
  replayed without their nondeterministic results. Gludd rejects that evidence
  unless the exact bounded fact is present in the verified bundle, and runtime
  never reaches out for a replacement fact.
- [SQLite forum: WAL with multiple processes](https://www.sqlite.org/forum/forumpost/65314c7f1ea8d20d)
  records the long-lived operator question about enabling WAL across reader and
  writer processes. The answer emphasizes persistent WAL state, one writer,
  concurrent readers, and waiting writers; Gludd initializes WAL once and uses
  one short write transaction per mutation.
- [SQLite forum: `BEGIN IMMEDIATE`](https://sqlite.org/forum/forumpost/04ed1d235b)
  demonstrates how a deferred read-to-write upgrade can return `SQLITE_BUSY`
  without honoring the expected wait. Gludd never upgrades a read transaction:
  every mutation begins with `BEGIN IMMEDIATE` and a bounded busy timeout.
- [SQLite forum: hidden WAL checkpoints](https://sqlite.org/forum/forumpost/49178f62e9?t=c)
  reports multi-process WAL stalls caused by close-time checkpoint behavior.
  Gludd opens short-lived connections, bounds lock waiting, surfaces storage
  failure as abstention/feedback failure, and does not claim unbounded progress
  under checkpoint contention.

These findings support deterministic exports, immutable provenance, and
abstention. They do not justify fuzzy runtime matching or autonomous approval.

## Integration and verification

The service now owns verified source-marker construction, bounded analysis,
lookup/fallback orchestration, and end-to-end core ZDD evidence. Its 48-bundle
test demonstrates candidate mining, exact human approval, activation, a
zero-fallback rule hit, an unseen-context fallback, and rollback continuity.
`DecisionCodificationAdapter` additionally supplies explicit injection through
daemon application state and `EventLoop`; integration tests prove the default
is disabled. Live REVIEW tests prove an exact hit makes no reviewer call, every
tested abstention or adapter failure makes exactly one reviewer call off-loop,
managed self-improvement refuses codified resolution, and retry identities and
content-free attribution remain stable. CLI tests cover request construction,
existing authentication, response bounds, safe output, and fixed diagnostics.

The remaining production integration owns automatic signed replay capture,
permissions, multi-host state if required, and deployed live-traffic proof.
Durable same-host configuration and application-outcome feedback are now
implemented and tested. Shared schema and infrastructure changes must still land
once and merge forward.

Focused tests live under `tests/unit/test_decision_codification_*.py`. The
documentation drift test is
`tests/unit/test_decision_log_codification_docs_sync.py`; it pins this guide,
the design status, and the reveal.js contract token. Production changes retain
the repository floors of at least 85% aggregate coverage and at least 75% in
each file, with decision-codification modules targeting 90% branch coverage.
The static coverage-gap gate records both cohesive durable-state implementation
modules through their real facade chain: `durable` imports `durable_feedback`,
which imports `durable_generation`, while the focused durable-state suite pins
that inheritance identity and exercises the public store. This keeps a
behavior-preserving file split from appearing untested without adding either
module to a gap allowlist or weakening executable coverage.
