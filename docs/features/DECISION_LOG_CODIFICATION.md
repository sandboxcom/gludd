# Decision-log codification

**Status:** Core, bounded analysis API/CLI, bounded operator lifecycle CLI,
automatic durable live REVIEW, and signed agent-outcome capture implemented;
deployed proof pending.

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
analysis-only HTTP route and `analyze` command expose proposal analysis without
executable artifacts or lifecycle controls. Local operator commands now compose
the same signed store, immutable approval service, and atomic rollout controller
for bounded capture inspection, mining, approval, activation, and rollback.
Enabled daemon configuration constructs the adapter automatically, shares
generation state safely between same-host workers, records idempotent terminal
application outcomes, and can automatically finalize eligible agent fallback
outcomes as signed replay evidence. No deployment claim follows from that
wiring: deployed live-traffic proof remains pending. Avoided-call metrics
therefore remain an integration outcome rather than a deployed claim.

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
construct one from an arbitrary log or an unverified bundle. Producer capture
uses the same store and cannot mint a verified-source marker; only the later
signed read can do that.

### Durable decision/outcome linking

A live terminal result is recorded as a second `decision.outcome` event in the
same signed bundle. The decision is appended first, giving it an immutable
store-computed digest; the outcome then refers to that digest and carries only a
closed outcome enum, bounded terminal event IDs, and gate/status digests. This
two-event shape avoids a self-referential digest in which an event's payload
would need to contain its own final digest.

Analysis joins only within one verified bundle. It requires same-project,
same-correlation fields, an outcome timestamp no earlier than the decision, and
exactly one outcome per decision digest. Missing, duplicate, conflicting,
cross-correlation, and orphan links become closed rejection counters and never
training rows. The existing 10,000-bundle and 100,000-event analysis limits bound
the in-memory join; the replay store remains the durable source of truth. Legacy
self-contained evidence remains readable during migration, but new capture must
use the non-circular split form.

### Automatic bounded producer capture

When `DecisionCodificationConfig.capture_identity` is present, the configured
adapter constructs `DecisionOutcomeRecorder` with the existing
`RunBundleStore`, active replay-signing key, exact project/policy binding, and
strict source/runtime/model identities. Model request parameters must be empty;
raw prompts, responses, rationale, audit notes, and application errors are never
accepted by this producer. Absence of `capture_identity` preserves the previous
no-write behavior.

After an agent fallback and downstream application attempt, the REVIEW path
passes only the allowlisted feature map, closed action, terminal outcome, and
private capture/task identifiers to the recorder off-loop. The recorder runs the
same pre-decision normalizer before writing anything. It transforms private
identifiers into domain-separated HMAC correlation values, appends the decision,
appends its digest-linked outcome, and finalizes the two-event bundle under the
configured replay signature. A retry with the same identity returns the existing
receipt only when payload and correlation are exact; changed content is a closed
conflict. A crash before finalization leaves incomplete evidence that
`read_verified()` and the analyzer reject.

Before the automatic live path touches replay storage, it derives an opaque
HMAC-derived lease key from the same private capture/task scope and acquires the
existing unique database lease in `bucket_leases`. Each attempt uses a fresh
content-free holder, a 60-second recovery lease, and exact-owner release. The
unique row stays in the event loop's transaction while the storage operation is
awaited, so a second host using the shared PostgreSQL application database must
wait or fail before it can write. Cross-host producers must also use the same
shared replay root with atomic publication visibility; serialization alone does
not reconcile host-local copies. After release, an identical retry may acquire
the key and receives the recorder's existing receipt; changed content still
conflicts. Database work remains sequential on the active `AsyncSession`; only
the replay write runs in the bounded worker thread.

Each capture is limited to a 128 KiB reserved bundle budget. The bounded durable
lease provides cross-host single-writer admission, while a cross-process capture
lock serializes retention and publication on the replay-store host.
`RunBundleStore` must prove the configured byte quota within its 1-10,000 entry
scan bound before a new bundle is written. Retention is 1-366 days and never
silently deletes held, pinned, corrupt, locked, or incomplete evidence. If lease,
quota, signing, locking, or storage is unavailable, capture emits a content-free
warning and the already chosen task outcome is unchanged; no unsigned fallback
evidence is created.

