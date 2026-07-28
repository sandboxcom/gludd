# SPEC — ML/AI expert collection and safe self-improvement

**Status:** READY-TO-IMPLEMENT  
**Specification date:** 2026-07-28  
**Implementation status:** Not implemented; every numbered requirement below is
an atomic feature specification, not a claim about current behavior.  
**Research basis:**
[`ML_AI_EXPERT_SYSTEM_RESEARCH_2026-07-28.md`](../../research/ML_AI_EXPERT_SYSTEM_RESEARCH_2026-07-28.md)

## 0. Objective and non-goals

Gludd needs an expert collection that can answer ML/AI questions and derive
solutions by combining current evidence, appropriate tools, repeatable
experiments, and independent verification. It also needs a safe way to turn its
own observed failures into candidate improvements without editing or grading
the live system in place.

No implementation can guarantee an answer to every possible question. The
enforceable promise is:

1. decompose a question into answerable claims and executable checks;
2. retrieve versioned, attributable evidence from a maintained source registry;
3. treat retrieved content as untrusted data;
4. use deterministic tools where they are more reliable than model recall;
5. expose evidence strength, uncertainty, conflicts, and abstentions;
6. reproduce every material experiment and cited claim; and
7. promote self-improvements only after independent, held-out evaluation.

This specification does **not** authorize:

- autonomous weight training, production deployment, or package installation;
- bypassing Gludd's capability lattice, approval rules, project isolation, or
  protected paths;
- using benchmark scores as universal capability claims;
- using model-generated reasoning as proof;
- allowing a candidate to be the sole author and judge of its own promotion; or
- writing experimental changes into a running daemon checkout.

## 1. Current seams and reuse decisions

The implementation MUST extend existing Gludd seams rather than duplicate them.

| Existing seam | Reuse | Required change |
|---|---|---|
| `agents/researcher.py::ResearcherAgent` | Keep as the low-level research adapter | Replace URL-only confidence heuristics with typed claims, source policy, corroboration, and verifier outputs |
| `retrieval/research_index.py::ResearchIndex` | Keep persistence and freshness concepts | Namespace by project/tenant, retain document versions, and add claim-level provenance |
| `retrieval/agentic_context.py::AgenticContextInjector` | Keep bounded context assembly | Mark all retrieved content as untrusted data and preserve evidence IDs through prompt construction |
| `memory/embedding_store.py::MemoryEmbeddingStore` | Keep the embedding adapter | Add hybrid retrieval and a deterministic lexical fallback; never make an embedding provider mandatory |
| `memory/procedural.py::ProceduralMemory` | Keep procedural-memory storage | Store only approved procedures with origin, evaluator, expiry, and rollback metadata |
| `scoring/router.py::AdaptiveRouter` | Keep quality/cost/health routing | Add risk, capability, evidence, and reproducibility constraints |
| `models/gateway.py::ModelGateway` | Keep the provider boundary | Expose model/version/parameters in every experiment and answer trace |
| `eval/harness.py::EvalHarness` | Keep the evaluation protocol seam | Generalize beyond patch evaluation to expert, retrieval, trajectory, safety, and promotion suites |
| `ag15_benchmarks/benchmark_harness.py::BenchmarkSuite` | Keep benchmark adapters | Add dataset cards, contamination checks, seeds, slices, and immutable result manifests |
| `compaction/arena.py::SelfImprovingCompactor` | Reuse champion/challenger pattern | Extract a generic promotion protocol rather than copying arena logic |
| `ag13_dspy/` and `ag14_reflexion/` | Keep optional prompt/reflection adapters | Run only inside a bounded experiment, never as an implicit live update |
| `self_improve/harness.py::SelfImprovementHarness` | Keep failure discovery | Produce proposals and evidence bundles, not direct live-tree mutations |
| `projects.workspace` and git automation | Keep isolated project workflow | Route every self-improvement through a dedicated `gludd-self` project workspace |
| `security/capability_lattice.py` | Keep default-deny authorization | Split broad research/self-improve roles into minimum scoped capabilities |
| `physics/mechanistic_interpretability.py` | Keep interpretability adapter seam | Label exploratory explanations and require method-specific validation |

Mature libraries remain optional adapters. Gludd MUST NOT reimplement numerical
arrays, estimators, training frameworks, vector databases, experiment tracking,
evaluation runners, or telemetry standards. Preferred integration candidates and
their evidence are recorded in the research dossier. Core operation MUST remain
available with standard-library/local deterministic fallbacks.

## 2. Architecture

