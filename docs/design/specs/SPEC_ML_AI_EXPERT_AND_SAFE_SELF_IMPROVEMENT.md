# SPEC — ML/AI expert collection and safe self-improvement

Status: READY-TO-IMPLEMENT (2026-08-14)

**Feature ID:** MLAI-CONTINUAL-v1  
**Target compatibility:** Gludd `0.1.x`; expert, evidence, experiment, and
promotion schemas `1.x` with N/N-1 readers  
**Specification date:** 2026-07-28  
**Implementation status:** Not implemented; every numbered requirement below is
an atomic feature specification, not a claim about current behavior.  
**Research basis:**
[`ML_AI_EXPERT_SYSTEM_RESEARCH_2026-07-28.md`](../../research/ML_AI_EXPERT_SYSTEM_RESEARCH_2026-07-28.md)

This specification is authoritative for ML/AI collection, role, skill, and safe
continual-improvement implementation. The broader
[`FEATURE_AI_ML_EXPERT.md`](../../specs/FEATURE_AI_ML_EXPERT.md) remains
authoritative for the public product surface and domain capability inventory.
Implementations satisfy both; incompatible ambiguity fails closed until the
documents are reconciled by an additive versioned change.

## 0. Objective and non-goals

Gludd needs an expert collection that can answer ML/AI questions and derive
solutions by combining current evidence, appropriate tools, repeatable
experiments, and independent verification. It also needs a safe way to turn its
own observed failures into candidate improvements without editing or grading
the live system in place.

The same collection must run bounded continual horizon scans across papers,
standards, official documentation, code/releases/issues, practitioner forums,
archives, and local outcomes; reproduce its complete deep-research process;
detect evidence-backed gaps; maintain a human-governed research agenda; and
synthesize new Gludd core, collection, role, and skill proposals. These are
proposal capabilities only. Any later self-improvement remains isolated,
independently evaluated, explicitly human-authorized, zero-downtime, and
reversible.

No implementation can guarantee an answer to every possible question. The
enforceable promise is:

1. decompose a question into answerable claims and executable checks;
2. retrieve versioned, attributable evidence from a maintained source registry;
3. treat retrieved content as untrusted data;
4. use deterministic tools where they are more reliable than model recall;
5. expose evidence strength, uncertainty, conflicts, and abstentions;
6. reproduce every material experiment and cited claim; and
7. preserve negative evidence and turn validated gaps into reviewable proposals;
   and
8. promote self-improvements only after independent, held-out evaluation and
   explicit human authority.

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

Continual research and self-improvement use separate control planes:

```text
source-registry refresh -> horizon scan -> immutable signal/evidence ledger
                                  |                  |
immutable outcomes -> knowledge-gap map -> ranked research agenda
                                              |
                           answer / Gludd proposal / collection proposal
                                              |
                                 isolated candidate workspace
                                              |
                 train/dev iteration -> frozen holdout + adversarial evaluation
                                              |
              blinded independent decision -> human approval -> shadow -> canary
                                                                |          |
                                                            champion <- rollback
```

The expert plane may read approved Gludd artifacts. The experiment plane may
write only inside its project workspace and artifact store. The promotion plane
may change a live selection pointer only through an authorized, audited,
reversible operation. Discovery output is evidence and a proposal, never
permission to edit a collection, role, skill, source policy, evaluator, or live
selection pointer.

### 2.1 Collection filesystem contract

The bundled collection MUST use the existing project/user/bundled Ansible
precedence and ship at:

```text
collections/ansible_collections/general_ludd/ml_ai_expert/
├── galaxy.yml
├── README.md
├── plugins/
│   ├── modules/                 # thin typed adapters, never prompt-only policy
│   └── module_utils/
│       ├── contracts.py         # answer/evidence/handoff/resource schemas
│       ├── datasets.py          # logical dataset + format adapter protocols
│       ├── adapters.py          # PEFT artifact/compatibility protocols
│       └── verification.py      # proof/check/evaluation result protocols
├── roles/
│   ├── expert_orchestrate/
│   ├── horizon_scan/
│   ├── source_discover/
│   ├── web_research/
│   ├── literature_research/
│   ├── gap_analyze/
│   ├── research_agenda/
│   ├── improvement_propose/
│   ├── knowledge_curate/
│   ├── outcome_learn/
│   ├── data_engineering/
│   ├── model_adaptation/
│   ├── adapter_audit/
│   ├── adapter_route/
│   ├── retrieval_engineering/
│   ├── retrieval_control/
│   ├── reasoning_verify/
│   ├── process_verify/
│   ├── vision_analyze/
│   ├── image_generate/
│   ├── image_edit/
│   ├── media_verify/
│   ├── math_solve/
│   ├── theorem_prove/
│   ├── science_discovery/
│   ├── research_reproduce/
│   ├── evolution_curate/
│   ├── eval_design/
│   ├── safety_review/
│   ├── answer_synthesize/
│   └── independent_verify/
└── tests/
    ├── unit/
    ├── integration/
    └── molecule/
```

Project-local roles may extend or intentionally shadow the collection through
the existing precedence contract. They may not weaken hard security,
provenance, holdout, or promotion policy.

### 2.2 Role execution contracts

Roles are typed execution units. Skills are reusable instructions and method
knowledge. A skill may help a role plan, but it cannot grant a tool, capability,
budget, network destination, promotion right, or secret.

| Role | Input | Output | Minimum capabilities |
|---|---|---|---|
| `expert_orchestrate` | Question, project, user constraints | Bounded role DAG and answer envelope | Registry read and dispatch only |
| `horizon_scan` | Versioned scope, source registry, watermark, budget | New/changed signal records and coverage report | Approved public source read; signal-ledger append only |
| `source_discover` | Coverage gaps, typed links, citations, and approved discovery surfaces | Candidate-source manifests and probe reports | Approved public discovery/probe access; candidate ledger append only |
| `web_research` | Query plan and source policy | Query ledger and evidence IDs | Search/fetch approved public sources |
| `literature_research` | Concepts, identifiers, date/venue rules | Deduplicated evidence graph and review log | Scholarly metadata/full-text adapters |
| `gap_analyze` | Claim/evidence graph, outcomes, unresolved questions | Evidence-linked gap map and uncertainty report | Read-only evidence/outcome access |
| `research_agenda` | Signals, gaps, objectives, constraints | Ranked, diverse, human-reviewable agenda | Agenda-store write; no experiment or code mutation |
| `improvement_propose` | Accepted agenda item and Gludd capability map | Core/collection/role/skill proposal plus eval plan | Isolated proposal workspace write only |
| `knowledge_curate` | Candidate source/claim graph and accepted policy | Validated candidate knowledge snapshot and reconciliation report | Candidate knowledge namespace write; no live pointer |
| `outcome_learn` | Exposure, intervention, outcome, and confounder records | Causal/uncertainty report and bounded candidate-memory proposals | Outcome-ledger read and candidate-memory write only |
| `data_engineering` | Data resources and target logical schema | Validated dataset manifest and conversion artifacts | Project dataset read/write |
| `model_adaptation` | Base/adapter/dataset/eval manifests | Isolated candidate adapter and training report | Authorized accelerator/model read; candidate workspace write |
| `adapter_audit` | Frozen base/adapter/runtime manifests and capability suites | Trainability, activation, merge-equivalence, and forgetting report | Read-only model/eval sandbox; no training, merge, route, or promotion write |
| `adapter_route` | Request features and approved adapter registry | Reproducible base/adapter route decision | Registry/model gateway read |
| `retrieval_engineering` | Corpus/query/eval manifests | Versioned index and retrieval report | Project knowledge-store read/write |
| `retrieval_control` | Atomic claims, search ledger, coverage, and budget | Typed next-query/source action or evidence-sufficiency stop record | Approved query dispatch and search-ledger append only |
| `reasoning_verify` | Subproblem DAG and candidate derivation | Checkable steps, tool artifacts, and verification state | Declared solvers only |
| `process_verify` | Frozen reasoning graph and verifier suite | First-error, calibration, perturbation, and hacking-resistance report | Read-only candidate and verifier access; no generator or reward mutation |
| `vision_analyze` | Media ingredients and question | Spatial/temporal observations with locators | Media decode and approved vision tools |
| `image_generate` | Prompt/conditions/policy | New media artifact plus provenance manifest | Authorized image backend and artifact write |
| `image_edit` | Source asset, region/instruction, policy | Edited artifact and ingredient graph | Authorized image backend and artifact write |
| `media_verify` | Source/condition/output transform graph and edit intent | Geometry, requested-change, protected-region, artifact, and provenance report | Read-only media transforms/checkers; no generation or publication |
| `math_solve` | Formalized mathematical problem | Exact/numeric result with assumptions and checks | Calculator/CAS/SMT sandbox |
| `theorem_prove` | Natural/formal statement and pinned environment | Kernel result and proof artifact | Formal prover sandbox |
| `science_discovery` | Domain, hypothesis, data, preregistration | Proposal or bounded computational experiment | Project sandbox; no physical action by default |
| `research_reproduce` | Paper claims, repository, data, environment, and rubric | Executed artifacts and claim-by-claim reproduction report | Isolated computational sandbox; no physical action, promotion, or source-policy write |
| `evolution_curate` | Candidate lineage, outcomes, capabilities, and budgets | Pareto archive, regression debt, parent-selection, and stop report | Read-only candidate/eval archive; no candidate, evaluator, or champion mutation |
| `eval_design` | Capability claim and risk | Versioned suite/card/threshold proposal | Eval registry write, no promotion |
| `safety_review` | Proposed plan/artifacts | Allow, constrain, review, or deny record | Policy read and approval request |
| `answer_synthesize` | Admitted claims/evidence/artifacts | Private-CoT-safe user report | No mutation or new external action |
| `independent_verify` | Frozen answer/candidate manifest | Verification report | Read-only evidence/tools; separately configured evaluator |

Every role receives an idempotency key, deadline, resource profile, capability
token, input schema version, and policy revision. Every role returns a typed
terminal state: `succeeded`, `partial`, `abstained`, `blocked`, `failed`, or
`cancelled`.

### 2.3 Skill package contract

The collection installs guidance through Gludd's existing `SkillRegistry`. The
initial skill set is:

| Skill | Purpose | Typical role consumers |
|---|---|---|
| `ml_question_triage` | Domain/risk/input clarification | `expert_orchestrate`, `safety_review` |
| `ml_method_select` | Baselines, assumptions, method cards | `expert_orchestrate`, `math_solve` |
| `horizon_scanning` | Reproducible weak-signal discovery, deduplication, and change detection | `horizon_scan` |
| `source_onboarding` | Discover, identify, probe, compare, quarantine, and evaluate candidate Internet sources | `source_discover`, `horizon_scan`, `safety_review` |
| `web_evidence_search` | Reproducible query/source/snowballing protocol | Research roles |
| `deep_research_replay` | Search, fetch, screening, extraction, contradiction, and synthesis ledger | Research roles, `independent_verify` |
| `knowledge_gap_map` | Unsupported/contradicted/stale claim and outcome-gap analysis | `gap_analyze`, `research_agenda` |
| `research_agenda` | Novelty, impact, feasibility, risk, diversity, and negative-evidence ranking | `research_agenda` |
| `capability_evolution` | Core/collection/role/skill proposal and compatibility protocol | `improvement_propose`, `eval_design` |
| `citation_audit` | Atomic-claim support, entailment, completeness, independence, and locator checks | `answer_synthesize`, `independent_verify` |
| `temporal_knowledge` | Valid-time/observation-time, supersession, staleness, and as-of queries | Research and curation roles |
| `outcome_learning` | Exposure logging, causal attribution, delayed outcomes, and feedback-loop controls | `outcome_learn`, `eval_design` |
| `dataset_contract` | Schema, formats, lineage, quality, split rules | `data_engineering`, `eval_design` |
| `peft_experiment` | LoRA/QLoRA/DoRA/adapter comparison checklist | `model_adaptation` |
| `adapter_equivalence` | Trainability, activation, dtype, merge/hotswap, and forgetting audit | `adapter_audit`, `model_adaptation`, `adapter_route` |
| `adapter_route` | Compatibility, composition, serving, rollback checklist | `adapter_route` |
| `hybrid_retrieval` | Lexical/dense/graph/fusion/reranker workflow | `retrieval_engineering` |
| `evidence_sufficiency` | Atomic-claim coverage, information-gain query choice, saturation, and stop protocol | `retrieval_control`, `web_research`, `independent_verify` |
| `verifiable_reasoning` | Externalized assumptions, tools, proof state | `reasoning_verify`, `math_solve` |
| `process_verifier_audit` | Error localization, calibration, metamorphic checks, and reward-hacking resistance | `process_verify`, `eval_design` |
| `private_reasoning_boundary` | Produce concise verification records without raw private CoT | All model-backed roles |
| `vision_evidence` | Regions, timestamps, OCR/caption lineage | `vision_analyze` |
| `image_generation` | Pipeline components, seeds, safety, provenance | `image_generate` |
| `image_editing` | Ingredients, masks, transformations, identity/rights | `image_edit` |
| `media_transform_trace` | Orientation, coordinate, mask, color, crop/resize, latent, and compositing invariants | `image_edit`, `vision_analyze`, `media_verify` |
| `formal_proof` | Natural/formal translation and kernel validation | `theorem_prove` |
| `scientific_method` | Hypothesis, controls, units, statistics, replication | `science_discovery` |
| `scientific_reproduction` | Environment restoration, execution, output extraction, and claim/result reconciliation | `research_reproduce`, `independent_verify` |
| `evaluation_card` | Claims, cases, metrics, slices, thresholds, limits | `eval_design`, `independent_verify` |
| `safe_self_improvement` | Candidate isolation and promotion evidence | Self-improvement roles |
| `evolution_lineage` | Multi-generation Pareto archive, regression debt, evaluator-cohort, and stopping protocol | `evolution_curate`, `improvement_propose`, `independent_verify` |

