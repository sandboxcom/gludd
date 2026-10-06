# Decision-log codification

**Status:** Core implemented; daemon integration pending.

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
activation, a zero-call rule hit, fallback, and rollback. The feature is not yet
wired into the daemon, event loop, durable database, recorder, CLI, or API.
Therefore no production traffic is currently served by a codified rule, and
avoided-call metrics remain an integration outcome rather than a deployed
claim.

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
closed vocabulary, and preserves the bounded abstention reason. Daemon wiring
must supply that existing fallback adapter and outcome recording.

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

These findings support deterministic exports, immutable provenance, and
abstention. They do not justify fuzzy runtime matching or autonomous approval.

## Integration and verification

The standalone service now owns verified source-marker construction, bounded
analysis, lookup/fallback orchestration, and end-to-end core ZDD evidence. Its
48-bundle test demonstrates candidate mining, exact human approval, activation,
a zero-fallback rule hit, an unseen-context fallback, and rollback continuity.

The single-writer production integration slice still owns automatic replay
capture, daemon/event-loop invocation, terminal outcome feedback, durable
multiworker repositories and migration, permissions, config, and CLI/API.
Shared schema and infrastructure changes must land once and merge forward.

Focused tests live under `tests/unit/test_decision_codification_*.py`. The
documentation drift test is
`tests/unit/test_decision_log_codification_docs_sync.py`; it pins this guide,
the design status, and the reveal.js contract token. Production changes retain
the repository floors of at least 85% aggregate coverage and at least 75% in
each file, with decision-codification modules targeting 90% branch coverage.