### Hermetic producer-to-reuse proof

`tests/integration/test_decision_codification_producer_reuse.py` drives the real
`EventLoop` review seam through 32 agent fallbacks over four UTC days. Each
fallback acquires the database lease, records a decision plus its terminal
outcome, and finalizes a signed `RunBundleStore` bundle. The proof reads every
bundle back through signature verification, confirms the two-event shape, and
checks that private return and task identifiers are absent from serialized
evidence. Its hermetic budget is fixed at 32 bundles, a 4 MiB replay quota, and a
64-entry retention scan; it uses local SQLite only for the existing lease table
and makes no network or model call.

The same test feeds those producer-created bundles to `DecisionLogAnalyzer`,
requires explicit human approval and atomic activation, then submits an
equivalent live REVIEW. The active rule returns `complete` without another
reviewer interaction. A context mismatch and an expired receipt each invoke the
reviewer exactly once, conflicting reuse leaves the original signed evidence
unchanged while preserving the already applied task outcome, and disabled
configuration retains the original reviewer path without capture attribution.
This is hermetic integration evidence, not deployed live-traffic proof.

## Bounded authenticated analysis API and operator CLI

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

`gludd decision-codification analyze` remains the matching proposal-only operator
surface. It requires an explicit `--project`, 1-256 repeated `--run-id` values,
SHA-256 training-recipe and dependency-lock digests, timezone-aware
`--created-at` and `--expires-at` values with a positive lifetime of at most 366
days, `--maximum-use-count` from 1-1,000,000, and
`--estimated-tokens-per-call` from 0-10,000,000. It uses the existing
`GLUDD_AUTH_PSK` project-bound bearer authentication and accepts no credential or
key on the command line. The client refuses redirects, applies a fixed request
timeout, enforces a 128 KiB response cap while streaming, and validates the
strict `DecisionAnalysisResponse` before printing safe summaries only. That
remote subcommand has no lifecycle, key, artifact, or evidence surface.

The local lifecycle is deliberately split into five bounded commands, all
loaded from one regular, non-symlink YAML file capped at 128 KiB:

- `capture` verifies an existing automatically produced bundle through
  `RunBundleStore.read_verified()` and prints only its safe run/project IDs,
  terminal status, event count, event-index digest, and `signed` integrity. It
  cannot accept or create raw decision content.
- `mine` applies the same analysis request bounds, then create-only persists the
  candidate and validation/holdout reports in `DecisionArtifactStore`. An exact
  authenticated rerun is idempotent; a digest/content conflict fails closed.
- `approve` loads those authenticated digests, requires an exact project,
  policy, decision kind, source digest, external-authorization digest, expiry,
  use limit, monotonic rollout plan, and explicit confirmation. The operator
  identity must exactly match `GLUDD_DECISION_APPROVER_ID`; only its HMAC is
  persisted. Approval uses `DecisionApprovalService` and CAS-installs shadow,
  never active traffic.
- `activate` requires the exact candidate and current receipt digests and asks
  `DecisionApprovalService` plus `RolloutController` to append and apply exactly
  one next approved stage. It cannot skip from shadow past an intermediate
  canary.
- `rollback` requires the same stale-head guards, appends an immutable rollback
  receipt, and atomically restores the newest compatible generation or removes
  the pointer so every new request abstains.

All five require the command's `--project` and `--policy-digest` to equal the
enabled configuration. Signing, artifact, and rollout keys remain available
only through the configuration's environment-variable indirection; there is no
key, prompt, response, feature, rule body, or evidence-content argument.
Lifecycle output is a strict digest/stage/epoch projection, and validation,
storage, authorization, stale-head, and CAS failures collapse to fixed
content-free diagnostics. The five-stage plan, 128-candidate mining result,
256-receipt verification chain, configured capture quota/scan bounds, and
durable busy timeout bound operator memory, disk, and lock consumption.