```text
question
   |
   v
risk + domain intake -----> capability/approval policy
   |
   v
claim/task decomposition
   |
   +----> evidence broker ---> versioned sources ---> claim ledger
   |
   +----> tool/experiment broker ---> sandbox ---> artifact manifest
   |
   +----> candidate solution(s)
                    |
                    v
          independent verifier(s)
                    |
                    v
 cited answer + calibration + limitations + reproducibility bundle
```

Self-improvement uses a separate control plane:

```text
immutable outcomes -> recurring-failure proposal -> isolated candidate workspace
     -> train/dev iteration -> frozen holdout + adversarial evaluation
     -> blinded independent promotion decision -> shadow -> canary -> champion
                                                   \------ rollback --------/
```

The expert plane may read approved Gludd artifacts. The experiment plane may
write only inside its project workspace and artifact store. The promotion plane
may change a live selection pointer only through an authorized, audited,
reversible operation.

## 3. Shared contracts

### 3.1 Answer and claim schema

Every answer MUST serialize to a versioned schema with:

```yaml
answer_id: uuid
schema_version: 1
question: string
question_hash: sha256
domain_labels: [string]
risk_level: low|medium|high|prohibited
status: answered|partial|abstained|blocked
summary: string
claims:
  - claim_id: string
    text: string
    type: fact|inference|recommendation|measurement
    support: [evidence_id]
    counterevidence: [evidence_id]
    confidence: 0.0
    verification: verified|partially_verified|unverified|contradicted
limitations: [string]
artifacts: [artifact_id]
trace_id: string
```

Confidence is a calibrated probability-like score for a declared event, not a
stylistic synonym for certainty. The event definition and calibration suite MUST
be stored with the answer.

### 3.2 Evidence schema

```yaml
evidence_id: sha256
canonical_uri: string
source_class: paper|standard|official_docs|repository|article|forum|local
publisher: string
authors: [string]
published_at: timestamp|null
retrieved_at: timestamp
version: string|null
content_digest: sha256
locator: string
license: string|null
integrity: active|corrected|retracted|withdrawn|unknown
trust_label: primary|secondary|practitioner_signal|local_observation
project_id: string
tenant_id: string
```

Evidence content never enters an instruction channel. A source's reputation
changes retrieval priority, not whether a claim is automatically true.

### 3.3 Experiment and promotion schema

Every experiment manifest MUST identify:

- immutable candidate and champion revisions;
- dataset and case IDs, split, licenses, and content digests;
- model provider, exact model/version, decoding parameters, prompts, tools, and
  environment lock digest;
- seeds or a deterministic-run declaration;
- per-case outcomes, costs, latency, safety results, and artifacts;
- evaluator identity, evaluator independence class, rubric version, and blinded
  ordering;
- promotion thresholds, statistical method, decision, approver, and timestamp;
- rollout phase, selection pointer revision, and rollback target.

### 3.4 Default resource envelope

Unless a project policy supplies stricter values, one expert request is bounded
to 12 search queries, 40 fetched documents, 32,000 injected evidence tokens,
three candidate solutions, two verifier passes, two concurrent network fetches,
and one retry per failed source. Tool time, model tokens, wall-clock time,
storage, and monetary cost MUST each have explicit hard limits. Reaching a limit
produces a partial answer or abstention; it MUST NOT start an unbounded loop.

## 4. Expert collection specifications

### MLAI.1 — Versioned expert collection skeleton

**Contract:** Add a `general_ludd.ml_ai_expert` collection manifest, role
registry, prompt assets, schemas, source policy, benchmark registry, and
collection-local tests. The collection MUST be disabled until explicitly
enabled by project policy and MUST declare every external capability.

**Primary seams:** `collections/`, `agents/registry.py`, collection loading, and
project collection precedence.

**Acceptance:** Schema validation rejects unknown roles/capabilities; an
install/load/unload compatibility test leaves a running daemon available; all
new Python files have at least 75% line coverage and the collection aggregate is
at least 85%.

### MLAI.2 — Question, domain, and risk intake

**Contract:** Normalize the question, identify ML/AI subdomains, classify risk,
record assumptions, and choose `answer`, `clarify`, `abstain`, or `block` before
retrieval or tool execution. High-risk recommendations require the applicable
human approval policy.

**Primary seams:** New `ml_ai_expert/intake.py`, capability lattice, and approval
router.

**Acceptance:** A versioned 200-case suite reaches macro F1 at least 0.90 for
domain labels and recall 1.00 for prohibited fixtures; paraphrases produce the
same risk decision in at least 98% of paired cases.

