# Typed Decision Provider Admission

## Decision

As of 2026-09-27, Gludd should not ship a direct OpenJev, Jev, Laya, Nimble,
Verdict, Kev, or System One integration in v0.1.2. The category is relevant to
Gludd, but every reviewed implementation is too new for a production control
dependency and nominal wire compatibility does not imply equivalent decisions.

The future integration boundary should be one provider-neutral typed-decision
protocol, initially shadow-only. It must not be an `openjev` provider preset:
Gludd's provider presets are explicitly chat-completion integrations, while
these services expose a decision endpoint with different request, response,
calibration, and lifecycle contracts. No upstream package, container, model, or
SDK is added by this decision.

This is a deliberate defer-with-admission-gates decision, not a rejection of the
capability. A later release may admit one or more backends behind the same
Gludd-owned contract after they pass the evaluation, security, lifecycle, and
zero-downtime gates below.

The five independently deliverable core steps and optional expansion artifacts
are tracked in the
[`v0.1.2 typed-decision backlog`](../releases/v0.1.2-typed-decision-backlog.md).
The implementation-ready, provider-neutral boundary is specified in the
[`OpenJev/Laya adapter and update contract`](../releases/v0.1.2-openjev-laya-adapter-contract.md).
That contract and its non-runnable schemas do not satisfy the revisit triggers
below or authorize a runtime integration.

## What OpenJev Means

There is no single canonical "OpenJev" project. The name currently refers to
multiple unrelated repositories, including:

- [`razorback16/openjev`](https://github.com/razorback16/openjev), an Apache-2.0
  Jev-shaped server whose primary backend is DiffusionGemma and which can also
  route to Laya and Verdict;
- [`bonsai/openjev`](https://github.com/bonsai/openjev), an MIT research
  baseline built around direct option logits from Qwen models;
- [`zhihz/openjev`](https://github.com/zhihz/openjev), a separate local,
  bilingual research preview; and
- [`kw2828/OpenJev`](https://github.com/kw2828/OpenJev), another local decision
  playground and application experiment.

An integration keyed only by the display name would therefore be ambiguous and
unsafe. Gludd must identify a backend by protocol version, endpoint, exact model
revision, prompt/scoring contract digest, and evaluated capability evidence.

## Capability Fit

Typed-decision systems accept state plus a bounded set of questions and return
booleans, choices, scores, and probability distributions without generating a
free-form answer. TypeSafe's primary documentation describes the three question
types as Choice, Score, and Noul and exposes them through
`POST /v1/systemone`: [TypeSafe API](https://docs.typesafe.ai/api.md) and
[TypeSafe primitives](https://docs.typesafe.ai/primitives.md).

That shape could eventually reduce latency and cost for high-volume, bounded
Gludd judgments such as:

- failure taxonomy classification;
- candidate model or specialist routing;
- low-risk context retention ranking; and
- escalation recommendation in a workflow where deterministic policy retains
  final authority.

It does not replace planning, code generation, explanations, evidence
collection, or acceptance tests. It also must not authorize repository
mutation, command execution, credentials, deployment, release, or security
decisions. Gludd already encodes that boundary in
`src/general_ludd/routing_roles/small_model_policy.py`: high-impact operations
are forbidden, an exact local evaluation suite must prove capability, and
completion still requires deterministic acceptance evidence.

The strongest concrete gap is therefore not "support OpenJev." It is "evaluate
bounded decisions through a typed, provider-neutral interface while retaining
Gludd's deterministic authorization and acceptance policies." That gap is real,
but no current production path requires it, so adding an unused client now
would create maintenance and attack surface without operational benefit.

## Candidate Comparison

The table records upstream claims and repository state observed on 2026-09-27.
Performance numbers are not treated as Gludd evidence until reproduced on a
Gludd-owned, task-specific dataset.

| Candidate | Relevant capability | License and update path | Admission result |
|---|---|---|---|
| TypeSafe Jev | Reference hosted `/v1/systemone` contract; Choice, Score, and Noul; official Python and JavaScript SDK documentation | Closed hosted model in early access, announced 2026-09-15; moving `jev-latest` alias and pin-able model versions | Reference contract only. Do not make a closed early-access service a required dependency. A future shadow adapter may use raw HTTPS or the maintained SDK after license and release policy review. |
| `razorback16/openjev` | Jev-shaped HTTP API; DiffusionGemma, MLX, Laya, Verdict, CLM, and JevK5 routes; text and optional image reads | Apache-2.0 code and documented Apache-2.0 primary weights; image pins and patches an upstream vLLM commit; no repository security policy detected | No direct integration. It is a young aggregation/runtime project, not a stable semantic target. Consider only as an externally managed endpoint after compatibility and supply-chain gates pass. |
| Laya | 421M English/typed-decision and 322M multilingual encoders; local Python/server paths; exact Hub revision can be selected | Apache-2.0; active releases, but no repository security policy detected | Best current local evaluation candidate, not production-ready for Gludd. Shadow-test only after pinning code and weights. |
| Bespoke Nimble | Open training recipe and local 9B scorer; up to 8,192 tokens and 255 options in the current checkpoint | Repository says it was built in one day and to expect rough edges; model updates changed context and temperature within days; no security policy detected | Research input only. It uses its own flat schema and current probabilities are not a portable control signal. |
| Verdict | Small local encoder, per-task fitting, temperature calibration, conformal abstention, and a Jev-shaped server | Apache-2.0; very small/new project and roadmap still includes core runtime and multilingual work | Promising evaluation comparator because abstention matches Gludd's fail-closed posture. Do not depend on it until lifecycle and maintenance gates pass. |
| Kev | Jev-shaped API over Ollama or another OpenAI-compatible backend; published API versioning | Apache-2.0, but it supplies an engine/interface rather than stable decision weights | No separate integration. If it proves compatible, the future protocol adapter should reach it without vendor-specific code. |
| LiteVar System One | Local Rust runtime with a model-independent `DecisionModel` boundary and Jev-shaped API | Early project; upstream says macOS arm64 is smoke-tested while hardware and reference-vector validation remain release checklist work | Architecture reference only. Revisit after cross-platform and reference-vector evidence matures. |

Primary project sources:

- [TypeSafe launch announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
  identifies Jev as early access and dates the public launch to 2026-09-15.
- [`razorback16/openjev` README](https://github.com/razorback16/openjev#readme)
  documents its API, hardware requirements, backend routing, pinned vLLM patch,
  caveats, and licenses.
- [Laya README](https://github.com/NandhaKishorM/laya#readme) documents its
  models, limits, calibration results, server, and revision selection.
- [Nimble README](https://github.com/bespokelabsai/nimble#readme) explicitly
  describes the one-day origin, rough edges, update history, resource needs,
  and probability limitations.
- [Verdict README](https://github.com/Manavarya09/verdict#readme) documents
  its calibration, conformal abstention, benchmarks, limits, and Apache-2.0
  license.
- [Kev README](https://github.com/arjun988/Kev#readme) documents its stable
  wire versioning and bring-your-own-backend design.
- [LiteVar System One README](https://github.com/LiteVar/system-one#readme)
  documents its model-independent runtime, offline cache checks, and current
  validation boundary.

## Compatibility Is Not Semantic Equivalence

Several candidates accept the same broad JSON shape, but Gludd cannot safely
move a threshold or decision rule between them:

- Laya documents normalized-entropy confidence, while the TypeSafe contract
  uses a different choice-confidence formula. A numeric threshold therefore
  changes behavior even when the response parses.
- Laya's available option budget depends on prompt and label lengths; its own
  documentation warns that options are trimmed and accuracy falls as the option
  count grows. TypeSafe documents up to 255 Choice options.
- Backends differ on null criteria, long-state behavior, truncation, model
  aliases, score legends, usage accounting, and whether one request actually
  shares state computation.
- Model aliases such as `latest` are mutable. Thresholds validated against one
  model, temperature, option count, language, or prompt renderer cannot be
  inherited by a later revision.
- A structurally valid answer can still be confidently wrong. Type safety
  prevents an out-of-schema value; it does not prove the judgment.

The future Gludd contract must therefore normalize only syntax. It must preserve
backend-reported probability and confidence fields with their declared
semantics, and it must select policy thresholds from Gludd-owned evaluation
evidence keyed by the full model and scoring identity. It must never translate
one backend's confidence into another backend's field silently.

## Practitioner and Forum Evidence

The project rules ask for long-lived practitioner reports. None exist for this
ecosystem yet: Jev launched on 2026-09-15, only twelve days before this review,
and the open alternatives followed immediately. The absence of aged reports is
itself lifecycle evidence and is one reason to defer production admission.

Recent reports still reveal failure classes that Gludd's evaluation must pin:

- [Laya issue 394](https://github.com/NandhaKishorM/laya/issues/394) measures a
  confidence threshold that worked for three options but selected below model
  accuracy for twenty options. The maintainer kept the issue as a reference and
  changed the documentation to require task-specific fitting.
- [Laya issue 302](https://github.com/NandhaKishorM/laya/issues/302) records
  `/v1/systemone` differences in option limits, null score levels, and confidence
  calculation despite general Jev compatibility.
- [Laya issue 124](https://github.com/NandhaKishorM/laya/issues/124) reports
  confidently wrong multilingual routing and an uncalibrated high-cardinality
  temperature bucket.
- [Laya issue 44](https://github.com/NandhaKishorM/laya/issues/44) reports that
  calibration compressed probabilities but did not improve incorrect ordering
  for a tool-compaction experiment; the discussion also highlights missing
  state as a task-design problem no model can repair.
- A [LocalLLaMA OpenJev thread](https://www.reddit.com/r/LocalLLaMA/comments/1wib9kj/openjev/)
  repeatedly distinguishes a one-pass classifier/readout from Jev's claimed
  calibration training and asks for reproducible benchmarks rather than
  assuming interface parity.
- The [`razorback16/openjev` launch thread](https://www.reddit.com/r/LocalLLaMA/comments/1wjlyzr/still_on_the_jev_waitlist_i_hosted_openjev_its/)
  candidly calls the service v0.1 on an unmerged vLLM change and warns that its
  single hosted GPU may return overload responses.
- A broader [agent-architecture discussion](https://www.reddit.com/r/AI_Agents/comments/1woe40a/i_went_through_jev_and_the_open_decisionmodel/)
  identifies the durable opportunity as a heterogeneous decision layer, not a
  particular branded model. That supports a protocol boundary rather than
  project-specific integrations.

These reports are recent, small-sample, and sometimes self-reported. They are
inputs to Gludd's risk model, not proof that any candidate is categorically good
or bad. Re-review after the ecosystem has at least ninety days of public issue
history and two stable releases from a candidate.

## Security Boundary

No typed-decision result may directly authorize a high-impact action. The
deterministic small-model policy remains the authority, including its forbidden
impacts and exact capability-evidence requirements.

A future adapter must satisfy all of these controls:

1. Accept only an operator-configured HTTPS endpoint or an attested local
   endpoint. Task content must never choose the host, scheme, model, or headers.
2. Reuse Gludd's canonical URL and SSRF validation, block redirects and private
   address changes unless the endpoint is an explicitly owned local worker, and
   keep bearer credentials in the existing secret boundary.
3. Bound request bytes, state tokens, question count, option count, response
   bytes, concurrency, queue depth, connect/read deadlines, and retries. A 429,
   529, timeout, malformed response, unknown model, or semantic mismatch must
   fail closed to the existing path, not trigger an unbounded retry storm.
4. Validate every answer against the request: exact question identifiers,
   answer type, declared labels, finite probabilities in `[0, 1]`, bounded
   rounding tolerance for sums, finite scores, and no undeclared fields used as
   authority.
5. Pin code/container digest, model-weight revision and digest, tokenizer,
   renderer/scoring contract, calibration artifact, and runtime version. A
   moving alias may be observed in research but cannot be promoted.
6. Require an upstream security policy or a Gludd-owned vendoring and patch
   process, dependency and image scanning, SBOM/provenance evidence, and a
   documented vulnerability response owner before production admission.
7. Emit content-free request identity, model identity, latency, fallback reason,
   and decision/evaluation correlation. Do not log state, criteria, bearer
   tokens, or raw answers that may contain sensitive task data.

The reviewed OpenJev, Laya, and Nimble repositories had no detected GitHub
`SECURITY.md` on 2026-09-27. That is not evidence of a vulnerability, but it is
insufficient lifecycle ownership for a new production dependency.

## Lifecycle Ownership

Gludd, not an upstream project, must own acquisition through teardown.

For a hosted service, the future adapter owns client construction, health and
model discovery, bounded calls, response validation, circuit breaking, and
drain. The vendor owns the remote runtime. An outage or contract mismatch falls
back to the current Gludd decision path.

For a self-hosted service, Gludd's existing universal model-worker lifecycle
owns `provision -> attest/configure -> health-ready -> publish -> work` and the
reverse `drain -> retire -> destroy -> absence proof`. A candidate-specific
configuration adapter may render an exact pinned server launch, but it may not
create an independent daemon, cache, port namespace, cleanup script, or process
supervisor. Model cache acquisition must use Gludd's leased, digest-checked
cache and resource budgets.

The typed-decision transport owns neither authorization nor acceptance.
Callers map its output into a recommendation; `SmallModelTaskPolicy` decides
whether local bounded work is eligible, and deterministic acceptance checks
decide whether the result can be used.

## Admission Plan

Implementation is justified only after one concrete Gludd caller and labelled
evaluation corpus are selected. The first recommended caller is
`failure_classification`, because it is bounded, already present in
`DEFAULT_TASK_CONTRACTS`, and can run without granting command or mutation
authority. Context compaction is second; security, completion, deployment, and
release judgments remain excluded.

### Phase 0: frozen evaluation

- Select at least two candidates, with Laya and the existing generative path as
  the initial comparison; optionally add hosted Jev if access and terms permit.
- Pin every artifact and record the exact request renderer.
- Build a Gludd-owned, human-reviewed corpus split by task, option count,
  language, input length, ambiguity, and explicit `unknown`/abstain cases.
- Measure accuracy, per-class recall, Brier score, expected calibration error,
  risk/coverage, p50/p95 latency, memory, throughput, and fallback rate.
- Test metamorphic stability under option reorder, label rename, irrelevant
  state, long state, missing evidence, and adversarial instructions.

### Phase 1: shadow-only adapter

- Send eligible decisions to both the current path and one candidate.
- Persist only redacted evaluation receipts; the candidate result cannot change
  execution.
- Treat every timeout or mismatch as a recorded fallback and keep production
  behavior unchanged.
- Require zero resource leaks and a clean drain/rollback exercise.

### Phase 2: bounded canary

- Promote one exact candidate identity for one task and one corpus-derived
  threshold.
- Start at zero-authority recommendation mode, then a small percentage of
  low-impact tasks only after shadow metrics meet the approved baseline.
- Automatically fall back when health, drift, calibration, or resource bounds
  fail. Never promote a moving model alias automatically.

### Phase 3: optional production backend

- Admit the candidate as one implementation of the generic contract, not as a
  special branch throughout Gludd.
- Re-run evaluation on every code, model, tokenizer, renderer, calibration, or
  runtime change. No evidence transfers across identities.
- Keep at least one independent fallback and preserve operator disablement with
  no migration or restart requirement.

## Zero-Downtime Delivery and Rollback

The v0.1.2 decision document changes no runtime, dependency, process, port,
database, or configuration and therefore needs no deployment action.

Any future adapter rolls out dark: configuration parsing and health checks may
be deployed while calls remain disabled, then shadow traffic can start without
changing decisions. A new endpoint generation becomes eligible only after
attestation and health; the prior generation remains available until in-flight
calls drain. Rollback disables shadow/canary selection and drains the candidate,
while the existing decision path remains continuously available. Stored
evaluation receipts are append-only and require no rollback migration.

## Revisit Triggers

Reopen this decision when all of the following are true:

- a named Gludd bounded-decision caller has enough volume to justify the added
  subsystem;
- at least one candidate has ninety days of public history, two stable releases,
  a security reporting policy, immutable artifacts, and a documented upgrade
  path;
- a second independent implementation passes the same frozen compatibility
  suite, proving that the Gludd contract is provider-neutral;
- task-specific evaluation beats or safely cascades with the current path and
  meets an approved calibration/risk-coverage target;
- shadow, overload, restart, drain, cache-pressure, and rollback tests pass with
  the project's coverage and warning-free gates; and
- one Gludd subsystem owner is named for transport compatibility, evaluation
  corpus maintenance, threshold review, and upstream security updates.

Until then, the correct v0.1.2 integration is no runtime integration: retain the
research, preserve the admission contract, and avoid coupling Gludd's control
plane to a twelve-day-old and semantically fragmented ecosystem.