Rollout is default-off and ZDD: deploy the command surface first while
`decision_codification.enabled` remains false, configure environment-indirected
keys and exact scope, mine and approve into shadow, then promote one observed
stage at a time. Roll back with the exact current receipt; if no compatible
history verifies, automation is disabled while the agent/LLM fallback remains
available. Operational rollback removes or disables the configuration after any
in-flight command completes; immutable artifacts remain auditable and cannot
silently reactivate themselves.

### Durable privacy-safe reuse observability

`gludd decision-codification status` returns one machine-readable, exact-scope
receipt for the enabled project and policy. The resolver records exact-rule
hits, typed abstentions by the closed `FallbackReason` enum, fallback calls,
avoided agent/LLM calls, resolution latency, rule-version changes, and closed
drift events. It never records prompts, context, decisions, correlation IDs,
features, model text, or exception content.

The existing SQLite WAL state owns fixed-cardinality aggregate rows: at most one
total per `DecisionKind` and one counter per closed abstention reason. Every
multiworker mutation uses the existing `BEGIN IMMEDIATE` transaction and bounded
busy timeout. The database is permanently bound to one exact project/policy
pair; a different binding fails closed. Latency is an integer microsecond sum
and maximum with a 300-second per-observation ceiling, counters stop at signed
64-bit bounds, and no request-level row exists to grow storage.

Status verifies the current candidate, lifecycle receipt, stage, epoch, policy,
and drift hold before returning five sorted summaries. Canonical JSON is capped
at 64 KiB, SHA-256 digest-bound, and HMAC-authenticated through the existing
environment-indirected artifact key. Thus a later release or deck can cite an
immutable bounded receipt without exposing the underlying decision traffic.

Observability is no-throw on the serving path: a clock, metric backend, lock, or
database failure cannot change the selected decision or add a second fallback.
The status command itself fails closed rather than fabricate evidence. ZDD
rollout remains default-off with decision codification; enable one exact scope
after deployment. Rollback disables the configuration or observer injection,
leaves the fixed aggregate tables inert, and preserves the original agent/LLM
path and all deterministic rollback behavior.

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