### MLAI.3 — Claim and task decomposition

**Contract:** Decompose a request into atomic factual claims, calculations,
experiments, design decisions, and missing inputs. Preserve a dependency DAG and
assign every leaf an evidence or tool requirement.

**Primary seams:** New `ml_ai_expert/decomposition.py` and typed answer schema.

**Acceptance:** On 100 gold questions, at least 95% of gold-critical leaves are
represented; no executable leaf lacks a tool/evidence plan; cyclic dependency
fixtures fail deterministically.

### MLAI.4 — Evidence acquisition and source policy

**Contract:** Search the configured primary, official, scholarly, code, and
practitioner sources through adapters. Apply domain allow/deny rules, robots and
rate limits, maximum sizes, content types, licenses, SSRF protection, and source
deadlines before fetching.

**Primary seams:** `agents/researcher.py`, new
`retrieval/evidence_broker.py`, and source-policy registry.

**Acceptance:** Controlled source fixtures demonstrate 100% blocking of private,
loopback, link-local, unsupported-scheme, oversized, and disallowed targets;
rate-limit tests do not exceed configured concurrency; failed sources appear in
the answer trace rather than disappearing.

### MLAI.5 — Claim-level provenance ledger

**Contract:** Persist the exact source version and locator supporting or
contradicting each material claim. Compute evidence IDs from normalized metadata
and content digest. A citation displayed to a user MUST resolve to the ledger
entry used during generation.

**Primary seams:** `retrieval/research_index.py`, answer schema, and artifact
store.

**Acceptance:** Citation precision is at least 0.98 and material-claim support at
least 0.95 on the frozen citation suite; fabricated or stale-mismatched citations
are zero; replay after a source changes still loads the original digest.

### MLAI.6 — Retrieval pipeline and independent retrieval evaluation

**Contract:** Provide lexical, dense, hybrid, metadata-filtered, and reranked
retrieval behind one protocol. Evaluate parsing, chunking, indexing, candidate
retrieval, and reranking independently from generation.

**Primary seams:** `memory/embedding_store.py`,
`retrieval/research_index.py`, and benchmark harness.

**Acceptance:** A versioned Gludd gold corpus achieves recall@10 at least 0.90
and reports precision@k, MRR, nDCG, duplicate rate, parse failures, latency, and
cost. Disabling embeddings retains a deterministic lexical path. A generation
score cannot mark a failed retrieval gate green.

### MLAI.7 — Evidence-grounded synthesis

**Contract:** Generate answers only from the claim DAG, admitted evidence, and
tool artifacts. Separate observations, inferences, measurements, and
recommendations. Surface credible disagreement and negative results.

**Primary seams:** New `ml_ai_expert/synthesis.py`,
`retrieval/agentic_context.py`, and model gateway.

**Acceptance:** Unsupported material claims are at most 1% on a 200-question
suite; contradictory-source fixtures cite both sides and describe the conflict
in at least 95% of cases; no evidence text can alter system policy in injection
tests.

### MLAI.8 — Calibrated confidence and abstention

**Contract:** Estimate claim and answer confidence using held-out calibration
data, evidence sufficiency, verifier agreement, and retrieval quality. Abstain or
return partial status when minimum evidence is absent.

**Primary seams:** New `eval/calibration.py`, answer schema, and expert policy.

**Acceptance:** Expected calibration error is at most 0.05 on the frozen suite;
unanswerable-case abstention recall is at least 0.90; citation-free factual
fixtures never receive `verified`; calibration metrics are split by domain and
risk.

### MLAI.9 — Tool-backed solution derivation

**Contract:** Route exact computation, code execution, data analysis, search,
and benchmark tasks to declared tools. Validate inputs/outputs against schemas,
execute in a sandbox, capture environment and artifacts, and never infer tool
success from fluent output.

**Primary seams:** tool registry, sandbox backends, artifact store, and
capability lattice.

**Acceptance:** Invalid schemas and unauthorized tools are blocked in 100% of
fixtures; deterministic calculations match reference results exactly; every tool
result contains command/tool version, input digest, exit status, resource use,
and artifact digests.

### MLAI.10 — Independent answer verification

**Contract:** Verify citations, calculations, code/tests, contradiction
handling, policy compliance, and answer completeness with deterministic checks
first and a separately configured model only where semantic judgment is needed.
The generator cannot be the sole verifier.

**Primary seams:** New `ml_ai_expert/verifier.py` and generalized eval harness.