The existing `Skill` model MUST be extended with `schema_version`,
`input_schema`, `output_schema`, `required_capabilities`, `resource_profile`,
`evidence_policy`, `evaluation_suite`, `version`, and `content_digest`.
Unknown fields fail validation. Remote skills remain untrusted until source,
signature/digest, policy, schema, and capability review pass.

### 2.4 Handoff envelope

No role passes free-form text as the sole handoff. The envelope is:

```yaml
handoff_id: uuid
parent_run_id: uuid
from_role: string
to_role: string
schema_version: 1
objective: string
inputs: [{artifact_id: string, digest: sha256}]
claims: [claim_id]
evidence: [evidence_id]
assumptions: [string]
required_checks: [string]
capability_token_id: string
resource_profile_id: string
policy_revision: sha256
deadline: timestamp
idempotency_key: string
```

The receiver resolves IDs from authoritative stores and rejects missing,
cross-tenant, digest-mismatched, expired, unauthorized, or cyclic handoffs.

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
    citation_quality:
      support_coverage: 0.0
      entailment: supported|partial|unsupported|contradicted|unknown
      source_independence_groups: [string]
      locator_valid: boolean
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
observed_at: timestamp
valid_from: timestamp|null
valid_to: timestamp|null
stale_after: timestamp|null
version: string|null
content_digest: sha256
locator: string
license: string|null
integrity: active|corrected|retracted|withdrawn|unknown
trust_label: primary|secondary|practitioner_signal|local_observation
independence_group: string
supersedes: [evidence_id]
superseded_by: [evidence_id]
project_id: string
tenant_id: string
```

Evidence content never enters an instruction channel. A source's reputation
changes retrieval priority, not whether a claim is automatically true.
`observed_at` records when Gludd captured the assertion; `valid_from` and
`valid_to` record when the assertion is claimed to hold in its domain. Unknown
validity remains null/unknown. A correction or later fact creates a new evidence
record and supersession edge; it never rewrites the historical observation.

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

### 3.5 Internet source registry and fallbacks

Each source adapter declares source class, authoritative identifiers, trust
label, supported operations, authentication, terms/license, robots behavior,
rate/concurrency limits, cache policy, freshness, maximum object size, parser,
health state, and fallback group.

An Internet resource absent from the champion registry is a **candidate source**,
not an approved source. Discovery writes a separate candidate manifest containing
its discovery path, canonical owner/identity, source class, unique coverage claim,
typed service or repository metadata, authentication needs, terms/license,
robots result, privacy/retention constraints, rate/cost limits, parser/schema
fingerprint, correction/retraction support, independence group, security probes,
fallback/archive plan, and health evidence. Candidate discovery cannot mint a
secret, create an account, install executable code, expand an allowlist, or grant
a network/tool capability. Admission follows `MLCONT.29` and registry promotion
follows `MLCONT.8`.

| Need | Primary | Ordered fallback |
|---|---|---|
| General web discovery | Project SearXNG | Approved search API; named-site search; no implicit HTML scraping |
| Historical page | Internet Archive CDX | Common Crawl CDX/URL index |
| DOI/metadata | Crossref | DataCite; publisher record |
| Scholarly graph | OpenAlex | Semantic Scholar; OpenCitations; Crossref references |
| Preprint/review | arXiv/OpenReview | Venue proceedings; author repository |
| Biomedicine | PubMed/Europe PMC | PMC open text; publisher metadata |
| Computer science | DBLP and canonical proceedings | OpenAlex/Semantic Scholar |
| Code and issues | Canonical forge API | Archived repository snapshot |
| Retraction/correction | Publisher/Crossref relationship | Retraction Watch/venue notice |

Fallbacks are explicit trace events. A fallback may broaden coverage but may not
silently upgrade trust, freshness, peer-review state, license, or claim
verification. A cached or archived page is labeled with its observation date.

### 3.6 Operation resource profiles

Defaults are safe ceilings, not utilization targets:

| Profile | Default hard ceiling |
|---|---|
| `research_standard` | 12 queries, 40 documents, 2 concurrent fetches, 32k admitted tokens, 15 minutes |
| `horizon_scan` | 60 queries, 500 metadata records, 100 full texts, 2 concurrent fetches, 128k admitted tokens, 60 minutes |
| `source_onboarding` | 50 candidates, 10 bounded probes per candidate, 2 concurrent fetches, 60 minutes, no secret/account/package mutation |
| `knowledge_refresh` | 100k source revisions or 2 GiB input, 2 workers, 4 GiB RAM, 30 minutes, candidate namespace only |
| `outcome_analysis` | 100k outcome records, 2 CPU cores, 4 GiB RAM, 30 minutes, no external network or accelerator |
| `retrieval_build` | 100k records or 2 GiB input, 2 workers, 4 GiB RAM, 30 minutes |
| `retrieval_query` | 200 lexical + 200 dense candidates, 500 graph nodes/2 hops, rerank 50, admit 20 |
| `dataset_preview` | 100k records or 2 GiB, 4 GiB RAM, 30 minutes, no accelerator |
| `peft_preview` | 1,000 examples, rank at most 16, trainable parameters at most 2%, one accelerator, 60 minutes |
| `media_standard` | Four outputs, 1024×1024 maximum each, two control/reference assets, 15 minutes |
| `formal_proof` | 1,000 prover actions, 4 GiB RAM, 10 minutes per theorem |
| `science_compute` | 4 CPU cores, 8 GiB RAM, 10 GiB artifacts, 60 minutes, no physical device |

Larger profiles require an explicit project policy and admission-control
decision. No role can increase its own profile. Admission accounts for release
priority and total system resources before work starts.

## 4. Expert collection specifications

### MLAI.1 — Versioned expert collection skeleton

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

**Contract:** Decompose a request into atomic factual claims, calculations,
experiments, design decisions, and missing inputs. Preserve a dependency DAG and
assign every leaf an evidence or tool requirement.

**Primary seams:** New `ml_ai_expert/decomposition.py` and typed answer schema.

**Acceptance:** On 100 gold questions, at least 95% of gold-critical leaves are
represented; no executable leaf lacks a tool/evidence plan; cyclic dependency
fixtures fail deterministically.

### MLAI.4 — Evidence acquisition and source policy

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

**Contract:** Estimate claim and answer confidence using held-out calibration
data, evidence sufficiency, verifier agreement, and retrieval quality. Abstain or
return partial status when minimum evidence is absent.

**Primary seams:** New `eval/calibration.py`, answer schema, and expert policy.

**Acceptance:** Expected calibration error is at most 0.05 on the frozen suite;
unanswerable-case abstention recall is at least 0.90; citation-free factual
fixtures never receive `verified`; calibration metrics are split by domain and
risk.

### MLAI.9 — Tool-backed solution derivation

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

**Contract:** Route every self-improvement change through a dedicated project
workspace with its own branch, environment, artifacts, budget, and capability
profile. The running checkout and imported live modules are read-only.

**Primary seams:** Project workspace, git automation, self-improvement harness,
and daemon wiring.

**Acceptance:** Filesystem and import-hook tests block 100% of writes/reloads
targeting the running tree; concurrent experiments receive unique namespaces;
discarding an experiment leaves champion files and processes unchanged.

### MLSI.2 — Immutable observation and outcome ledger

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

**Contract:** Extract a reusable protocol from the compaction arena: immutable
champion, isolated challenger, declared metrics, paired cases, resource budgets,
and a typed outcome. Candidate iteration may use train/dev cases only.

**Primary seams:** `compaction/arena.py`, generalized eval harness, and
experiment registry.

**Acceptance:** Champion and challenger run the identical frozen harness and
case order; paired case IDs are complete; at least three seeds are used for
stochastic behavior unless determinism is proven and recorded.

### MLSI.5 — Frozen holdout and adversarial evaluator

**Status:** Not implemented

**Contract:** Freeze train, development, holdout, and adversarial splits with
content digests before candidate iteration. Candidate processes cannot read
holdout prompts, expected outputs, judge keys, or adversarial labels.

**Primary seams:** Benchmark harness, sandbox, secret store, and evaluator.

**Acceptance:** Capability tests deny all challenger reads of protected splits;
split overlap and near-duplicate scans are zero above the configured threshold;
holdout is opened only by the independent evaluation process after candidate
freeze.

### MLSI.6 — Statistical promotion gate

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

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

### MLSI.13 — Multi-generation improvement archive and regression-debt gate

**Status:** Not implemented

**Contract:** Maintain every self-improvement candidate as an immutable lineage
node containing parent/root/champion digests, exact diff and environment,
proposal/evidence/evaluation/approval links, full capability/safety/resource
vector, known regressions, and terminal state. `evolution_curate` preserves a
bounded Pareto-diverse archive and may recommend parents or stopping, but cannot
edit candidates, evaluators, metrics, policy, or champion state. Evaluate each
generation against its parent, current champion, and root on frozen old/new/
transfer/adversarial suites; carry unresolved regression debt forward visibly
and forbid promotion of a local gain that violates any hard invariant. When an
evaluator or benchmark revision changes, create a new immutable cohort and
backtest retained nodes rather than rewriting historical fitness. The open-ended
archives in [Darwin Gödel Machine](https://arxiv.org/abs/2505.22954) and
machine-gradable evaluator boundary in
[AlphaEvolve](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)
are experimental patterns, not authority for recursive live modification.

**Primary seams:** Self-improvement harness, experiment/evaluation registries,
artifact/provenance stores, candidate scheduler, and promotion controller.

**Acceptance:** Fixtures cover local benchmark gain with root capability loss,
safety/resource regression, ancestor-only useful capability, duplicate/cyclic or
broken lineage, archive collapse to one family, novelty without quality,
benchmark contamination/overfit, evaluator/rubric revision, incomparable metric,
stale parent, rejected ancestor, hidden regression debt, runaway depth/branch/
cost, stagnation, restart, and rollback. Archive pruning preserves champion,
last-known-good, Pareto boundary, required ancestors, negative evidence, and
audit lineage; deterministic replay returns the same cohort/rank/stop record;
no number of successful generations can relax human approval or the
`MLCONT.31` promotion protocol.

## 6. Reusable core-system specifications

### MLCORE.1 — Typed evidence broker

**Status:** Not implemented

**Contract:** Introduce one protocol for discovery, fetch, parse, normalize,
version, and citation location across web, scholarly, repository, local, and
artifact sources. Adapters return typed failures and never raw policy decisions.

**Primary seams:** Retrieval package and researcher agent.

**Acceptance:** Contract tests run against every adapter; equivalent canonical
content produces the same digest; timeouts, parse failures, and policy denials
remain distinguishable in traces.

### MLCORE.2 — Declarative source-policy registry

**Status:** Not implemented

**Contract:** Store source class, trust label, allowed domains/schemes/types,
freshness, rate, size, license, integrity, and corroboration requirements in a
versioned project policy. Hard security constraints cannot be relaxed by model
output.

**Primary seams:** Config models, evidence broker, and capability lattice.

**Acceptance:** Invalid/unknown policy fields fail startup validation; policy
resolution is deterministic across global/collection/project precedence; 100%
of evidence records include the policy revision that admitted them.

### MLCORE.3 — Hybrid retrieval and reranking protocol

**Status:** Not implemented

**Contract:** Provide lexical, vector, filter, fusion, and reranker adapters with
stable query/result schemas, explicit score semantics, and an offline evaluator.
No one backend becomes a core dependency.

**Primary seams:** Retrieval and memory packages.

**Acceptance:** Each adapter passes the same conformance suite; score fusion is
deterministic under tied inputs; lexical-only mode passes core tests without
network or accelerator access.

### MLCORE.4 — Versioned evaluation registry

**Status:** Not implemented

**Contract:** Register evaluation suites, cases, slices, metrics, rubrics,
oracles, datasets, and owners by immutable digest. Suites declare intended use,
limitations, license, contamination policy, and minimum sample size.

**Primary seams:** Eval harness, benchmark harness, and artifact store.

**Acceptance:** Missing cards or mutable case digests fail registration; results
cannot compare incompatible metric versions without an explicit migration;
every reported aggregate links to per-case records.

### MLCORE.5 — Calibration and selective prediction service

**Status:** Not implemented

**Contract:** Offer reusable reliability diagrams, ECE/Brier/selective-risk
metrics, threshold fitting, abstention policies, and domain/risk slices. Never
reuse calibration across materially changed models, prompts, tools, or
distributions without revalidation.

**Primary seams:** New eval calibration module and model/run metadata.

**Acceptance:** Reference datasets reproduce known metrics within `1e-6`;
calibration revision mismatches fail closed; drift beyond a declared threshold
marks confidence stale and disables automatic high-risk use.

### MLCORE.6 — Immutable experiment registry

**Status:** Not implemented

**Contract:** Record parameters, inputs, environment, code, models, artifacts,
metrics, and lineage behind an adapter protocol compatible with local storage
and mature trackers such as MLflow. Gludd policy remains authoritative.

**Primary seams:** Artifact store, run recorder, optional MLflow adapter.

**Acceptance:** Re-registering an identical manifest is idempotent; changing any
material input produces a new digest; optional tracker failure does not erase
the local authoritative record.

### MLCORE.7 — Sanitized trajectory dataset builder

**Status:** Not implemented

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

**Status:** Not implemented

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

**Status:** Not implemented

**Contract:** Namespace research indexes, embeddings, procedures, experiments,
and caches by project and tenant with explicit, audited sharing rules. Remove
implicit user-global knowledge paths from production defaults.

**Primary seams:** Research index, embedding store, procedural memory, and
project workspace.

**Acceptance:** Cross-tenant read/write fixtures are blocked in 100% of cases;
cache keys include tenant, project, policy revision, and content digest; an
export/import operation preserves ownership and provenance.

### MLCORE.10 — Capability- and risk-aware action router

**Status:** Not implemented

**Contract:** Resolve requested action, data sensitivity, risk, tool capability,
agent delegation, and approval into an allow/deny/clarify decision before model
or tool execution. Split broad research/self-improve permissions into minimum
scopes.

**Primary seams:** Capability lattice, role registry, approvals, and router.

**Acceptance:** The decision table has complete deny-by-default coverage;
property tests never produce an allow with a missing capability; delegated roles
cannot exceed the intersection of parent authority and project policy.

### MLCORE.11 — Provenance and replay API

**Status:** Not implemented

**Contract:** Expose authenticated APIs to resolve answer, claim, evidence,
artifact, experiment, evaluation, and promotion lineage and to request an
authorized replay. Raw secrets and protected holdout content remain inaccessible.

**Primary seams:** API routers, run recorder, artifact store, and audit log.

**Acceptance:** Every public answer ID resolves to a complete non-secret lineage
graph; authorization tests cover tenant, role, holdout, and deleted data; replay
reports exact and non-exact components before execution.

### MLCORE.12 — Drift and feedback-loop monitor

**Status:** Not implemented

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

**Status:** Not implemented

**Contract:** Provide shared admission control, priority, namespace, concurrency,
deadline, cancellation, and resource accounting for research, evaluation, and
experiments. Release work remains higher priority than background research.

**Primary seams:** Scheduler, resource monitor, project registry, and model spend
guard.

**Acceptance:** Priority tests show release-critical work admitted before
background expert refreshes; cancellations release leases/resources; per-project
and system caps remain enforced during concurrent stress.

### MLCORE.14 — Mature-tool adapter and dependency policy

**Status:** Not implemented

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

**Status:** Not implemented

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

### MLCORE.16 — Typed transformation and equivalence protocol

**Status:** Not implemented

**Contract:** Introduce one immutable manifest for any operation that changes an
artifact representation while claiming to preserve all or part of its meaning.
Record input/output digests, ordered algorithm/library/environment revisions,
parameters, randomness, dtype/quantization, shape and coordinate/token/module
maps, expected irreversible loss, declared invariants, equivalence level
(`byte_exact`, `numerically_bounded`, `task_equivalent`, or `derived_candidate`),
tolerances, and independent checker results. Use it for adapter merge/unmerge and
tokenizer changes, image orientation/crop/resize/color/mask/latent transforms,
dataset conversion, retrieval chunk/embed/index migrations, and scientific
result extraction. This adapts the invariant checks exposed by current
[PEFT troubleshooting](https://huggingface.co/docs/peft/developer_guides/troubleshooting)
and image-transform pipelines into a shared Gludd contract rather than assuming
that a successful conversion preserved behavior.

**Primary seams:** Artifact store, run recorder, adapter/media/dataset/retrieval
protocols, provenance API, and independent verifier topology.

**Acceptance:** Table-driven conformance fixtures cover exact copy, lossy dtype
cast, quantize/dequantize, adapter merge, tokenizer/embedding resize, EXIF
orientation, crop/pad/resize and mask interpolation, color/alpha conversion,
dataset schema cast, embedding-dimension change, chunker/parser revision, and
paper table/figure extraction. Missing maps/checkers, undeclared loss,
out-of-tolerance results, nondeterministic order, stale environment, and
round-trip failure cannot claim equivalence or replace an accepted artifact;
every derived output resolves its full transform graph and failed checks.

## 7. Deep capability specifications

### 7.1 Collection, role, and skill control plane

### MLARCH.1 — Collection structure and precedence

**Status:** Not implemented

**Contract:** Implement the filesystem contract in section 2.1 as the bundled
`general_ludd.ml_ai_expert` collection and resolve project/user/bundled
overrides through the existing collection precedence.

**Acceptance:** Role/FQCN resolution tests cover all three tiers, deliberate
shadowing, missing tiers, and concurrent project switches; a project override
cannot replace hard policy or verifier modules.

### MLARCH.2 — Typed role registry

**Status:** Not implemented

**Contract:** Register every section 2.2 role with input/output schema,
capabilities, allowed children, terminal states, default resource profile, and
independence class.

**Acceptance:** Unknown roles/fields/states and capability escalation fail
closed; registry snapshots replay identical route decisions; every role has unit
and Molecule coverage.

### MLARCH.3 — Versioned skill metadata

**Status:** Not implemented

**Contract:** Extend `Skill` and its loader with the section 2.3 metadata while
preserving project precedence. Skill text is guidance-only and all requested
tools/capabilities are intersected with role and project policy.

**Acceptance:** Strict schema, digest, remote-skill injection, missing variable,
cross-project shadow, and unauthorized-tool tests pass; legacy skills migrate
additively and continue to load with an explicit legacy version.

### MLARCH.4 — Bounded orchestration state machine

**Status:** Not implemented

**Contract:** `expert_orchestrate` constructs a DAG of roles and checks with
explicit iteration, branching, deadline, token, action, and cost ceilings.
Transitions are code-controlled rather than inferred from prose.

**Acceptance:** Property tests prove no cycles, orphan nodes, post-terminal
actions, or ceiling overruns; repeated-state/action/output triples terminate
with a typed loop-detected result.

### MLARCH.5 — Typed handoff and artifact resolution

**Status:** Not implemented

**Contract:** Use the section 2.4 envelope for every delegation. Resolve content
by ID/digest from authoritative stores instead of embedding opaque free-form
state.

**Acceptance:** Missing, expired, tampered, cross-tenant, cyclic, replayed, and
over-budget envelopes are rejected in 100% of fixtures; idempotent replay
returns the prior terminal result.

### MLARCH.6 — Independent verifier topology

**Status:** Not implemented

**Contract:** Configure generator, deterministic checker, semantic judge, safety
reviewer, and promotion authority as distinct independence classes. No route may
collapse them into one sole model/process for high-risk work.

**Acceptance:** Topology validation blocks shared sole-judge configurations;
failure of any required independent component yields `unverified` or
`needs_review`, never an automatic answer/promotion.

### 7.2 Parameter-efficient adaptation and adapter routing

### MLPEFT.1 — Base and adapter compatibility manifest

**Status:** Not implemented

**Contract:** Key every adapter to base-weight, config, architecture, tokenizer,
vocabulary, chat-template, quantization, target-module-map, library, and
environment digests.

**Acceptance:** One-field mutation fixtures are rejected; no mutable branch name
or human model label satisfies compatibility; accepted loads reproduce a golden
logit/output tolerance suite.

### MLPEFT.2 — PEFT method experiment planner

**Status:** Not implemented

**Contract:** Compare no-adaptation, prompting/RAG, LoRA, QLoRA, DoRA, and other
approved PEFT methods under identical splits and budgets before selecting one.
Full fine-tuning is a separately authorized profile.

**Acceptance:** Plans missing a baseline, method-specific parameters, memory
estimate, held-out suite, license/privacy check, or rollback are rejected; the
default preview obeys section 3.6.

### MLPEFT.3 — Isolated adapter training runner

**Status:** Not implemented

**Contract:** Run PEFT through an optional Hugging Face PEFT adapter inside a
candidate workspace, pinned environment, dataset manifest, resource cgroup, and
continuous progress reporting.

**Acceptance:** Timeout/OOM/cancellation leaves the base and champion unchanged;
peak CPU/RAM/VRAM/disk, examples, tokens, optimizer state, checkpoints, and
warnings are captured; no live-tree import reload occurs.

### MLPEFT.4 — QLoRA and DoRA compatibility checks

**Status:** Not implemented

**Contract:** Validate quantizer, dtype, hardware kernels, sharding/ZeRO/FSDP,
supported layer types, offload, merge, and serving backend before QLoRA, QDoRA,
or DoRA execution.

**Acceptance:** Maintained incompatible fixtures—including the declared
QDoRA/ZeRO-2 case—fail preflight; supported matrices run smoke, resume, merge,
unmerge, and inference-equivalence tests.

### MLPEFT.5 — Safe adapter artifact registry

**Status:** Not implemented

**Contract:** Store adapters in `safetensors` by default with config, model/data
cards, signatures/digests, origin, license, metrics, base dependencies, status,
and deletion/retention policy. Pickle-backed weights are denied by default.

**Acceptance:** Truncated, oversized, malformed, executable, wrong-shape,
wrong-dtype, unsigned-when-required, revoked, or incompatible artifacts never
load; registry events are append-only and tenant scoped.

### MLPEFT.6 — Adapter composition evaluation

**Status:** Not implemented

**Contract:** Treat stacking, fusion, weighted merge, LoRAHub-like search, and
mixture-of-adapter experts as new candidates with explicit composition graph,
weights/router, order, and constituent digests.

**Acceptance:** Every composition is evaluated against base and each constituent
on in-domain, out-of-domain, conflict, and safety slices; weight/order changes
produce new artifact IDs; no circular composition is allowed.

### MLPEFT.7 — Request-level adapter router

**Status:** Not implemented

**Contract:** Route only among compatible, approved adapters using task/domain,
risk, measured quality, calibration, health, latency, and memory. Preserve base
fallback and expose the route decision.

**Acceptance:** A frozen 10,000-request simulation is deterministic and never
selects incompatible/revoked/cross-tenant adapters; uncertain or out-of-domain
requests use base/abstain rather than an arbitrary specialist.

### MLPEFT.8 — Multi-adapter serving and rollback

**Status:** Not implemented

**Contract:** Serve approved adapters through the existing model gateway with an
optional vLLM backend and bounded adapter/KV memory, homogeneous batching where
useful, atomic selection pointers, canary, and prior-version rollback.

**Acceptance:** Load/unload/switch/merge concurrency tests show no request uses a
partial or wrong adapter; per-request lineage is complete; rollback completes in
60 seconds without reloading the base.

### MLPEFT.9 — Adapter trainability, activation, merge, and forgetting audit

**Status:** Not implemented

**Contract:** Before training and every load, hotswap, composition, merge, or
unmerge, have `adapter_audit` freeze and compare base/adapter/runtime state:
targeted and actually executed modules, trainable parameter and optimizer
identities, update/delta norms, active/available/merged adapters per layer,
irregular layer state, dtype and quantization casts, device/offload, train/eval
mode, tokenizer/vocabulary/chat template, embeddings and task heads, compile
state, and deterministic logits/task outputs. Emit an `MLCORE.16` equivalence
manifest and run old-task, new-task, transfer, safety, calibration, and resource
suites so PEFT efficiency cannot hide a silent no-op, merge drift, or capability
forgetting. Current [PEFT status/troubleshooting guidance](https://huggingface.co/docs/peft/developer_guides/troubleshooting)
and [model-merging constraints](https://huggingface.co/docs/peft/developer_guides/model_merging)
are adapter inputs, not substitutes for measured equivalence.

**Acceptance:** Fixtures cover a stale optimizer reference that performs no
update, a configured target module never reached in forward execution, wrong or
disabled adapter, irregular per-layer active state, missing classification head
or resized embedding, dropout/sampling mismatch, fp32-adapter to bf16-base merge,
quantized merge/dequantize/requantize, compiled hotswap limit, target shape/rank
mismatch, sequential-task forgetting, and base/active/merged output divergence.
Every unexpected state or tolerance/capability regression blocks publication and
serving; a supported case reproduces the declared logits and capability vector
before and after merge/unmerge or hotswap.

### 7.3 Dataset and format system

### MLDATA.1 — Logical dataset manifest

**Status:** Not implemented

**Contract:** Define dataset, resource, shard, record-ID, feature/label schema,
split, ordering, license, consent, sensitivity, lineage, transformation, and
quality metadata independently of storage format.

**Acceptance:** Every admitted record resolves to source resource and
transformation digests; unknown fields/types, duplicate IDs, absent licenses,
and cross-tenant lineage fail closed.

### MLDATA.2 — JSONL, Arrow, Parquet, and WebDataset adapters

**Status:** Not implemented

**Contract:** Implement conformance adapters for JSONL, exact Arrow variant,
Parquet, and optional WebDataset while preserving logical schema and IDs.

**Acceptance:** Round trips preserve values, nullability, logical types,
record/split IDs, ordering contract, and digests; cross-reader fixtures detect
Arrow IPC stream/file and encoding mismatches.

### MLDATA.3 — Streaming and sharding

**Status:** Not implemented

**Contract:** Stream with bounded buffers, deterministic shard enumeration,
checkpoint/resume, seeded shuffle semantics, backpressure, cancellation, and
per-shard validation.

**Acceptance:** Multi-terabyte synthetic manifests run bounded previews without
full materialization; RSS/file-descriptor ceilings hold; interrupted runs resume
without loss or duplication.

### MLDATA.4 — Schema and data-quality gates

**Status:** Not implemented

**Contract:** Validate types, ranges, units, nulls, referential constraints,
duplicates, encoding, corrupt media, label consistency, distribution/slice
statistics, and declared business rules before training/evaluation.

**Acceptance:** Seeded violations are detected at least 99% with false-positive
rate at most 1% on the conformance suite; invalid records are quarantined with
reason and lineage, never silently coerced.

### MLDATA.5 — Deduplication and contamination graph

**Status:** Not implemented

**Contract:** Scan exact, normalized, semantic, code, and media near-duplicates
across source, train, development, holdout, adversarial, model context, and
known benchmark sets.

**Acceptance:** All seeded exact and above-threshold near duplicates are found;
uncertain pairs are reviewable; contaminated cases are excluded or disclosed
before metrics run.

### MLDATA.6 — Privacy, license, and deletion lineage

**Status:** Not implemented

**Contract:** Enforce consent/use purpose, data classification, tenant,
retention, deletion, license compatibility, and export restrictions at record,
resource, derivative dataset, checkpoint, and adapter levels.

**Acceptance:** A deletion request computes the full derivative impact graph;
blocked records never enter a candidate; deletion/retraining exceptions require
an authorized auditable policy decision.

### MLDATA.7 — Dataset conversion and reproducible export

**Status:** Not implemented

**Contract:** Convert/export by immutable manifest with source/target format
versions, code/environment digest, shard plan, compression, checksums, rejected
records, and validation report.

**Acceptance:** Two deterministic conversions produce identical record and
logical dataset digests; format-specific byte differences are explained; failed
publishing cannot replace the prior accepted dataset pointer.

### 7.4 Web research, retrieval, graphs, and rerankers

### MLRET.1 — Policy-aware Internet source registry

**Status:** Not implemented

**Contract:** Implement section 3.5 as a versioned registry of adapters,
operations, identifiers, trust, terms, robots, rate/concurrency, cache,
freshness, parser, health, and fallbacks.

**Acceptance:** Every network request resolves one policy revision and source
entry; unregistered destinations, silent fallback, trust upgrades, and ignored
`Retry-After`/robots rules are blocked in 100% of fixtures.

### MLRET.2 — Reproducible multi-source search

**Status:** Not implemented

**Contract:** Expand identifiers/synonyms, query at least two independent
eligible indexes for material research, record raw ranked results, deduplicate,
and stop by budget or declared saturation.

**Acceptance:** Replay with stored responses reconstructs identical result IDs
and ranks; query/source omissions and failures remain visible; BrowseComp,
FRAMES, and Deep Research Bench adapters report per-source coverage and cost.

### MLRET.3 — Safe fetch, archive, and parser chain

**Status:** Not implemented

**Contract:** Fetch through SSRF/robots/terms/type/size/deadline controls, retain
original bytes and HTTP validators, optionally resolve archived versions, and
parse through versioned HTML/PDF/code/media adapters.

**Acceptance:** Private/link-local/rebinding/redirect-chain/zip-bomb/parser-bomb
fixtures are blocked; every extracted span maps to byte/source locator; parser
fallback never overwrites the original.

### MLRET.4 — Scholarly discovery and snowballing

**Status:** Not implemented

**Contract:** Resolve DOI/arXiv/venue/repository identities; traverse bounded
backward/forward citations, versions, reviews, corrections, retractions, code,
datasets, and negative/replication evidence.

**Acceptance:** A gold 100-work graph has at least 0.98 identity precision and
0.95 relation recall; duplicate preprint/published versions coalesce but remain
separately addressable; retractions cannot be sole verified support.

### MLRET.5 — Structure-preserving ingestion

**Status:** Not implemented

**Contract:** Preserve document/page/section/table/figure/code/citation
structure, stable chunk IDs, neighbors, source revision, permissions, language,
timestamps, and authoritative structured fields before indexing.

**Acceptance:** Gold HTML/PDF/notebook/repository fixtures retain at least 0.98
section/locator fidelity; parsing failures quarantine the document; derived rich
text never overrides newer authoritative structured state.

### MLRET.6 — Hybrid candidate retrieval

**Status:** Not implemented

**Contract:** Run eligible metadata filters, lexical/BM25, dense, learned sparse,
and optional late-interaction retrievers; retain component ranks/scores and
combine through a versioned fusion policy.

**Acceptance:** Exact lexical-only mode remains available; approximate-index
recall is checked against exact search; the gold corpus meets recall@10 at least
0.90 and reports each component plus fusion ablations.

### MLRET.7 — Provenance-preserving graph retrieval

**Status:** Not implemented

**Contract:** Build entities/relations/communities only as derived,
source-spanned records; support bounded traversal and graph-plus-text retrieval
without treating extraction confidence as truth.

**Acceptance:** Every edge resolves to one or more evidence locators; traversal
obeys tenant/permission/time filters, 500-node/two-hop default, and cycle limits;
edge deletion/revision propagates to indexes.

### MLRET.8 — Bounded reranking and context assembly

**Status:** Not implemented

**Contract:** Rerank a bounded candidate set using deterministic features,
cross-encoders, or separately configured model judges, then expand neighbors and
assemble evidence under diversity and token constraints.

**Acceptance:** Candidate-to-score mapping is permutation-safe; reranking cannot
recover a missed gold document or hide first-stage failure; top-50/default,
latency, memory, and model-version limits are enforced.

### MLRET.9 — Index freshness, tombstones, and reconciliation

**Status:** Not implemented

**Contract:** Track source revision, ingestion/index watermarks, pending
updates, tombstones, cache expiry, and authoritative-state reconciliation across
lexical, vector, graph, and summary indexes.

**Acceptance:** Create/update/delete fault injection converges every index to the
same revision; stale derived content is suppressed when authoritative state is
newer; unresolved lag is visible and prevents high-risk verified answers.

### MLRET.10 — Retrieval and web-research evaluation suite

**Status:** Not implemented

**Contract:** Evaluate discovery coverage, identity precision, parser fidelity,
recall/precision/MRR/nDCG, freshness, citation entailment, contradiction,
abstention, injection resistance, latency, tokens, network, storage, and cost.

**Acceptance:** Frozen normal/adversarial/live-refresh suites publish per-stage
and per-source results with confidence intervals; no end-answer metric can waive
a failed security, identity, freshness, or retrieval gate.

### MLRET.11 — Evidence-sufficiency-controlled agentic retrieval

**Status:** Not implemented

**Contract:** Let `retrieval_control` iteratively choose a typed next action
(`query`, `reformulate`, `switch_source`, `follow_relation`, `fetch`,
`seek_counterevidence`, or `stop`) from unresolved atomic claims, observed coverage,
source independence, retrieval failures, expected information gain, saturation,
and remaining budget. Search snippets/ranks are discovery leads, not admitted
evidence. Stop only when every required claim is supported, contradicted, or
explicitly `unknown`, or when a predeclared saturation/budget rule fires; preserve
why another query was or was not useful. Evaluate the controller separately from
retrievers and synthesis with both live time-sliced sources and a fixed,
human-verified corpus such as
[BrowseComp-Plus](https://arxiv.org/abs/2508.06600), whose controlled documents
and hard negatives expose black-box search and end-answer confounding.

**Acceptance:** Fixed-corpus and recorded-live fixtures require query
decomposition, exact identifier search, vocabulary expansion, alternate-source
selection, citation snowballing, negative evidence, and return to a previously
unresolved claim. Duplicate/rephrased loops, premature fluent answers, snippet-
only support, popularity capture, poisoned expansion terms, dynamic rank changes,
hidden missing positives, source outage, and exhausted budgets produce typed
coverage/stop states. Replay reconstructs every action and cost; adding useless
queries cannot improve the score; no end-answer success can hide missed gold
evidence, unsupported claims, or a controller that failed to terminate.

### 7.5 Verifiable reasoning, mathematics, and science

### MLREAS.1 — Private reasoning boundary

**Status:** Not implemented

**Contract:** Never require, expose, log, or train on raw provider-private
chain-of-thought. Produce a verification record containing assumptions,
subproblems, evidence, tools, concise derivation, checks, alternatives, and
limitations.

**Acceptance:** Trace/API/log/dataset scans contain zero seeded private-reasoning
canaries; users receive sufficient checkable evidence; provider reasoning fields
are discarded or separately encrypted only under authorized diagnostic policy.

### MLREAS.2 — Checkable reasoning artifact graph

**Status:** Not implemented

**Contract:** Represent each externally meaningful step as typed claim,
calculation, program, proof obligation, tool result, or inference with
dependencies and verifier state.

**Acceptance:** Cycles, missing premises, unsupported conclusions, altered tool
digests, and contradictory terminal claims fail validation; replay resolves the
same dependency graph without a hidden prose transcript.

### MLREAS.3 — Process-supervision experiment

**Status:** Not implemented

**Contract:** Admit process reward models/step labels only as candidate scoring
signals with dataset lineage, step semantics, annotator/judge agreement,
faithfulness interventions, and outcome checks.

**Acceptance:** Process scoring is ablated against outcome-only and deterministic
checks; seeded style/verbosity/reward-hacking steps cannot promote a worse
answer; low-agreement domains disable automatic use.

### MLREAS.4 — Exact tool and solver router

**Status:** Not implemented

**Contract:** Route arithmetic, units, symbolic algebra, optimization,
statistics, code, SAT/SMT, and formal proof to approved deterministic tools with
typed inputs, assumptions, precision/tolerance, and complete outputs.

**Acceptance:** A 500-case cross-domain suite matches reference results;
floating-point and timeout states are explicit; model text can never override a
nonzero solver/checker status.

### MLREAS.5 — Mathematical solution role

**Status:** Not implemented

**Contract:** Classify numeric, symbolic, proof, counterexample, estimation, and
modeling problems; state domains/assumptions; attempt independent methods; and
label evidence as tested, derived, or formally verified.

**Acceptance:** Unit/domain/edge-case and adversarial benchmark slices report
exact-answer, derivation-check, calibration, and counterexample rates; no
numeric sampling is labeled proof.

### MLREAS.6 — Formal theorem-proving role

**Status:** Not implemented

**Contract:** Pin prover/toolchain/library, preserve natural-to-formal
translation, run bounded tactic/proof search, and trust only the full formal
kernel/build result.

**Acceptance:** Lean/mathlib version mismatch and “No goals with other errors”
fixtures fail; accepted proofs rebuild from a clean environment; translations
require review when semantic equivalence is not mechanically established.

### MLREAS.7 — Scientific hypothesis and preregistration

**Status:** Not implemented

**Contract:** Separate observation, literature claim, hypothesis, prediction,
experiment, analysis, result, interpretation, and replication. Require units,
controls, power/sample rationale, exclusions, multiple-testing policy, risks,
and stopping rule before execution.

**Acceptance:** Missing controls/units/outcomes, post-hoc threshold changes, data
leakage, and unsafe physical/biomedical/chemical actions are blocked; plans
produce an immutable preregistration digest.

### MLREAS.8 — Bounded scientific experiment and replication

**Status:** Not implemented

**Contract:** Execute only approved computational experiments in isolated,
pinned environments; retain raw inputs/outputs, code, seeds, environment,
statistics, negative results, and independent replication status.

**Acceptance:** CORE-Bench, RE-Bench, ScienceAgentBench, and project-domain
fixtures report executable correctness and reproducibility; an automated review
or novelty score alone can never establish discovery or authorize a physical
action.

### MLREAS.9 — Process-verifier robustness and search separation

**Status:** Not implemented

**Contract:** Treat every process reward model, step judge, critic, and
first-error detector as a versioned candidate verifier, never as proof or its own
promotion authority. `process_verify` evaluates stable step semantics, first-error
localization, calibration/selective risk, domain transfer, harmless
metamorphisms, logic/unit/premise corruption, right-answer/wrong-process cases,
and adversarially optimized trajectories against deterministic execution,
solver, proof-kernel, citation, or authorized human checks. Separate the
generator's development verifier from the hidden promotion verifier and prevent
the generator from observing or updating promotion rewards. This combines the
data-efficient opportunity in [ThinkPRM](https://arxiv.org/abs/2504.16828) with
the demonstrated proxy vulnerability in
[Reward Under Attack](https://arxiv.org/abs/2603.06621).

**Acceptance:** ProcessBench/PRM-BiasBench-style fixtures perturb style,
verbosity, ordering, redundant steps, copied critiques, false premises, units,
arithmetic, code results, proofs, circular reasoning, and the first invalid step.
The suite reports error-side/success-side precision and recall, calibration,
abstention, domain slices, exact-check disagreement, best-of-N selection lift,
compute, and attack success. Near-perfect proxy reward with a wrong checked
answer, reward inflation under optimization, train/promotion-verifier leakage,
or low-confidence transfer blocks automatic use; no test requires retaining or
exposing provider-private chain-of-thought.

### MLREAS.10 — Executable research reproduction and claim reconciliation

**Status:** Not implemented

**Contract:** Have `research_reproduce` turn a paper, repository, data package,
declared environment, and claim/rubric into a pinned execution DAG. Resolve
dependencies and licenses, build without undeclared host state, run the exact
subset, capture commands/configs/seeds/raw outputs/checkpoints/logs/resource
usage, extract tables/figures/statistics through `MLCORE.16`, and compare every
target claim with the reproduced value and tolerance. Distinguish artifact
availability, successful execution, numerical reproduction, independent
replication, statistical validity, and scientific truth. Use hierarchical
author-reviewed tasks such as
[PaperBench](https://openai.com/index/paperbench/) and cross-format
[REPRO-Bench](https://arxiv.org/abs/2507.18901); an automated rubric judge remains
a fallible evaluator.

**Acceptance:** Fixtures cover missing/private data, ambiguous license,
unresolvable or mutable dependency, Lean/mathlib toolchain mismatch, hidden
network download, undeclared manual step, platform nondeterminism, seed
instability, timeout/OOM, partial checkpoint, cherry-picked task subset, unit or
statistical mismatch, stale cached output, extraction error, and paper/code/data
contradiction. A green subprocess or plausible report cannot mark a claim
reproduced unless the authoritative outputs match; partial runs preserve
claim-level `matched`, `mismatched`, `not_run`, and `unknown` states and never
authorize physical, biomedical, chemical, environmental, or human-subject action.

### 7.6 Multimodal vision, photo generation, and editing

### MLMEDIA.1 — Typed media ingredient manifest

**Status:** Not implemented

**Contract:** Represent image/audio/video/document ingredients with original
bytes digest, MIME/codec, dimensions/duration, color/sample metadata, source,
rights/consent, capture/import time, regions/timestamps, transformations, and
derived annotations.

**Acceptance:** Unsupported/corrupt/polyglot/oversized/decompression-bomb files
are rejected; every derivative resolves to immutable ingredients and transform
versions; tenant and rights policy applies transitively.

### MLMEDIA.2 — Vision evidence role

**Status:** Not implemented

**Contract:** Route OCR, captioning, detection, segmentation, visual QA, and
document/table understanding through declared adapters and emit spatial/page/
time locators with confidence and transformation lineage.

**Acceptance:** A versioned 100-case suite reports task-specific metrics and
calibration; generated captions/OCR never replace original evidence; absent or
ambiguous visual support yields partial/abstained claims.

### MLMEDIA.3 — Photo generation role

**Status:** Not implemented

**Contract:** Generate new assets through an optional Diffusers/provider adapter
using recorded model/component/adapter/scheduler/seed/device/precision/prompt/
condition state and pre-execution safety/rights policy.

**Acceptance:** Default jobs obey four-output/1024px/15-minute limits; every
output has a unique per-image generator state and pixel digest; denied content
and unauthorized model/adapter loads never execute.

### MLMEDIA.4 — Photo editing role

**Status:** Not implemented

**Contract:** Treat edit, inpaint, outpaint, variation, restoration, upscale,
background/region replacement, and instruction edit as distinct operations with
source ingredient, mask/region, intent, preservation constraints, and output.

**Acceptance:** Golden edits measure requested change and protected-region/
identity preservation separately; empty/invalid masks and implicit whole-image
changes fail; original assets remain immutable.

### MLMEDIA.5 — Conditioning and media-adapter router

**Status:** Not implemented

**Contract:** Validate and route LoRA, DreamBooth, ControlNet, T2I-Adapter,
IP-Adapter, reference image, pose/depth/edge, and multimodal tower adapters only
against compatible base pipelines and declared purposes.

**Acceptance:** Base/component/shape/scale/format mismatches fail preflight;
composition order and weights are new candidate IDs; router replay is
deterministic and cross-tenant/identity misuse fixtures are blocked.

### MLMEDIA.6 — Media safety and identity/rights review

**Status:** Not implemented

**Contract:** Apply policy for sexual/minor content, graphic harm, deception,
impersonation/deepfakes, biometric identity, copyrighted/trademarked material,
private imagery, location metadata, and domain-specific high-risk media before
generation/edit and before publication.

**Acceptance:** Maintained red-team suites achieve policy-required recall,
including transformed/encoded prompts and reference images; uncertain
identity/rights cases require review; safety model output is not the only
control.

### MLMEDIA.7 — C2PA-compatible provenance and disclosure

**Status:** Not implemented

**Contract:** Emit and validate C2PA-compatible ingredient/action manifests when
the format/backend supports them, plus an external signed Gludd manifest. Record
credential validation state without claiming that missing metadata proves
authenticity.

**Acceptance:** Tamper, stripped metadata, unknown signer, expired/revoked
credential, transformed export, and sidecar-loss fixtures return correct states;
pixel/ingredient/action digests remain resolvable after ZDD migration.

### MLMEDIA.8 — Multimodal generation/edit evaluation

**Status:** Not implemented

**Contract:** Evaluate prompt/condition alignment, composition/counting/text,
edit instruction success, preservation, visual quality, diversity, factuality,
bias, accessibility, safety, provenance, latency, memory, and cost with
task-specific metrics and human review where needed.

**Acceptance:** FID/CLIPScore/GenEval-like metrics are reported as partial
signals, not one promotion score; golden cases run on at least two seeds where
stochastic; safety/provenance regressions block promotion regardless of
preference score.

### MLMEDIA.9 — Geometry-preserving media transform and edit verification

**Status:** Not implemented

**Contract:** Represent EXIF orientation, pixel/aspect coordinates, crop, pad,
resize, resample, mask polarity/threshold/blur/dilate, alpha, ICC/colorspace,
control extraction, VAE/latent scale, batch ordering, and final compositing as an
invertible or explicitly lossy `MLCORE.16` transform graph. Map requested and
protected regions through every stage. `media_verify`, configured independently
from generation/editing, measures requested-change success, protected-region
preservation, boundary/seam/histogram artifacts, identity/text/count/spatial
constraints, and provenance against original pixels and derived annotations.
Model/VLM judgments are only signals because
[EditInspector](https://arxiv.org/abs/2506.09988) finds that current evaluators
can miss artifacts and hallucinate edit-induced changes.

**Acceptance:** Golden fixtures cover rotated/mirrored EXIF inputs, non-square
aspect ratios, coordinate-origin and off-by-one errors, mask polarity and empty/
full/out-of-bounds masks, antialiased/soft masks, crop-and-resize versus pad,
latent divisibility, alpha and wide-gamut conversion, batch permutation,
strength endpoints, ControlNet/reference transforms, protected text/face/detail,
post-composite seams, and cross-backend outputs. Every output region maps back to
source/condition coordinates; undeclared resampling or color loss fails
provenance; an edit that scores well semantically but alters protected content,
or merely pastes a mismatched region, cannot pass.

## 8. Continual research and governed capability evolution

These units operationalize the dossier's primary evidence on
[horizon scanning](https://arxiv.org/abs/2202.13480),
[scientific research agents](https://arxiv.org/abs/2404.07738),
[idea-evaluation limits](https://arxiv.org/abs/2409.04109),
[negative/missing evidence](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-13),
[human authority and independent assessment](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/),
[provenance](https://www.w3.org/TR/prov-o/),
[poisoning](https://arxiv.org/abs/2302.10149),
[reward overoptimization](https://arxiv.org/abs/2210.10760), and
[silent drift](https://proceedings.neurips.cc/paper/2019/hash/846c260d715e5b854ffad5f70a516c88-Abstract.html).
The persistent
[AI Scientist rate-limit](https://github.com/SakanaAI/AI-Scientist/issues/84)
and
[PaperQA resumability](https://github.com/Future-House/paper-qa/issues/381)
reports are mandatory failure fixtures, not anecdotes used to claim general
performance.

### MLCONT.1 — Versioned horizon-scan scope and cadence

**Status:** Not implemented

**Contract:** Define each recurring or one-shot scan by immutable scope revision:
questions, concepts/synonyms, Gludd components, source/domain/language/time
slices, inclusion/exclusion policy, cadence, freshness, expected outputs, risk,
resource profile, owner, and expiry. Record the prior watermark and do not start
a duplicate overlapping run with the same idempotency key.

**Acceptance:** Unit tests reject an unversioned or unowned scope; scheduler tests
prove exact-once logical admission across restart and duplicate delivery;
changing scope creates a new digest without rewriting prior runs; an expired or
disabled scope performs no fetch and records a terminal reason.

### MLCONT.2 — New issue, topic, idea, and weak-signal discovery

**Status:** Not implemented

**Contract:** Discover new and materially changed papers, standards, official
documentation, releases, code, issues/discussions, security advisories,
practitioner reports, benchmarks, datasets, patents/grants when configured, and
local Gludd outcomes. Normalize each observation into a signal with source
identity/version, first/last seen, change digest, affected capability, source
class, trust label, and uncertainty.

**Acceptance:** Time-sliced fixtures detect a new issue, changed API, corrected
paper, new code release, resurfaced duplicate, and deleted page exactly once;
source and language strata appear in coverage; popularity alone cannot admit or
rank a signal; unavailable strata remain visible missing coverage.

### MLCONT.3 — Reproducible deep web, paper, code, and forum research

**Status:** Not implemented

**Contract:** Execute the dossier section 12.3 process as a typed state machine:
preregister, registry refresh, search, immutable capture, identity resolution,
screening, extraction, disconfirmation, gap mapping, hypothesis generation,
ranking, agenda publication, and proposal. Preserve queries, cursors/pages,
ranks, fetch/parse outcomes, inclusion/exclusion reasons, locators, hashes,
fallbacks, and budget use.

**Acceptance:** A frozen mixed web/paper/code/forum corpus reproduces the same
admitted identities and claim graph from the run manifest; interrupted search
resumes from durable per-source checkpoints without duplicate fetch/admission;
429, parser failure, paywall, deletion, and archive fallback appear in the
report; a second implementation replays the trace without provider-private CoT.

### MLCONT.4 — Evidence-linked knowledge-gap detection

**Status:** Not implemented

**Contract:** Generate typed gaps only from unsupported, contradicted, stale,
out-of-domain, unreplicated, low-calibration, or repeatedly failing claims and
from missing capability/evaluation/source slices. Store the searched evidence,
blind spots, operational consequence, uncertainty, falsifier, cheapest next
check, expiry, and supersession graph.

**Acceptance:** Fixtures distinguish unsupported from disproved, absent evidence
from negative evidence, stale from retracted, and model uncertainty from source
coverage; synthetic recurring failures produce one deduplicated gap; resolving,
superseding, or invalidating a gap never erases its prior evidence.

### MLCONT.5 — Transparent novelty, impact, feasibility, and risk ranking

**Status:** Not implemented

**Contract:** Compare every idea to nearest prior art across identity, lexical,
semantic, citation, repository, issue/forum, and archive evidence. Report
novelty, expected impact against declared project outcomes, feasibility,
information gain, evidence strength, uncertainty, safety, cost, reversibility,
and source diversity separately; expose a Pareto frontier and configurable human
priorities rather than an opaque universal scalar.

**Acceptance:** Exact and paraphrased duplicates lose novelty; an idea with high
citation/social velocity but no project impact cannot outrank solely on
popularity; adversarial judge-order and self-citation fixtures do not change
deterministic dimensions; unavailable novelty sources yield `unknown`, not
`novel`; blinded human review can reproduce the displayed ranking inputs.

### MLCONT.6 — Negative, null, contradictory, and failed evidence

**Status:** Not implemented

**Contract:** Run explicit searches for contradiction, correction, withdrawal,
retraction, null/negative results, failed replication, security incident,
regression, abandoned project, and maintainer/user failure evidence. Preserve
these records in the claim and agenda graphs with the same identity, provenance,
screening, and freshness treatment as supporting evidence.

**Acceptance:** Gold fixtures cover separate Crossmark retraction records,
in-place correction, null result, failed replication, issue regression, and
searched-but-not-found; the first five can lower or block a claim while the last
only lowers coverage; deleting an unfavorable source from the current index does
not delete its immutable evidence or increase confidence.

### MLCONT.7 — Human-governed research agenda lifecycle

**Status:** Not implemented

**Contract:** Convert signals and gaps into a versioned agenda item containing
question/hypothesis, evidence graph, alternatives including no change, expected
information gain/outcome, minimum experiment, falsifier, budget, dependencies,
risks, owner, review/expiry date, and state. Only an authorized human may accept,
rescope, defer, reject, or close an item; automation may recommend a transition.

**Acceptance:** State-machine tests reject skipped, self-approved, unsigned, and
stale transitions; concurrent updates use compare-and-swap and retain both
proposals; rejected/failed items remain searchable negative evidence; agenda
ordering can be recomputed from recorded inputs without changing human decisions.

### MLCONT.8 — Source-registry discovery and zero-downtime refresh

**Status:** Not implemented

**Contract:** Periodically revalidate each source adapter's API/schema version,
capabilities, authentication, terms/robots, rate and concurrency observations,
pagination/cursor semantics, freshness, corrections/retractions, parser,
health, and fallbacks. Build a candidate registry beside the champion, replay
contract fixtures, shadow/dual-read, reconcile, then atomically move a versioned
pointer; retain the prior registry for rollback.

**Acceptance:** Fixtures cover GitHub API version sunset, Semantic Scholar 429,
SearXNG JSON disabled, Crossref cursor interruption/update relation, schema
addition/removal, credential expiry, and primary/fallback disagreement; active
research continues on the champion during refresh; failed reconciliation leaves
the pointer unchanged; rollback is lossless and does not restart admitted runs.

### MLCONT.9 — Evidence-backed Gludd core proposal synthesis

**Status:** Not implemented

**Contract:** Convert an accepted agenda item into a core-system proposal with
problem/outcome, affected existing seams, source and local-outcome evidence,
alternatives/OSS reuse decision, threat model, schemas/APIs, compatibility and
data migration, ZDD rollout/rollback, resource model, atomic requirement IDs,
first failing acceptance tests, evaluation plan, dependencies, and non-goals.
The output is a proposal artifact, not a repository mutation.

**Acceptance:** Schema tests reject a proposal without nearest existing seams,
OSS comparison, negative evidence, measurable outcome, tests, rollout, or
rollback; a fixture proposes an improvement to an existing Gludd seam without
inventing duplicate infrastructure; no proposal role has live-tree, promotion,
or deployment capability.

### MLCONT.10 — Collection, role, and skill proposal generation

**Status:** Not implemented

**Contract:** Propose new or changed collection/role/skill artifacts as explicit
versioned diffs containing input/output schemas, capabilities, tools/sources,
resource profile, evidence policy, evaluation suite, triggers, compatibility,
deprecation/migration, safety boundaries, and content digest. The skill body
cannot grant authority, and project overrides cannot weaken hard policy.

**Acceptance:** Golden proposals add a role, split an overloaded role, update a
skill, and retire a skill through side-by-side compatibility; tests reject
implicit tools, capability escalation, missing digest/eval, hidden policy
changes, and direct incumbent edits; unchanged inputs produce the same proposal
digest.

### MLCONT.11 — Isolated candidate materialization and compatibility

**Status:** Not implemented

**Contract:** Materialize only a human-accepted proposal in a project-isolated,
namespaced candidate workspace with immutable champion/proposal/source/eval
digests, least privilege, network policy, secret isolation, storage quota,
deadline, and cleanup. Build new schemas/artifacts beside old versions and
exercise forward/backward/rollback compatibility before shadow use.

**Acceptance:** Escape, symlink, cross-tenant, secret, network, budget, and
champion-write fixtures fail closed; interruption leaves the champion serving
and a resumable or safely disposable candidate; expand/migrate/contract tests
prove old and new readers during the transition; cleanup cannot remove the
champion or evaluation evidence.

### MLCONT.12 — Evaluation-driven champion/challenger promotion

**Status:** Not implemented

**Contract:** Evaluate candidate core/collection/role/skill artifacts against the
immutable champion on frozen capability, regression, transfer, adversarial,
security, resource, and rollback suites. Use deterministic oracles first,
independent blinded evaluators second, confidence intervals and predeclared
thresholds; require material improvement with no safety, compatibility, or
per-file coverage regression.

**Acceptance:** Candidate identity/order swaps do not change deterministic
outcomes; missing/timed-out cases count by the predeclared fail-closed rule;
holdout contamination, evaluator disagreement, insignificant lift, safety
regression, or resource breach prevents promotion; the complete per-case result
and decision replay from immutable artifacts.

### MLCONT.13 — Human authority and separation of duties

**Status:** Not implemented

**Contract:** Separate scanner, proposer, implementer, evaluator, policy owner,
approver, and deployer identities/capabilities. Require explicit human approval
for source-policy expansion, new external data/tool access, collection/role/skill
activation, core changes, holdout replacement, threshold change, or promotion.
No candidate or model judgment may mint, delegate, or satisfy that approval.

**Acceptance:** Authorization tests reject self-approval, model-generated
signature, expired/replayed approval, role collision forbidden by policy,
approval for a different digest, and post-approval mutation; emergency human
deny/stop overrides every automated recommendation; the audit proves who knew
which artifacts before the decision.

### MLCONT.14 — Pointer rollback, kill switch, and recovery

**Status:** Not implemented

**Contract:** Keep the last known-good core/collection/role/skill/source-registry
revision runnable and switch authority through an atomic versioned pointer.
Define automatic rollback on safety, correctness, latency, cost, resource,
drift, or error-budget breach plus an independent human kill switch and bounded
recovery procedure.

**Acceptance:** Fault-injection under concurrent traffic exercises each trigger,
stale compare-and-swap, partial migration, evaluator outage, and control-plane
restart; rollback meets the declared recovery objective without destructive
migration or request-schema break; the kill switch works when the candidate and
model providers are unavailable.

### MLCONT.15 — End-to-end research and evolution provenance

**Status:** Not implemented

**Contract:** Link signal, source snapshot, query, screening decision, claim,
gap, idea, agenda decision, proposal, code/config/data/model artifact,
experiment, evaluation, approval, rollout, observation, and rollback as
immutable entity/activity/agent records. Export W3C PROV-O and
SLSA/in-toto-compatible attestations while retaining compact Gludd IDs and
redacting secrets/private data.

**Acceptance:** A promoted and a rejected candidate each traverse back to exact
source/eval versions and responsible identities; digest tamper, missing edge,
wrong tenant, revoked signer, clock skew, and redaction fixtures return explicit
invalid/incomplete states; export/import round-trips preserve derivation and ZDD
pointer history.

### MLCONT.16 — Research poisoning and indirect-instruction defenses

**Status:** Not implemented

**Contract:** Treat all fetched papers, pages, repositories, issues, forums,
metadata, models, and generated critiques as untrusted data. Pin snapshots and
hashes, compare mutable/archive views, require source diversity for consequential
claims, detect duplicate/coordinated records and indirect prompt injection,
quarantine suspect evidence, and keep retrieval content out of policy,
instruction, capability, and approval channels.

**Acceptance:** Split-view, frontrunning, PoisonedRAG, malicious PDF/HTML,
repository-instruction, metadata-spoofing, coordinated-source, and stale-cache
fixtures cannot trigger a tool/policy action or verified claim; quarantine is
traceable and reversible; removing suspect evidence recomputes affected claims,
gaps, and rankings without rewriting the original run.

### MLCONT.17 — Reward hacking, evaluator capture, and contamination controls

**Status:** Not implemented

**Contract:** Prevent a candidate from reading hidden tests, modifying cases,
labels, metrics, judge prompts/models, thresholds, policy, provenance, resource
accounting, or promotion state. Use deterministic external checks, multiple
independence classes, blinded order, canaries, leakage/near-duplicate scans,
metric-component reporting, and periodic human audits; never optimize a single
model-judge score as the promotion objective.

**Acceptance:** Fixtures attempt answer-key discovery, metric tampering,
verbosity/style gaming, self-preference, sycophancy, benchmark memorization,
judge collusion, failure relabeling, cost hiding, and delayed trigger behavior;
each is detected or prevents promotion; optimizing the proxy while ground-truth
quality declines is a hard rejection with preserved evidence.

### MLCONT.18 — Evidence, behavior, evaluator, and objective drift

**Status:** Not implemented

**Contract:** Monitor source/topic coverage, claim validity, retrieval and answer
quality, calibration, task mix, costs/resources, safety, evaluator agreement,
human override, proposal acceptance, and post-promotion outcomes against
versioned baselines. Distinguish data, concept, schema, policy, objective, and
feedback-loop drift and route each to revalidation, shadow evaluation, rollback,
or a human agenda item rather than automatic retraining.

**Acceptance:** Controlled gradual, abrupt, seasonal, benign, malign, schema,
judge, policy, and objective shifts produce typed alerts with exemplars and
uncertainty; low-power/no-label conditions stay `unknown`; drift detection alone
does not mutate a model or threshold; post-promotion regression triggers
MLCONT.14 within its recovery objective.

### MLCONT.19 — Bounded, observable, release-aware research scheduling

**Status:** Not implemented

**Contract:** Enforce the `horizon_scan` profile and per-source quotas across
query/fetch/token/model/CPU/RAM/accelerator/disk/network/time/money dimensions.
Namespace every run and process, expose phase progress and heartbeats, checkpoint
long work, cap retries/concurrency, and yield admission to higher-priority release
and production work. A role cannot increase its own profile.

**Acceptance:** Boundary tests hit each ceiling with a typed partial/cancelled
result and intact checkpoint; 429/timeout retries honor headers and remain
bounded; restart and duplicate delivery do not double spend; load tests show a
release gate retains its declared resources and latency while a scan is paused
or throttled; no orphan process, lock, cache, or temporary artifact remains.

### MLCONT.20 — Safe self-evolution ceiling

**Status:** Not implemented

**Contract:** Allow the expert to research, answer, derive solutions, discover
gaps, synthesize Gludd core proposals, and propose improvements to its own
collection/roles/skills. Forbid autonomous live edits, authority expansion,
policy/evaluator/holdout mutation, deployment, or recursive spawning outside the
approved DAG. Progression is proposal -> human acceptance -> isolated
implementation -> independent evaluation -> human promotion -> ZDD canary, with
rollback available at every mutable stage.

**Acceptance:** An end-to-end fixture discovers a persistent Gludd issue,
reproduces deep research, proposes a new skill and core change, builds candidates
in isolation, and reaches a reviewable promotion record without touching live
state; adversarial requests to skip any stage, grant tools, reveal holdouts,
rewrite policy, self-approve, or suppress rollback fail closed; denial leaves the
champion unchanged and all useful research preserved.

### MLCONT.21 — Atomic claim and citation quality gate

**Status:** Not implemented

**Contract:** Decompose externally meaningful answer and proposal assertions
into atomic claims and evaluate citation locator validity, atomic entailment,
support completeness, contradiction, source quality, source independence,
version/integrity, and temporal scope. A citation URL or model-based entailment
score alone never verifies a claim; consequential claims require primary or
official evidence plus deterministic or authorized human verification when
available.

**Acceptance:** ALCE/FActScore-style fixtures include supportive, partially
supportive, irrelevant, contradictory, circular, mirrored, stale, retracted,
wrong-locator, and inaccessible citations; every unsupported clause remains
visible; dependent copies count as one independence group; evaluator
disagreement returns `partial` or `unknown`; the answer cannot report a higher
verified-claim count than the serialized claim/citation records reproduce.

### MLCONT.22 — Bitemporal validity, supersession, and as-of answers

**Status:** Not implemented

**Contract:** Store observation time separately from claimed valid-from/valid-to
time, with unknown bounds explicit. Append correction, supersession,
withdrawal, retraction, and deletion records instead of overwriting history.
Resolve current and as-of queries against one immutable knowledge snapshot and
prevent expired or future-invalid evidence from satisfying a present claim.

**Acceptance:** Fixtures cover an undated fact, scheduled API sunset, retroactive
correction, overlapping conflicting versions, retraction, deletion, late-arriving
older observation, and false-premise FreshQA-style question; current/as-of
answers select the expected versions and cite their temporal basis; rollback
reproduces the exact former answer without resurrecting evidence outside that
snapshot.

### MLCONT.23 — Practitioner, forum, and maintainer signal lifecycle

**Status:** Not implemented

**Contract:** Search canonical issue trackers/discussions and approved
practitioner forums for recurring failures, workarounds, regressions, operational
costs, and unmet needs. Preserve thread/comment identity, author/maintainer role,
timestamps, edits, issue state, affected versions/environment, reproducer,
reactions only as metadata, archive/digest, and corroboration. Default these
records to `practitioner_signal`; they may prioritize investigation but cannot
alone verify a general technical claim.

**Acceptance:** Fixtures distinguish maintainer confirmation, reproducible user
bug, duplicate reports, bot response, anecdote, edited/deleted post, coordinated
spam, popularity, resolved version, and still-open issue; a reproduced failure
links a local observation without rewriting the thread; search coverage includes
negative/closed results; likes or repetition cannot raise verification state.

### MLCONT.24 — Zero-downtime candidate knowledge snapshot

**Status:** Not implemented

**Contract:** Apply admitted source/claim changes to a versioned candidate
knowledge namespace, then reconcile source identities, temporal records,
tombstones, dependent chunks, embeddings, lexical index, graph edges, caches,
citations, access/deletion policy, and source/index watermarks. Run frozen
retrieval, answer, injection, privacy, resource, temporal, and rollback suites
before an authorized atomic pointer swap; retain champion and snapshot leases
for in-flight requests and replay. Enforce the `knowledge_refresh` profile; the
curator cannot expand it.

**Acceptance:** Concurrent update/query tests never observe a mixed snapshot;
insert/update/delete/retraction/parser/embedding/schema fixtures remove orphaned
derived state and preserve history; failed reconciliation or evaluation leaves
the champion pointer unchanged; in-flight champion requests finish while new
requests select the candidate; rollback restores the prior snapshot without
reindex or downtime.

### MLCONT.25 — Exposure-aware outcome and causal-learning ledger

**Status:** Not implemented

**Contract:** Record eligibility/context, assignment rule and probability,
selected revision, delivered intervention, immediate/delayed outcomes,
observation window, missingness, safety/cost/latency, human override, concurrent
changes, known confounders, prior Gludd-output exposure, and permitted learning
use. Prefer randomized shadow/canary evidence when safe; otherwise report
support/overlap, estimator assumptions, sensitivity, uncertainty, and
non-identifiability. Enforce the `outcome_analysis` profile. Outcome analysis
emits candidates, never live updates.

**Acceptance:** Fixtures cover randomized comparison, selection bias, zero/weak
propensity support, delayed/censored outcome, policy change, seasonality,
duplicate user, self-generated feedback, engagement proxy, override, rollback,
and no-effect result; causal claims appear only when the predeclared design
identifies them; off-policy estimates fail closed on inadequate support; raw
outcomes cannot directly mutate knowledge, procedure, role, skill, or thresholds.

### MLCONT.26 — Evidence-gated procedural and outcome memory

**Status:** Not implemented

**Contract:** Convert repeated validated outcomes into a candidate procedure or
calibration memory containing scope, preconditions, action, expected outcome,
counterevidence, provenance, independence class, confidence/calibration,
applicable versions, privacy/license, expiry, invalidation triggers, evaluation,
and rollback. Deduplicate semantically, preserve failures, and test old, new,
transfer, adversarial, and no-memory baselines to detect forgetting or harmful
overgeneralization.

**Acceptance:** A repeated successful procedure with independent outcomes can
reach human review; one anecdote, poisoned feedback, self-authored outcome,
expired version, privacy-disallowed record, or proxy-only gain cannot; tests
detect catastrophic forgetting and domain leakage; shadow lookup records whether
the memory would have changed an outcome; pointer rollback removes its authority
while preserving the evidence and rejected candidate.

### MLCONT.27 — Progressive role and skill revision rollout

**Status:** Not implemented

**Contract:** Version role and skill schemas, content, tools, capabilities,
budgets, triggers, evidence policy, and evaluation identity as one immutable
artifact. Install a candidate beside the champion; run static/schema/security
checks, replay, shadow decisions, bounded canary by eligible task slice, and
post-canary observation before pointer promotion. Existing in-flight DAGs pin
their revision. Tool/capability expansion requires separate human authority and
cannot ride a content-only approval.

**Acceptance:** Fixtures cover compatible addition, trigger change, schema
migration, tool addition, capability escalation, budget change, cyclic handoff,
bad shadow route, canary regression, mid-DAG pointer change, and rollback;
content-only changes cannot smuggle authority; champion and candidate outcomes
are attributed by exact revision; rollback affects new DAGs without corrupting
in-flight work.

### MLCONT.28 — Continual-loop health, staleness, and anti-drift audit

**Status:** Not implemented

**Contract:** Publish per-scope service objectives and distributions for source
coverage/diversity, registry health, scan/checkpoint age, source-to-index lag,
tombstone/orphan reconciliation, claim/citation support, stale-answer rate,
gap/agenda aging, proposal novelty/acceptance/outcome, evaluator agreement,
human override, memory use/lift, promotion/rollback, safety, resource cost, and
release interference. Compare against immutable baselines and detect metric
definition, objective, source, evaluator, and selection drift.

**Acceptance:** Synthetic outages, silent source loss, stale index, changed
metric denominator, evaluator upgrade, agenda starvation, popularity capture,
survivorship bias, rising cost, failed rollback, and release contention each
produce a typed alert with lineage and uncertainty; a green aggregate cannot
hide a failed required slice; the audit is read-only and cannot auto-relax its
SLO, change weights, retrain, promote, or suppress negative results.

### MLCONT.29 — Autonomous new Internet source discovery and onboarding

**Status:** Not implemented

**Contract:** Discover sources that are absent from the champion registry by
following typed Web links, API descriptions, repository metadata, scholarly
citations, archive/repository discovery records, standards registries, canonical
issue/forum references, and explicit coverage gaps. Normalize each result into
the section 3.5 candidate-source manifest, resolve mirrors and ownership, and
measure unique coverage, authority, independence, freshness, correction support,
terms/license, robots policy, privacy, authentication, rate/cost limits,
schema/parser stability, availability, archival fallback, SSRF/redirect/TLS
risk, untrusted-content risk, and required capabilities. A candidate remains
quarantined until sandboxed contract tests, shadow/dual-read comparison,
security and policy review, and explicit human approval succeed; only then may
`MLCONT.8` atomically promote the new registry revision. Discovery never grants
network or tool authority, creates an account or secret, installs source code, or
changes live source policy.

**Acceptance:** Fixtures discover a useful OAI-PMH/OpenAPI source and distinguish
it from a mirror, fork, search-result wrapper, paywall, abandoned endpoint, and
already registered source; robots denial, ambiguous terms/license, private-IP or
redirect SSRF, broken TLS, malicious metadata/instructions, schema drift,
pagination loss, 429, authentication request, and disappearing source remain
quarantined with typed reasons. A candidate with genuinely independent coverage
can reach human review through a reproducible discovery/probe ledger; denial,
timeout, failed shadow comparison, or absent approval leaves the champion
registry and active research unchanged.

### MLCONT.30 — Unattended autonomous research and improvement cycle

**Status:** Not implemented

**Contract:** Run registered research scopes without a live user question on a
versioned schedule or approved event trigger, including source/standard/library
changes, recurring Gludd failures, evaluation or outcome regressions, stale
coverage, and new issue/topic/idea signals. Each cycle preregisters scope,
budgets, source classes, policy, stop conditions, and outputs; invokes
`MLCONT.1`–`.7`, `.21`–`.23`, and `.29` as applicable; and may emit evidence,
gaps, agenda items, candidate-source manifests, or Gludd/expert improvement
proposals. Triggers and deliveries are deduplicated and idempotent. The cycle is
namespaced, checkpointed, heartbeat-visible, pausable, release-aware, and bounded
by `MLCONT.19`; it cannot autonomously accept its agenda, implement a candidate,
alter a champion, expand authority, or promote any result.

**Acceptance:** An end-to-end time/event replay with no user prompt discovers one
new topic, one recurring issue, one falsifiable idea, and one previously unknown
Internet source; performs reproducible deep and negative-evidence research;
deduplicates a repeated trigger; and produces reviewable source, expert, and
Gludd proposals with complete provenance. Empty scans remain valid negative
evidence rather than invented findings. Release contention pauses and resumes
from the same checkpoint without duplicate spend; cancellation, restart, stale
trigger, unavailable sources, insufficient evidence, and lack of human approval
leave every champion pointer unchanged.

### MLCONT.31 — Unified artifact promotion, dependency bundle, and rollback

**Status:** Not implemented

**Contract:** Apply one explicit transition protocol to source-registry adapters,
knowledge snapshots, procedure/outcome memories, collections, roles, skills,
Gludd core/config/schema/API changes, model adapters, routers, and evaluation
assets:
`proposed -> human_accepted -> materialized -> evaluated -> human_approved ->
shadow -> canary -> champion`, with terminal `rejected`, `rolled_back`, and
`retired` states. Every transition binds immutable artifact, dependency,
policy, capability, evaluation, approval, compatibility, migration, and rollback
digests. A multi-artifact improvement is one compatible dependency bundle:
prepare and evaluate all members beside their champions, then use versioned
compare-and-swap pointers or an atomic manifest pointer so partial promotion
cannot expose a mixed generation. In-flight work leases its admitted bundle.
Automatic rollback may select only a preapproved last-known-good bundle on a
declared breach; policy, evaluator, holdout, capability, or approval changes
always require their own human authority and cannot ride another artifact's
approval.

**Acceptance:** A table-driven conformance suite exercises every artifact class
and every legal/illegal transition, including missing or mismatched digests,
stale approval, skipped shadow/canary, capability smuggling, policy/holdout
mutation, partial materialization, failed migration, concurrent promotion,
stale compare-and-swap, mixed bundle revisions, in-flight lease, canary breach,
control-plane restart, rollback dependency ordering, and retirement. No failing
member changes an authoritative pointer; successful promotion exposes exactly
one compatible generation; rollback restores the complete last-known-good
bundle within its recovery objective while preserving provenance and rejected
evidence.

## 9. Cross-cutting acceptance criteria and gates

An implementation unit is complete only when all applicable gates below pass.

### 9.1 Evaluation design

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
- Evaluate horizon scanning with time-sliced replay so future evidence cannot
  leak into past discovery or novelty decisions.
- Evaluate agendas by executed information gain/outcome and human burden, not
  only whether another model liked the proposed idea.
- Score citation support at atomic-claim level and report source-independence
  groups, temporal validity, and evaluator uncertainty.
- Evaluate outcome-learning candidates against no-change and champion baselines
  with exposure/assignment and missing-outcome assumptions preserved.
- Evaluate candidate Internet sources for unique coverage, owner identity,
  independence, policy conformance, schema correctness, adversarial safety, and
  graceful loss before registry admission.
- Run the artifact-transition conformance matrix for every newly promotable
  artifact class and every multi-artifact dependency bundle.
- Run declared metamorphic and equivalence checks for every representation
  transform, including adapter merge/load, media geometry, dataset conversion,
  retrieval serialization, and executable-research materialization.
- Report PEFT results separately for retained base capabilities, new-task
  quality, transfer slices, activation/route correctness, and merged-versus-live
  adapter equivalence.
- Evaluate agentic retrieval at the controller-action level: unresolved atomic
  claims, counterevidence sought, marginal information gain, reformulations,
  backend switches, loop/saturation state, and justified stop decisions.
- Evaluate process verifiers independently from generators and search policy
  against exact checks, metamorphic corruptions, style shortcuts, calibration
  shifts, and hidden promotion attacks.
- Score media edits separately for requested-region success, protected-region
  preservation, geometry/mask alignment, artifact introduction, provenance, and
  semantic consistency.
- Reproduce computational research against pinned environments and report
  executable completion, artifact agreement, claim agreement, replication, and
  truth status as distinct outcomes.
- Compare every evolved generation with its parent, current champion, and
  immutable root baseline under frozen evaluator cohorts; report accumulated
  regression debt rather than only the latest aggregate score.

### 9.2 Security and privacy

- All network access passes SSRF, domain, scheme, size, content-type, timeout,
  and rate policy.
- All retrieved/model/tool content stays in untrusted data channels.
- Tools run with minimum capabilities in a namespaced sandbox.
- Secrets and personal data are redacted before model or dataset use.
- Project and tenant isolation is default deny.
- Dataset licenses, retention, deletion, and training permission are enforced.
- High-risk actions and promotions require separate human authority.
- Candidates cannot read hidden evaluations or write source policy, metrics,
  evaluators, approvals, promotion state, or their own resource accounting.
- Candidate generators, search policies, and process verifiers cannot observe
  hidden-promotion rewards or mutate the independent exact-check/verifier path;
  all evaluator roles remain read-only.
- Forum/practitioner content, outcome records, and candidate memories remain
  untrusted data subject to poisoning, privacy, consent, and retention policy.
- New-source discovery cannot create accounts/secrets, install code, expand
  destinations/capabilities, or convert discovered metadata into instructions.

### 9.3 Zero-downtime delivery

- Add schemas, tables, fields, and APIs compatibly before switching readers.
- Feature flags default off; shadow output is non-authoritative.
- Dual-read/write periods have reconciliation metrics and a removal plan.
- Canary selection uses a versioned pointer, not in-place artifact replacement.
- The previous champion remains runnable until post-rollout observation passes.
- Rollback is exercised under load and requires no destructive migration.
- Source-registry, collection, role, and skill revisions use the same
  side-by-side shadow/reconcile/pointer/rollback discipline as model artifacts.
- Knowledge snapshots pin in-flight requests and atomically reconcile source,
  temporal, tombstone, index, graph, cache, citation, and deletion state before
  pointer movement.
- Every mutable artifact class uses the `MLCONT.31` state machine; compatible
  multi-artifact changes promote and roll back as one immutable dependency
  bundle, never as independently visible partial revisions.
- A representation migration or destructive source retirement occurs only
  after `MLCORE.16` proves its declared equivalence level. Until then, preserve
  the original input plus the complete transform graph and expose only the
  prior authoritative representation.

### 9.4 Quality and coverage

- New or changed code aggregate line coverage is at least 85%.
- Every individual new or changed source file is at least 75%.
- Type checking, linting, unit, integration, security, injection, replay,
  resource, compatibility, and rollback tests are green.
- Tests may change only to match this specification, never merely to hide an
  implementation defect.
- Warnings, dependency-update informational messages, and deprecations have an
  actionable remediation or a dated, owned policy record.

## 10. Failure behavior

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
| Adapter/base/component mismatch | Refuse load before allocating serving state; retain current selection pointer |
| Adapter route uncertain or out of domain | Use approved base/fallback or abstain; never select by name similarity |
| Dataset format/schema drift | Quarantine affected shards and keep prior accepted dataset pointer |
| Streaming conversion interrupted | Preserve checkpoint and temporary namespace; never publish a partial manifest |
| Search API rate limited/unavailable | Honor headers, record failure, use declared fallback within the same trust policy |
| Derived index newer/older than authoritative state | Reconcile by source revision; suppress contradictory stale derived content |
| Raw/private reasoning returned | Prevent ordinary logging/API/dataset persistence and retain only allowed verification record |
| Formal prover emits partial-success text with errors | Trust full kernel/build failure and mark proof unverified |
| Media component/LoRA mismatch | Fail preflight without changing loaded champion pipeline |
| Media provenance missing or stripped | Return `unknown provenance`; never infer authentic or synthetic |
| Scientific result lacks replication/independent review | Label preliminary; do not claim discovery or authorize physical action |
| Required horizon-scan source unavailable | Mark the affected coverage/novelty dimensions `unknown`, checkpoint, and use only declared fallbacks |
| Research scan interrupted or rate limited | Persist causal failure and per-source cursor; resume idempotently within retry/budget limits |
| Candidate idea appears novel only to its generator | Require nearest-prior-art search and independent/human review; retain `unknown` if coverage is inadequate |
| Negative or retracted evidence found | Recompute dependent claims, gaps, agenda ranks, and promotion eligibility without deleting history |
| Source-registry candidate changes identity/pagination semantics | Keep champion registry active and fail reconciliation |
| Approval missing, expired, replayed, or for another digest | Keep the champion pointer; record denied/invalid transition |
| Candidate attempts evaluator, policy, evidence, or reward mutation | Terminate candidate, reject promotion, retain forensic artifacts, and open a security finding |
| Post-promotion behavior/objective drift | Freeze further promotion, route to typed review, and roll back when a declared error budget is breached |
| Research scheduler competes with release/production work | Throttle or checkpoint the scan and yield its namespace/resources without losing provenance |
| Citation exists but does not support the atomic claim | Mark unsupported/partial, lower coverage, and block verified status for the claim |
| Fact validity time is missing or inconsistent | Preserve `unknown` or conflict; do not substitute capture time or silently choose the newest record |
| Forum signal is popular but uncorroborated | Keep `practitioner_signal`; prioritize reproducibility without upgrading truth status |
| Candidate knowledge reconciliation is incomplete | Keep champion snapshot and quarantine the candidate namespace |
| Outcome attribution lacks overlap or identifiability | Report association/unknown with assumptions; do not create a learned live update |
| Candidate memory helps one slice but forgets or harms another | Reject promotion, retain counterevidence, and keep champion memory pointer |
| Role/skill candidate changes capability under content approval | Reject as authorization mismatch and open an auditable security finding |
| Discovered source lacks clear owner, terms, robots permission, or safe network path | Quarantine the candidate; do not expand the registry, credentials, or allowlist |
| Autonomous cycle overlaps, repeats, or competes with release work | Deduplicate or checkpoint/pause it; preserve provenance and never double spend or delay the release reservation |
| One member of a promotion dependency bundle fails | Leave all authoritative pointers on the prior compatible generation and retain the failed candidate evidence |
| Adapter appears to train/load but targets do not execute, weights do not update, routing is inactive, or merge equivalence fails | Reject or quarantine the adapter, retain base/champion pointers, and preserve activation, optimizer-identity, dtype, and forgetting evidence |
| Media transform cannot map regions exactly or changes a protected region | Reject the output, retain the original asset and transform graph, and report geometry/protected-region failure |
| Agentic retrieval repeats, saturates, or stops with unresolved required claims | Stop within the declared bound, return partial/abstained with the evidence-state ledger, and never infer coverage from answer fluency |
| Process verifier rewards a candidate that fails an exact check or adversarial robustness probe | Disable the candidate verifier for promotion, reject the scored candidate, and preserve the attack/calibration trace |
| Research execution succeeds but reported claims do not match reproduced artifacts | Mark the claim `mismatched`, preserve both outputs, and never label the work reproduced |
| Descendant improves a local metric but regresses the root/champion baseline or changes its evaluator | Record regression debt, reject promotion, and keep the immutable lineage/evaluator cohort |
| Representation transform cannot prove its declared invariant | Keep the result as a non-authoritative derived candidate; retain the source and do not move any pointer |

## 11. Delivery order

Implementation MUST land on `development` through small, independently tested
feature branches in this order:

1. typed schemas, project/tenant isolation, untrusted-content boundary, source
   policy, role/skill control plane, and transformation/equivalence protocol
   (`MLCORE.1`, `.2`, `.8`–`.10`, `.16`, `MLARCH.1`–`.6`);
2. logical datasets, format adapters, lineage, privacy, and conversion
   (`MLDATA.1`–`.7`);
3. Internet sources, safe fetch/parse, evidence broker, hybrid/graph retrieval,
   provenance, evidence-sufficiency control, and evaluation
   (`MLRET.1`–`.11`, `MLAI.4`–`.6`, `MLCORE.3`,
    `.4`, `.11`);
4. source-registry refresh, deep-research replay, signal/gap/negative-evidence
   ledgers, new-source onboarding, citation/temporal/forum evaluation, candidate
   knowledge snapshots, unattended bounded scheduling, and loop-health audit
   (`MLCONT.1`–`.8`, `.15`, `.16`, `.19`, `.21`–`.24`, `.28`–`.30`);
5. collection intake, decomposition, synthesis, calibration, tools, verifier,
   routing, and reporting (`MLAI.1`–`.3`, `.7`–`.15`);
6. private-reasoning boundary, exact tools, mathematics, formal proof,
   process-verifier robustness, and executable scientific reproduction
   (`MLREAS.1`–`.10`);
7. media ingredients, vision, generation/editing, conditioning, safety,
   provenance, geometry-preserving transforms, and evaluation
   (`MLMEDIA.1`–`.9`);
8. human-governed agendas, core/collection/role/skill proposal generation,
   isolated candidate materialization, outcome attribution, candidate memory,
   progressive role/skill rollout, unified artifact lifecycle, and authority
   separation (`MLCONT.7`, `.9`–`.11`, `.13`, `.20`, `.25`–`.27`, `.31`);
9. immutable experiment/outcome infrastructure, isolated workspace, and PEFT
   artifact/training foundations and adapter activation/equivalence auditing
   (`MLSI.1`–`.5`, `MLCORE.5`–`.7`, `.13`–`.15`,
   `MLPEFT.1`–`.5`, `.9`);
10. adapter composition/routing/serving plus evaluation-driven promotion, ZDD
    rollout, rollback, drift, reward-hacking controls, privacy, and authority
     (`MLCONT.12`, `.14`, `.17`, `.18`, `MLPEFT.6`–`.8`, `MLSI.6`–`.13`,
    `MLCORE.12`);
11. shadow evaluation and a disabled-by-default canary before any production
   authority is granted.

Shared infrastructure has one writer at a time. A feature lands on one branch
first and is then merged; it must not be independently recreated on multiple
branches.

## 12. Required implementation evidence

For each atomic ID, its implementation record MUST contain:

- code and test paths;
- the first failing test and final passing test evidence;
- exact evaluation suite and dataset digests;
- coverage for every changed source file;
- threat cases and resource-limit results;
- research query/screening/checkpoint/coverage replay and negative-evidence
  results where continual discovery applies;
- agenda transition, separation-of-duties, approval, provenance-attestation,
  poisoning, reward-hacking, and drift evidence where capability evolution
  applies;
- atomic citation, temporal/as-of, forum-signal, knowledge reconciliation,
  exposure/outcome attribution, memory-forgetting, role/skill canary, and
  continual-loop SLO evidence where those contracts apply;
- candidate-source discovery/probe/onboarding, unattended-cycle replay, and
  artifact-class transition/dependency-bundle conformance evidence where those
  contracts apply;
- representation-transform manifests and declared byte/numerical/task/derived
  equivalence evidence;
- adapter target execution, trainable-parameter identity, update-norm,
  activation/route, merge/load, dtype/quantization, and forgetting evidence;
- retrieval-controller action/stop replay with unresolved-claim,
  counterevidence, marginal-gain, saturation, and loop evidence;
- process-verifier exact-check, metamorphic-corruption, shortcut, calibration,
  generator/search-separation, and hidden-promotion evidence;
- media coordinate/mask/color/latent/composite transform traces plus
  requested-region and protected-region results;
- executable-research environment/artifact digests and distinct completion,
  claim-match, replication, and truth-state results;
- multi-generation lineage, evaluator-cohort, parent/champion/root comparison,
  diversity, and regression-debt evidence;
- rollout/rollback evidence when behavior can affect a running system;
- documentation and source-registry changes;
- commit, branch, CI run, and artifact digests; and
- known limitations and an owner/review date.

Until those fields exist and the relevant gate is green, the unit remains
unimplemented regardless of partial code or prose.

## 13. Practitioner evidence and reconciliation notes

Hugging Face PEFT [issue #1802](https://github.com/huggingface/peft/issues/1802)
records a 2024 user report that named adapter switches left outputs unchanged,
which directly motivates activation-state, route, merge, and golden-output
acceptance checks. AI Scientist
[issue #84](https://github.com/SakanaAI/AI-Scientist/issues/84) records repeated
429 failures during novelty/citation search; an unavailable required source must
therefore make novelty `unknown`, checkpoint bounded work, and leave the active
knowledge pointer untouched. These practitioner reports remain untrusted inputs
until reproduced. Their durable failure modes are preserved in the research
ledger and cannot grant promotion authority.