The split-event schema uses an expand/contract ZDD rollout. Deploy readers that
know `decision.outcome` first while producers continue the old shape; only
after every analysis worker accepts the additive type may capture producers emit
it. Then deploy producer code with `capture_identity` absent, add exact capture
identity and bounded retention configuration, and observe signed-bundle counts
before widening. Multi-host enablement additionally requires every producer to
share the migrated PostgreSQL `bucket_leases` table and the shared replay root
with atomic publication visibility; no new coordination schema is introduced.
Producer rollback comes first: remove `capture_identity` to stop new outcome
emission, allow exact-owner releases or the 60-second recovery bound,
drain/finalize in-flight bundles, and then roll readers back. Immutable bundles
containing the new type remain quarantined from older readers rather than being
rewritten.
Runtime decision lookup, active generation pointers, and agent fallback are not
coupled to capture availability, so capture rollback cannot interrupt serving
traffic or disable deterministic rollback.

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
- [OPA #5054](https://github.com/open-policy-agent/opa/issues/5054) reports that
  decision logs were absent from the default JSON console-log configuration but
  visible under other formats. Gludd therefore never treats console visibility
  as evidence: only events committed into a complete signed replay bundle can
  enter the analyzer.
- [OpenTelemetry's stable log data model](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/logs/data-model.md)
  treats structured event name, event time, and execution-context identifiers as
  distinct correlation fields. Gludd likewise emits separate typed decision and
  outcome events and joins them by exact HMAC correlation plus event digest,
  rather than parsing display text or correlating by time alone.
- [OpenTelemetry Python #4336](https://github.com/open-telemetry/opentelemetry-python/issues/4336)
  reports user-observed data loss when a bounded batch queue silently discarded
  older log records under exporter pressure. Gludd does not queue training
  evidence in memory: retention is checked before synchronous atomic append,
  and any lock/quota/store failure produces no finalized training bundle.
- [Prometheus client_python #568](https://github.com/prometheus/client_python/issues/568)
  has recorded since 2020 that stale per-worker metric files can accumulate and
  increase Gunicorn collection CPU. Gludd therefore keeps durable reuse totals
  in the already shared WAL database rather than treating worker-local metric
  files as release evidence.
- [Prometheus client_python #431](https://github.com/prometheus/client_python/issues/431)
  records a 2019 practitioner deadlock after mixing multiprocess metrics and
  inherited thread locks. Gludd uses short database transactions for durable
  aggregation and keeps the existing no-throw metrics adapter secondary.
- [Prometheus instrumentation guidance](https://prometheus.io/docs/practices/instrumentation/)
  recommends query counts, errors, and latency while warning that every label
  set adds a time series. Gludd uses only closed decision-kind, resolution-path,
  stage, and abstention enums; digests and project identifiers never become
  metric labels.
- [PostgreSQL index uniqueness checks](https://www.postgresql.org/docs/current/index-unique-checks.html)
  specify that a would-be duplicate inserter waits for the transaction owning an
  uncommitted conflicting key, then rechecks visibility. Gludd reuses the unique
  `bucket_leases.bucket_key` constraint and keeps the claim transaction open
  through capture instead of inventing a process-local lock.
- [Kubernetes #23731](https://github.com/kubernetes/kubernetes/issues/23731)
  has documented a practitioner-observed leader-election split brain since 2016
  when a former leader continued acting after losing its lease. Gludd grants a
  fresh holder per attempt, performs the write only inside that claim, and
  requires exact-owner release; a busy or uncertain claim produces no write.
- [SQLAlchemy discussion #8554](https://github.com/sqlalchemy/sqlalchemy/discussions/8554)
  records user failures since 2022 from concurrent work sharing one
  `AsyncSession`. Gludd awaits acquire, capture, and release in order: the worker
  thread never receives the session and no `gather()` shares its transaction.
- [MLflow #5133](https://github.com/mlflow/mlflow/issues/5133) records a 2021
  practitioner failure in which a training artifact named a dependency that was
  still absent when the model was loaded for serving. Gludd binds the dependency
  lock and source digest into approval and proves capture, mining, and live reuse
  through one hermetic runtime seam; deployment must still verify the exact
  environment rather than trusting artifact presence.
- [Vault #6501](https://github.com/hashicorp/vault/issues/6501) has documented
  since 2019 the confusing and unsafe precedence created when a CLI accepts both
  an environment token and inline token material. Gludd has one secret source:
  configured environment-variable pointers. No decision operator command has a
  key or credential value argument, and diagnostics never echo rejected input.
- [Kubernetes #61897](https://github.com/kubernetes/kubernetes/issues/61897)
  has recorded since 2018 that concurrent stale-resource updates are normal and
  need machine-recognizable optimistic-lock handling rather than blind writes.
  Activation and rollback therefore require both expected candidate and receipt
  digests; a stale head cannot append a usable transition or replace the pointer.
- [Helm #5377](https://github.com/helm/helm/issues/5377) records operator-facing
  atomic rollback failures and timeout confusion dating to 2019. Gludd does not
  wait for unrelated resources inside lifecycle commands: each promotion or
  rollback is one bounded durable CAS, and rollback either restores a verified
  compatible pointer or disables reuse so fallback remains available.
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
content-free attribution remain stable. The producer-to-reuse proof additionally
uses only automatically signed fallback evidence to build, approve, activate,
and exercise a zero-reviewer exact hit while pinning conflict, mismatch, expiry,
and disabled fallbacks. CLI tests cover request construction, existing
authentication, bounded config loading, safe capture/mining projections, exact
approval scope, one-stage activation, immutable stale-head rejection, ZDD
rollback-to-disabled behavior, and fixed content-free diagnostics.

The remaining production integration owns permissions, multi-host generation
state, and deployed live-traffic proof. Durable same-host generation state,
application-outcome feedback, automatic signed agent-outcome capture, and its
shared-PostgreSQL producer lease are now implemented and tested. Shared schema
and infrastructure changes must still land once and merge forward.

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