**Acceptance:** The verifier detects at least 95% of seeded unsupported-claim,
wrong-result, broken-citation, and policy-violation mutations with false-positive
rate at most 5%; same-model verification is labeled non-independent and cannot
authorize a high-risk answer.

### MLAI.11 — Evidence-, cost-, and risk-aware routing

**Contract:** Select roles, models, retrieval methods, and tools using measured
task quality, current health, cost, latency, context fit, risk, and capability.
Route decisions MUST be reproducible and fail closed when no candidate satisfies
constraints.

**Primary seams:** `scoring/router.py`, `models/gateway.py`, and capability
lattice.

**Acceptance:** Replay with a frozen routing snapshot produces identical
decisions; budget and capability violations are zero in adversarial tests; a
10,000-decision simulation never selects an unhealthy or unauthorized route.

### MLAI.12 — Domain expert role packs

**Contract:** Ship independently versioned packs for foundations/statistics,
classical ML, deep learning, NLP, computer vision, reinforcement learning,
generative models, RAG, agents, evaluation, safety, efficiency/systems,
interpretability, multimodal systems, and MLOps. A pack contains taxonomy,
method cards, source rules, benchmark slices, and failure modes—not vendor
marketing or a frozen “best model” list.

**Primary seams:** Collection role registry and expert knowledge assets.

**Acceptance:** Every pack passes schema and link validation, declares a maintainer
and review interval, maps to at least one benchmark slice, and has at least five
gold questions including one negative or “method not appropriate” case.

### MLAI.13 — Freshness, correction, and retraction handling

**Contract:** Revalidate mutable sources, retain versions, ingest DOI
relations/corrections/retractions where available, and prevent superseded
evidence from silently supporting a fresh answer.

**Primary seams:** Evidence broker, scheduled research refresh, Crossref/OpenAlex
adapters, and research index.

**Acceptance:** Retraction/correction fixtures update integrity state within one
successful refresh; a retracted source cannot be sole support for a verified
claim; historical answer replay preserves the earlier state and displays the
new integrity warning.

### MLAI.14 — Multimodal evidence and accessibility

**Contract:** Admit text, tables, figures, images, audio, and video only through
modality adapters that retain source location and transformation lineage.
Generated captions/transcripts are derived evidence and cannot replace the
original artifact.

**Primary seams:** Artifact store, new modality adapter protocol, and answer
renderer.

**Acceptance:** A 50-case multimodal suite verifies byte digest, page/time/region
locator, transformation model/version, and accessible text alternative for
100% of admitted artifacts; unsupported modalities cause a typed abstention.

### MLAI.15 — Reproducible expert report

**Contract:** Emit a human-readable answer plus machine-readable evidence,
experiment, routing, and verification manifests. A replay mode MUST reconstruct
the admitted context and deterministic tool steps without refetching mutable
sources.

**Primary seams:** Run recorder, artifact store, answer renderer, and provenance
API.

**Acceptance:** Two clean-environment replays of deterministic fixtures produce
identical claim/evidence/artifact digests; stochastic fixtures preserve inputs,
seeds, model identifiers, and per-sample outputs; secrets are redacted without
destroying provenance links.

## 5. Safe self-improvement specifications

### MLSI.1 — Isolated `gludd-self` project workspace

**Contract:** Route every self-improvement change through a dedicated project
workspace with its own branch, environment, artifacts, budget, and capability
profile. The running checkout and imported live modules are read-only.

**Primary seams:** Project workspace, git automation, self-improvement harness,
and daemon wiring.

**Acceptance:** Filesystem and import-hook tests block 100% of writes/reloads
targeting the running tree; concurrent experiments receive unique namespaces;
discarding an experiment leaves champion files and processes unchanged.

### MLSI.2 — Immutable observation and outcome ledger

**Contract:** Record candidate inputs from real outcomes: request class,
revision, model/tool versions, sanitized trajectory, deterministic checks,
human feedback, incidents, cost, latency, and environment. Append-only records
must distinguish observations from interpretations.

**Primary seams:** Run recorder, self-improvement harness, telemetry, and
artifact store.

**Acceptance:** Mutation and deletion attempts fail authorization; duplicate
events are idempotent; 100% of candidate proposals trace to at least one outcome
ID; tenant, secret, and retention-policy fixtures remain isolated/redacted.

### MLSI.3 — Evidence-backed candidate proposals

**Contract:** Generate a bounded candidate only after a recurring failure,
measured opportunity, or approved feature request crosses its declared trigger.
The proposal states causal hypothesis, affected surface, expected gain, risks,
tests, rollback, and relevant external evidence.

**Primary seams:** `self_improve/harness.py`, issue sources, research expert, and
project todo workflow.

**Acceptance:** Proposals lacking a trigger, baseline, test, risk, or rollback
are rejected; duplicate hypotheses within the configured similarity threshold
coalesce; no proposal directly changes champion state.

### MLSI.4 — Generic champion/challenger experiment protocol

**Contract:** Extract a reusable protocol from the compaction arena: immutable
champion, isolated challenger, declared metrics, paired cases, resource budgets,
and a typed outcome. Candidate iteration may use train/dev cases only.

**Primary seams:** `compaction/arena.py`, generalized eval harness, and
experiment registry.

**Acceptance:** Champion and challenger run the identical frozen harness and
case order; paired case IDs are complete; at least three seeds are used for
stochastic behavior unless determinism is proven and recorded.

### MLSI.5 — Frozen holdout and adversarial evaluator

**Contract:** Freeze train, development, holdout, and adversarial splits with
content digests before candidate iteration. Candidate processes cannot read
holdout prompts, expected outputs, judge keys, or adversarial labels.

**Primary seams:** Benchmark harness, sandbox, secret store, and evaluator.

**Acceptance:** Capability tests deny all challenger reads of protected splits;
split overlap and near-duplicate scans are zero above the configured threshold;
holdout is opened only by the independent evaluation process after candidate
freeze.

### MLSI.6 — Statistical promotion gate

**Contract:** Promote only when all deterministic gates pass, there are no
declared safety/regression failures, and the paired held-out quality improvement
has a predeclared lower 95% confidence bound above the minimum effect. Default
minimum effect is two absolute percentage points; projects may set a stricter
threshold, never silently lower it.

**Primary seams:** Generalized eval harness, experiment registry, and promotion
controller.

**Acceptance:** Boundary tests reproduce decisions exactly; missing cases,
changed thresholds, multiple-comparison violations, non-independent evaluators,
or inconclusive intervals produce `reject` or `needs_review`, never `promote`.

### MLSI.7 — Zero-downtime shadow and canary rollout

**Contract:** After offline promotion approval, run the challenger in
non-authoritative shadow mode, then canary it through a versioned selection
pointer. Use additive schemas and dual-read/write compatibility where state
changes are involved.

**Primary seams:** Model/agent router, deployment controller, telemetry, and ZDD
state-migration conventions.

**Acceptance:** Shadow output cannot affect user-visible state; at least three
healthy observation windows are required before each traffic increase; a mixed
old/new compatibility suite remains green throughout rollout.

### MLSI.8 — Automatic and operator rollback

**Contract:** Preserve the prior champion and provide automatic rollback on
safety, correctness, availability, cost, or latency thresholds plus an audited
operator kill switch. Rollback changes only the selection pointer before any
cleanup.

**Primary seams:** Promotion controller, deployment/router selection pointer,
incident workflow, and break-glass authorization.

**Acceptance:** Fault injection triggers rollback within 60 seconds; rollback
requires no rebuild or destructive migration; requests already in flight finish
under a declared version and new requests select the restored champion.

### MLSI.9 — Reward-hacking and judge-bias controls

**Contract:** Separate candidate author, semantic judge, deterministic oracle,
and promotion authority. Blind candidate identity/order, swap presentation order,
use multiple judge families for material semantic decisions, and monitor style
shortcuts and judge disagreement.

**Primary seams:** Evaluator registry, model gateway, and promotion controller.

**Acceptance:** The suite contains seeded verbosity, self-preference,
position-bias, sycophancy, and rubric-gaming candidates; none may promote solely
from the biased judge; order-swap decision disagreement is reported and blocks
automatic promotion above 5%.

### MLSI.10 — Contamination, privacy, license, and poisoning controls

**Contract:** Track origin and license of training/evaluation examples, scan
overlap across all splits and candidate context, enforce retention and deletion,
and quarantine anomalous or untrusted feedback. User data is excluded from
training by default.

**Primary seams:** Dataset registry, evidence ledger, privacy policy, and
artifact store.

**Acceptance:** Known contaminated, poisoned, unlicensed, cross-tenant, secret,
and deletion-request fixtures are blocked in 100% of cases; every admitted
example has an origin and policy decision; benchmark results disclose detected
overlap.

### MLSI.11 — Resource-aware experiment scheduler

**Contract:** Allocate CPU, memory, accelerator, disk, token, network, wall-time,
and monetary budgets before an experiment starts. Namespace processes, cap
parallelism, expose progress and heartbeats, and stop cleanly at a budget.

**Primary seams:** Scheduler, resource monitor, project workspace, model spend
guard, and observability.

**Acceptance:** Stress tests never exceed configured concurrency by more than one
in-flight cancellation interval; disk/memory/cost limits stop new work before
the hard system guard; runs longer than ten seconds emit progress or a heartbeat
at least every 30 seconds.

### MLSI.12 — Auditable promotion authority and kill switch

**Contract:** Promotion is a first-class, signed/audited action containing
candidate digest, evidence manifest, threshold policy, evaluator identities,
approval, rollout plan, and rollback target. High-risk system or policy changes
require human approval and cannot be delegated back to the candidate.

**Primary seams:** Capability lattice, approval system, audit log, promotion
controller, and break-glass path.

**Acceptance:** Unauthorized, expired, mismatched-digest, replayed, and
self-approved promotions are blocked in 100% of fixtures; every live selection
pointer resolves to one accepted promotion record; the kill switch is tested in
each release.

## 6. Reusable core-system specifications

### MLCORE.1 — Typed evidence broker

**Contract:** Introduce one protocol for discovery, fetch, parse, normalize,
version, and citation location across web, scholarly, repository, local, and
artifact sources. Adapters return typed failures and never raw policy decisions.

**Primary seams:** Retrieval package and researcher agent.

**Acceptance:** Contract tests run against every adapter; equivalent canonical
content produces the same digest; timeouts, parse failures, and policy denials
remain distinguishable in traces.

### MLCORE.2 — Declarative source-policy registry

**Contract:** Store source class, trust label, allowed domains/schemes/types,
freshness, rate, size, license, integrity, and corroboration requirements in a
versioned project policy. Hard security constraints cannot be relaxed by model
output.

**Primary seams:** Config models, evidence broker, and capability lattice.

**Acceptance:** Invalid/unknown policy fields fail startup validation; policy
resolution is deterministic across global/collection/project precedence; 100%
of evidence records include the policy revision that admitted them.

### MLCORE.3 — Hybrid retrieval and reranking protocol

**Contract:** Provide lexical, vector, filter, fusion, and reranker adapters with
stable query/result schemas, explicit score semantics, and an offline evaluator.
No one backend becomes a core dependency.

**Primary seams:** Retrieval and memory packages.

**Acceptance:** Each adapter passes the same conformance suite; score fusion is
deterministic under tied inputs; lexical-only mode passes core tests without
network or accelerator access.

### MLCORE.4 — Versioned evaluation registry

**Contract:** Register evaluation suites, cases, slices, metrics, rubrics,
oracles, datasets, and owners by immutable digest. Suites declare intended use,
limitations, license, contamination policy, and minimum sample size.

**Primary seams:** Eval harness, benchmark harness, and artifact store.

**Acceptance:** Missing cards or mutable case digests fail registration; results
cannot compare incompatible metric versions without an explicit migration;
every reported aggregate links to per-case records.

### MLCORE.5 — Calibration and selective prediction service

**Contract:** Offer reusable reliability diagrams, ECE/Brier/selective-risk
metrics, threshold fitting, abstention policies, and domain/risk slices. Never
reuse calibration across materially changed models, prompts, tools, or
distributions without revalidation.

**Primary seams:** New eval calibration module and model/run metadata.

**Acceptance:** Reference datasets reproduce known metrics within `1e-6`;
calibration revision mismatches fail closed; drift beyond a declared threshold
marks confidence stale and disables automatic high-risk use.

### MLCORE.6 — Immutable experiment registry

**Contract:** Record parameters, inputs, environment, code, models, artifacts,
metrics, and lineage behind an adapter protocol compatible with local storage
and mature trackers such as MLflow. Gludd policy remains authoritative.

**Primary seams:** Artifact store, run recorder, optional MLflow adapter.

**Acceptance:** Re-registering an identical manifest is idempotent; changing any
material input produces a new digest; optional tracker failure does not erase
the local authoritative record.

### MLCORE.7 — Sanitized trajectory dataset builder

**Contract:** Convert agent/tool traces into versioned datasets while preserving
action/result relationships, redacting secrets and personal data, applying
retention, and recording sampling bias. Failed and abstained trajectories are
first-class data.

**Primary seams:** Run recorder, privacy controls, dataset registry, and
artifact store.

**Acceptance:** Seeded secrets/PII are absent from exported content and present
only as typed redaction markers; source event lineage coverage is 100%; outcome
class distributions and excluded-record counts are reported.

### MLCORE.8 — Untrusted-content boundary

**Contract:** Tag evidence, memories, tool output, critiques, and external
messages as untrusted data from ingestion through prompt rendering. Instructions
inside data cannot acquire tool, routing, approval, or persistence authority.

**Primary seams:** Context injector, prompt renderer, tool broker, memory, and
capability lattice.

**Acceptance:** A maintained direct/indirect injection corpus achieves 100%
prevention of unauthorized actions and secret disclosure; benign quoted
instructions remain available as evidence; security decisions are made outside
the generator.

### MLCORE.9 — Project- and tenant-namespaced knowledge stores

**Contract:** Namespace research indexes, embeddings, procedures, experiments,
and caches by project and tenant with explicit, audited sharing rules. Remove
implicit user-global knowledge paths from production defaults.

**Primary seams:** Research index, embedding store, procedural memory, and
project workspace.

**Acceptance:** Cross-tenant read/write fixtures are blocked in 100% of cases;
cache keys include tenant, project, policy revision, and content digest; an
export/import operation preserves ownership and provenance.

### MLCORE.10 — Capability- and risk-aware action router

**Contract:** Resolve requested action, data sensitivity, risk, tool capability,
agent delegation, and approval into an allow/deny/clarify decision before model
or tool execution. Split broad research/self-improve permissions into minimum
scopes.

**Primary seams:** Capability lattice, role registry, approvals, and router.

**Acceptance:** The decision table has complete deny-by-default coverage;
property tests never produce an allow with a missing capability; delegated roles
cannot exceed the intersection of parent authority and project policy.

### MLCORE.11 — Provenance and replay API

**Contract:** Expose authenticated APIs to resolve answer, claim, evidence,
artifact, experiment, evaluation, and promotion lineage and to request an
authorized replay. Raw secrets and protected holdout content remain inaccessible.

**Primary seams:** API routers, run recorder, artifact store, and audit log.

**Acceptance:** Every public answer ID resolves to a complete non-secret lineage
graph; authorization tests cover tenant, role, holdout, and deleted data; replay
reports exact and non-exact components before execution.

### MLCORE.12 — Drift and feedback-loop monitor

**Contract:** Monitor input, retrieval, model, outcome, calibration, cost, and
feedback distributions by meaningful slice. Detect feedback loops where model
outputs enter future training/evidence and retain the generation lineage.

**Primary seams:** Telemetry, dataset registry, calibration, and incident
workflow.

**Acceptance:** Synthetic covariate, label, retrieval, calibration, and recursive
model-content shifts trigger their configured alert; alerts include baseline,
window, statistic, threshold, slice, and candidate causes; no alert promotes a
change automatically.

### MLCORE.13 — Resource-aware work scheduler

**Contract:** Provide shared admission control, priority, namespace, concurrency,
deadline, cancellation, and resource accounting for research, evaluation, and
experiments. Release work remains higher priority than background research.

**Primary seams:** Scheduler, resource monitor, project registry, and model spend
guard.

**Acceptance:** Priority tests show release-critical work admitted before
background expert refreshes; cancellations release leases/resources; per-project
and system caps remain enforced during concurrent stress.

### MLCORE.14 — Mature-tool adapter and dependency policy

**Contract:** Define a common lifecycle for optional OSS adapters: evidence of
need, maintained project evaluation, license/security review, version pin,
health check, capability declaration, fallback, update policy, and removal.

**Primary seams:** Dependency policy, collection manifests, adapters, and update
audits.

**Acceptance:** An optional dependency may be absent without breaking core
startup; unpinned, unlicensed, abandoned, vulnerable, or no-fallback adapters
fail registration under policy; update findings include actionable versions and
do not emit unresolved informational noise.

### MLCORE.15 — GenAI/ML observability semantics

**Contract:** Extend tracing with question/claim/evidence/retrieval/model/tool/
evaluation/promotion spans aligned where practical with OpenTelemetry GenAI
semantic conventions. Record tokens, latency, cost, cache, errors, model/version,
policy revision, and artifact IDs without prompt or secret leakage by default.

**Primary seams:** Observability package, model gateway, tool broker, evaluator,
and promotion controller.

**Acceptance:** End-to-end fixtures produce one connected trace across all
executed stages; mandatory attributes are present in 100% of spans; privacy tests
prove prompts, secrets, and protected evidence are absent unless an authorized
debug policy explicitly opts in.

## 7. Cross-cutting acceptance gates

An implementation unit is complete only when all applicable gates below pass.

### 7.1 Evaluation design

- Freeze train, development, holdout, and adversarial splits by digest.
- Run deterministic oracles before model judges.
- For stochastic behavior, use at least three seeds and publish per-case results,
  dispersion, and failed-run counts.
- Blind candidate identity and swap presentation order for pairwise model
  judgments.
- Report results by domain, risk, source class, model, and failure mode.
- Scan exact and near-duplicate contamination before opening the holdout.
- Treat missing, timed-out, or invalid cases as failures unless the metric
  explicitly defines another predeclared rule.
- Preserve negative results and rejected candidates.

### 7.2 Security and privacy

- All network access passes SSRF, domain, scheme, size, content-type, timeout,
  and rate policy.
- All retrieved/model/tool content stays in untrusted data channels.
- Tools run with minimum capabilities in a namespaced sandbox.
- Secrets and personal data are redacted before model or dataset use.
- Project and tenant isolation is default deny.
- Dataset licenses, retention, deletion, and training permission are enforced.
- High-risk actions and promotions require separate human authority.

### 7.3 Zero-downtime delivery

- Add schemas, tables, fields, and APIs compatibly before switching readers.
- Feature flags default off; shadow output is non-authoritative.
- Dual-read/write periods have reconciliation metrics and a removal plan.
- Canary selection uses a versioned pointer, not in-place artifact replacement.
- The previous champion remains runnable until post-rollout observation passes.
- Rollback is exercised under load and requires no destructive migration.

### 7.4 Quality and coverage

- New or changed code aggregate line coverage is at least 85%.
- Every individual new or changed source file is at least 75%.
- Type checking, linting, unit, integration, security, injection, replay,
  resource, compatibility, and rollback tests are green.
- Tests may change only to match this specification, never merely to hide an
  implementation defect.
- Warnings, dependency-update informational messages, and deprecations have an
  actionable remediation or a dated, owned policy record.

## 8. Failure behavior

| Failure | Required behavior |
|---|---|
| No adequate evidence | Return `partial` or `abstained`; list missing evidence and safe next checks |
| Sources disagree | Preserve both positions, provenance, and the basis for any qualified inference |
| Citation cannot be resolved | Mark the affected claim unverified; never emit a fabricated replacement |
| Retriever misses gold evidence | Fail retrieval gate independently of answer fluency |
| Tool unavailable or unauthorized | Record typed failure; do not simulate a result |
| Budget or deadline exhausted | Cancel bounded work, preserve partial trace, and return partial/abstained |
| Prompt injection detected | Quarantine the content, log evidence ID, and continue only if safe evidence remains |
| Calibration stale or out of domain | Suppress verified/high-confidence status and require revalidation |
| Candidate improves quality but regresses safety | Reject promotion |
| Promotion evidence is inconclusive | Keep champion and return `needs_review` |
| Canary breach | Roll back within 60 seconds and open an incident with trace/evidence IDs |
| Optional OSS adapter fails | Use declared local fallback or return typed unavailable state |
| Provenance store unavailable | Do not issue a verified answer or promote a candidate |

## 9. Delivery order

Implementation MUST land on `development` through small, independently tested
feature branches in this order:

1. typed schemas, project/tenant isolation, untrusted-content boundary, and
   source policy (`MLCORE.1`, `.2`, `.8`, `.9`, `.10`);
2. evidence broker, hybrid retrieval, provenance, and retrieval evaluation
   (`MLAI.4`–`.6`, `MLCORE.3`, `.4`, `.11`);
3. collection skeleton, intake, decomposition, synthesis, calibration, tools,
   verifier, routing, and reporting (`MLAI.1`–`.3`, `.7`–`.15`);
4. immutable experiment/outcome infrastructure and isolated workspace
   (`MLSI.1`–`.5`, `MLCORE.5`–`.7`, `.13`–`.15`);
5. promotion, ZDD rollout, rollback, bias, privacy, and authority controls
   (`MLSI.6`–`.12`, `MLCORE.12`);
6. shadow evaluation and a disabled-by-default canary before any production
   authority is granted.

Shared infrastructure has one writer at a time. A feature lands on one branch
first and is then merged; it must not be independently recreated on multiple
branches.

## 10. Required implementation evidence

For each atomic ID, its implementation record MUST contain:

- code and test paths;
- the first failing test and final passing test evidence;
- exact evaluation suite and dataset digests;
- coverage for every changed source file;
- threat cases and resource-limit results;
- rollout/rollback evidence when behavior can affect a running system;
- documentation and source-registry changes;
- commit, branch, CI run, and artifact digests; and
- known limitations and an owner/review date.

Until those fields exist and the relevant gate is green, the unit remains
unimplemented regardless of partial code or prose.
