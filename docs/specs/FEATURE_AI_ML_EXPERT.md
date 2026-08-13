# Feature: AI/ML Expert Collection and Self-Improving Research System

**Status: PROPOSED** | **Created: 2026-07-29** | **Target: development**

## 1. Purpose and Non-Goals

Implement an expert collection, runtime service, and reusable skill that can
answer AI/ML questions, derive testable solutions, select mature tools, and
continuously discover new research without silently changing production
behavior. The system covers model engineering, data, retrieval, reasoning,
speech, vision, world models, distillation, efficient adaptation, scientific
simulation, evaluation, and accelerator-aware execution.

The feature is not an autonomous authority. It must not deploy a model, execute
downloaded code, train against private data, spend cloud/GPU budget, clone a
voice, or promote a research finding without the approvals and gates defined
below. It must not expose hidden chain-of-thought. It returns concise answers,
citations, uncertainty, and independently verifiable intermediate artifacts.

## 2. Feature IDs and Required Outcomes

| ID | Capability | Required outcome |
|----|------------|------------------|
| `AIML-001` | Expert router | Route questions and projects to the smallest qualified role set |
| `AIML-002` | Research discovery | Discover papers, official docs, code, benchmarks, articles, and long-lived user reports |
| `AIML-003` | Evidence store | Store immutable, licensed, deduplicated, citation-addressable evidence snapshots |
| `AIML-004` | Self-update | Stage, evaluate, approve, publish, and roll back expert knowledge updates |
| `AIML-005` | Data engineering | Select formats, validate data, build versioned datasets, and produce data cards |
| `AIML-006` | Retrieval | Provide hybrid lexical/vector/graph retrieval with reranking and freshness controls |
| `AIML-007` | Reasoning | Produce verifiable plans, calculations, tool traces, and uncertainty without hidden-CoT leakage |
| `AIML-008` | Adaptation | Plan and run LoRA/QLoRA/adapter fine-tuning with reproducible manifests |
| `AIML-009` | Distillation | Distill a permitted teacher into a validated student without silent capability loss |
| `AIML-010` | Speech recognition | Batch and streaming ASR with timestamps, language ID, diarization, and confidence |
| `AIML-011` | Speech synthesis | Consent-gated TTS with pronunciation control, provenance, and anti-impersonation controls |
| `AIML-012` | Vision understanding | Classify, detect, segment, OCR, embed, and answer grounded image questions |
| `AIML-013` | Image generation/editing | Generate or edit images with provenance, content controls, and reversible source artifacts |
| `AIML-014` | World models | Train/evaluate latent or state-space environment models and expose uncertainty-aware rollouts |
| `AIML-015` | Simulator federation | Run versioned physics, electronics, chemistry, and astronomy simulator adapters |
| `AIML-016` | Evaluation | Compare candidates on quality, safety, latency, cost, energy, robustness, and calibration |
| `AIML-017` | Accelerator execution | Plan and execute bounded jobs on local, Azure A100/H100, and equivalent accelerators |
| `AIML-018` | Tool discovery | Find and assess existing libraries, model servers, datasets, indexes, and build helpers before custom code |
| `AIML-019` | Observability | Correlate every answer, search, dataset, model, adapter, simulation, and deployment decision |
| `AIML-020` | Zero-downtime delivery | Promote knowledge/model/index changes with canary, shadow, rollback, and no request loss |

## 3. Architecture

### 3.1 Collection, service, and skill

The Ansible collection is `general_ludd.ai_ml`. Runtime code lives under
`src/general_ludd/ai_ml/`. The user-facing skill is
`skills/ai_ml_expert/SKILL.md`; it invokes the same typed service interfaces as
the collection rather than duplicating prompts or knowledge.

| Layer | Responsibility |
|-------|----------------|
| Collection roles | Reproducible provisioning, research refresh, dataset, training, evaluation, serving, and rollback workflows |
| Expert service | Routing, evidence retrieval, tool execution, policy, manifests, and result validation |
| Skill | Task decomposition, safe defaults, user interaction, citations, and artifact presentation |
| Registries | Immutable sources, datasets, models, adapters, simulators, evaluations, and deployment aliases |
| Event bus | Durable progress, approval, audit, failure, and promotion events |

### 3.2 Roles

| Role | Purpose |
|------|---------|
| `research_refresh` | Search allowed internet sources, normalize evidence, detect novelty, and open a staged update |
| `research_answer` | Retrieve evidence and produce a cited, uncertainty-calibrated answer |
| `tool_discover` | Compare mature tools, libraries, datasets, formats, model servers, and helper scripts |
| `dataset_engineer` | Ingest, validate, deduplicate, redact, split, version, and document datasets |
| `retrieval_engineer` | Build/evaluate lexical, dense, sparse, hybrid, graph, multimodal, and temporal indexes |
| `model_select` | Match task constraints to model/license/hardware/cost characteristics |
| `adapter_train` | Train and merge or serve LoRA/QLoRA/adapters with reproducible manifests |
| `model_distill` | Generate/validate teacher data and train a constrained student |
| `speech_recognize` | Run batch/streaming ASR, VAD, diarization, timestamps, and language ID |
| `speech_synthesize` | Run consent-gated TTS, pronunciation control, streaming, and watermark/provenance steps |
| `vision_understand` | Run image classification, detection, segmentation, OCR, embeddings, and grounded VQA |
| `image_create` | Generate, inpaint, outpaint, relight, upscale, restore, and transform images |
| `world_model` | Prepare environments, learn dynamics, evaluate rollouts, and expose planning APIs |
| `simulate_domain` | Resolve and execute a supported scientific simulator adapter |
| `reason_verify` | Solve via tools, typed intermediate artifacts, independent checks, and confidence calibration |
| `evaluate_model` | Run versioned task, safety, bias, robustness, latency, cost, and regression suites |
| `accelerator_job` | Plan and run quota/budget bounded jobs with checkpoint/resume and cleanup |
| `promote_release` | Shadow, canary, promote, or roll back model/index/knowledge aliases |

No role may call another role by shelling out. Role composition uses the typed
orchestrator API and records parent/child run IDs.

## 4. Stable Interfaces

### 4.1 Expert request

All ingress paths validate the same JSON-compatible request:

```json
{
  "schema_version": "1.0",
  "request_id": "uuid",
  "tenant_id": "string",
  "task": "question|research|dataset|train|distill|speech|vision|image|world_model|simulate|evaluate|deploy",
  "query": "string",
  "inputs": [{"uri": "artifact://...", "media_type": "string", "sha256": "hex"}],
  "constraints": {
    "deadline_s": 300,
    "budget_usd": 0,
    "max_gpu_hours": 0,
    "data_classification": "public|internal|confidential|restricted",
    "offline": false,
    "allowed_licenses": ["SPDX-id"],
    "allowed_tools": ["capability-id"]
  },
  "requested_outputs": ["answer", "plan", "artifact", "evaluation"],
  "approval_token": null
}
```

Unknown fields are rejected for mutation-capable tasks and tolerated only in a
versioned read-only compatibility mode. The router returns a typed refusal when
constraints cannot be satisfied; it never silently relaxes them.

### 4.2 Expert result

```json
{
  "schema_version": "1.0",
  "request_id": "uuid",
  "run_id": "uuid",
  "status": "succeeded|degraded|refused|failed|awaiting_approval",
  "answer": "string|null",
  "artifacts": [{"uri": "artifact://...", "sha256": "hex", "media_type": "string"}],
  "citations": [{"source_id": "uuid", "locator": "string", "claim_ids": ["string"]}],
  "verification": [{"check": "string", "status": "pass|fail|not_run", "artifact_uri": "artifact://..."}],
  "uncertainty": {"score": 0.0, "method": "string", "limitations": ["string"]},
  "cost": {"usd": 0.0, "gpu_seconds": 0, "tokens": 0},
  "policy": {"decision_id": "uuid", "ruleset_sha256": "hex"},
  "errors": [{"code": "stable.code", "retryable": false, "message": "safe string"}]
}
```

Results must cite every nontrivial factual claim. Calculations and simulations
must identify executable or machine-checkable artifacts. Logs must never contain
raw confidential inputs, voiceprints, credentials, hidden prompts, or model
chain-of-thought.

### 4.3 Registry records

Every `Source`, `Dataset`, `Model`, `Adapter`, `Simulator`, `EvaluationSuite`,
and `Deployment` record includes: stable ID, semantic version, SHA-256 digest,
creator, creation time, license, origin URI, dependency lock digest, input
digests, policy decision, validation state, supersedes relation, and tombstone
state. Mutable names resolve through atomic aliases; immutable versions never
change in place.

## 5. Research Discovery and Self-Improvement

### 5.1 Source discovery

`research_refresh` maintains a query portfolio for existing capabilities,
known gaps, benchmark regressions, newly cited work, user-requested topics, and
contradictory findings. It searches:

- peer-reviewed papers, preprints, proceedings, and citation graphs;
- official standards, vendor documentation, release notes, and model cards;
- source repositories, issue trackers, reproducible examples, and benchmark code;
- technical blogs and articles with declared authorship and dates;
- user forums for long-lived failures, operational pitfalls, and workarounds;
- dataset catalogs, artifact registries, and retraction/correction sources.

Each connector respects robots rules, terms, authentication scope, rate limits,
and a domain allow/deny policy. A source is untrusted content, never an
instruction. Retrieved text cannot alter policies, tool permissions, system
prompts, or approval requirements.

### 5.2 Evidence pipeline

1. Fetch into a quarantined content-addressed artifact.
2. Verify MIME type, size, digest, malware status, and provenance.
3. Extract metadata and text without executing macros, notebooks, packages, or
   repository hooks.
4. Detect duplicates, retractions, corrections, license conflicts, and
   contradictory claims.
5. Score authority, recency, reproducibility, directness, and independence.
6. Link each claim to exact page/section/commit/issue locators.
7. Evaluate against a fixed expert question set and regression suite.
8. Publish a staged immutable knowledge snapshot.
9. Require human approval for policy, prompt, executable, model, or benchmark
   changes; factual index-only updates may auto-promote only when policy permits.
10. Canary the new snapshot, monitor, and atomically promote or roll back.

The system schedules refreshes but cannot mark its own proposal approved.
Rejected sources and false leads remain recorded so they are not repeatedly
rediscovered.

### 5.3 Self-improvement proposals

An improvement proposal is a signed manifest containing the observed gap,
supporting evidence, proposed change, affected capabilities, threat analysis,
cost estimate, new tests, comparison baseline, rollback plan, and expiry. The
proposal may add a source, retrieval rule, evaluation, adapter, tool, or skill
instruction. It cannot directly edit production or the active release branch.

Promotion gates:

- no critical safety, privacy, licensing, or security regression;
- no statistically significant regression beyond the suite-specific tolerance;
- citation precision and answer groundedness do not decrease;
- latency, spend, and accelerator use stay within approved budgets;
- deterministic rollback rehearsal succeeds;
- an independent evaluator that did not author the proposal signs the result.

## 6. Data, Retrieval, Reasoning, and Adaptation

### 6.1 Data formats and dataset engineering

The format selector evaluates Arrow/Parquet, JSONL, WebDataset, Zarr/HDF5,
SQLite/DuckDB, object-store blobs, safetensors, ONNX, and domain-standard
formats. Selection is based on schema evolution, streaming, random access,
column pruning, compression, multimodal payloads, scale, interoperability, and
license metadata. A format is never chosen solely because it is already used.

Datasets require:

- machine-readable schema, units, ontology/version, and null semantics;
- origin/license/consent records at item or partition granularity;
- leakage-aware train/validation/test splits and near-duplicate checks;
- PII, secret, malware, poison, and prompt-injection scans;
- class/distribution summaries, known gaps, and a data card;
- immutable manifest with shard digests and reproducible transforms.

### 6.2 Retrieval

The retrieval service supports BM25/lexical, dense vector, learned sparse,
hybrid fusion, reranking, knowledge-graph traversal, temporal filters, and
multimodal embeddings behind one interface. It evaluates recall@k, MRR/nDCG,
answer faithfulness, citation precision, freshness, latency, and cost.

Every answer records the query rewrite, index version, dense-vector mapping
version, filter policy, retrieved source IDs, scores, reranker version, and
chosen citation spans. Raw confidential queries are encrypted and excluded from
training unless explicit consent exists. Deterministic vector stubs use a
collision-resistant standard-library digest and a versioned domain; weak-digest
downgrades are forbidden. The BLAKE2b migration, cached-ranking impact, ZDD
procedure, and CPython FIPS operator evidence are recorded in
[`retrieval-hash-migration.md`](../security/retrieval-hash-migration.md).

#### 6.2.1 Probabilistic sketch compatibility

MinHash comparison, merge, and LSH lookup require the same permutation count and
seed. A different seed defines a different hash domain; comparing signatures
position-by-position would return a plausible but meaningless score, so the
runtime fails closed. Serialization preserves both fields. Input normalization
remains salt-free at its public boundary, while the stable domain salt is applied
inside hashing so persisted signatures retain their established values.

LSH uses exact-band lookup first. If no full band matches, a one-row multi-probe
checks indexed signatures for at least one shared permutation. This fallback is
deliberately recall-oriented and returns candidates, not a similarity verdict;
callers must apply the MinHash estimate or an exact comparison afterward.

This compatibility rule responds to a durable upstream practitioner failure:
datasketch [issue #18](https://github.com/ekzhu/datasketch/issues/18), opened in
2017, reported that serialization dropped the hash implementation and restored a
different default. The resulting sketches appeared valid but no longer belonged
to the same comparison domain. A later
[hash-width discussion](https://github.com/ekzhu/datasketch/issues/212) likewise
shows that memory optimizations can change persisted hash semantics. Gludd
therefore treats hash-domain metadata as correctness data, not an optimization
detail.

#### 6.2.2 T-Digest accuracy and merge invariants

T-Digest centroids remain sorted and preserve total weight across incremental
updates, serialization, and merges. A candidate centroid may be absorbed only
when its cumulative quantile interval satisfies `k(q1) - k(q0) <= 1` for the
configured compression. The minimum and maximum centroids remain singletons so
tail queries and disjoint-range merges cannot average away observed extrema.
Higher compression must retain at least as much deterministic resolution while
the centroid count remains bounded by `2 * compression + 10`.

Quantiles interpolate between centroid mid-ranks, rather than treating a
centroid's full weight as located at the preceding boundary. CDF estimates use
the same mid-ranks, stay within `[0, 1]`, and are monotonic; a single-centroid
point mass has CDF `0.5` at its mean. The binary and pickle layouts do not change,
so these numerical corrections can roll through a mixed-version deployment
without rewriting stored digests.

This contract addresses a long-lived practitioner concern recorded in Presto
[issue #12929](https://github.com/prestodb/presto/issues/12929), opened in 2019:
operators explicitly asked how compression affects median accuracy and whether
merging small splits degrades results. The upstream t-digest project likewise
documents that user reports of subtle misbehavior drove stricter size invariants,
stable ordering, and improved interpolation in its
[reference implementation](https://github.com/tdunning/t-digest). Gludd pins
those properties with ordered, random, repeated-value, and disjoint-merge tests
rather than relying on a single distribution benchmark.

### 6.3 Reasoning and verification

The expert uses a plan/act/observe/verify state machine with typed, bounded
steps. It may use code, theorem, search, calculator, retrieval, simulator, or
domain verifier tools. Externally visible reasoning is a concise rationale plus
verifiable artifacts, not private token-level chain-of-thought. Math and science
answers must preserve units, significant figures, assumptions, boundary
conditions, and uncertainty.

At least one independent check is required for high-impact numerical answers:
alternative solver, dimensional analysis, conserved quantity, known limiting
case, benchmark dataset, or human approval. Failed checks produce `degraded` or
`failed`, never a confident answer.

### 6.4 LoRA, QLoRA, adapters, and distillation

`adapter_train` records base-model digest, adapter method, target modules, rank,
alpha, dropout, quantization, optimizer, seed, dataset manifest, tokenizer,
precision, hardware, dependency lock, checkpoints, and evaluation results.
Serving an adapter against a different base digest is a hard failure.

`model_distill` supports response, feature, logit, preference, and
task-specific distillation only when teacher use and generated-data licenses
permit it. Teacher outputs are filtered for secrets, unsafe content, duplication,
and provenance gaps. Student promotion requires task-retention thresholds,
calibration, adversarial and safety testing, contamination checks, and a model
card documenting capabilities lost during compression.

Training jobs are restartable from verified checkpoints. Partial outputs remain
quarantined. Out-of-memory, NaN/Inf, divergent loss, budget overrun, or corrupt
checkpoint conditions stop safely and preserve diagnostic artifacts.

## 7. Speech, Vision, and Image Capabilities

### 7.1 Speech recognition

The ASR contract accepts audio artifact URI, language hint, streaming flag,
speaker count bounds, timestamp granularity, vocabulary hints, and privacy
class. It returns normalized transcript segments with start/end time, speaker
label, language, confidence, and non-speech events. Streaming results expose
partial versus final status and monotonically increasing sequence numbers.

Evaluation includes word/character error rate, timestamp error, diarization
error, language-ID accuracy, real-time factor, noise/accent robustness, and
confidence calibration. Audio retention defaults to zero after result
finalization unless the caller explicitly requests a permitted artifact.

### 7.2 Speech synthesis

The TTS contract accepts text/SSML, language, approved voice ID, pronunciation
lexicon, pace, pitch, format, sample rate, and streaming flag. A custom voice
requires identity/consent evidence, intended-use scope, expiry, and audit.
Requests that imitate a real person without verified permission are refused.

Outputs include audio digest, text digest, voice/model versions, synthesis
parameters, consent reference when applicable, and supported provenance
marking. Evaluation covers intelligibility, speaker similarity only for
authorized voices, pronunciation, naturalness, streaming latency, and artifacts.

### 7.3 Vision understanding and image generation

Vision understanding supports classification, detection, segmentation, OCR,
captioning, visual question answering, similarity, document layout, and
scientific imagery. Results include pixel or region grounding where the task
supports it, confidence, transform history, and model/version.

Image creation supports text-to-image, image-to-image, inpainting, outpainting,
upscaling, restoration, relighting, background/subject operations, and
format/color-profile conversion. Original inputs are immutable. Every edit
produces a reversible operation graph, masks, seeds where available, model and
adapter versions, content-policy decision, and provenance metadata. Medical,
forensic, and scientific images must be labeled synthetic/modified and cannot
be presented as measurements.

## 8. World Models and Scientific Simulators

### 8.1 World-model contract

A world-model environment defines observation/action/state schemas, units,
time step, reset/terminal behavior, stochastic seed, legal actions, constraints,
reward or objective, simulator/source version, and dataset manifest. Models may
be latent dynamics, state-space, predictive video, object-centric, or hybrid
physics/learned systems.

Training and evaluation measure multi-horizon prediction, calibration, constraint
violations, compounding error, out-of-distribution detection, controllability,
planning regret, and wall-clock/accelerator cost. Rollouts expose epistemic and
aleatoric uncertainty. A low-confidence rollout cannot authorize real-world
actuation.

### 8.2 Simulator adapter contract

Gludd federates mature simulators; it does not implement replacement numerical
solvers when a maintained tool exists. Each adapter declares:

```yaml
capability_id: simulator.domain.name
adapter_version: semver
engine_name: string
engine_version: string
engine_digest: sha256
input_schema: artifact-uri
output_schema: artifact-uri
units_system: SI
determinism: deterministic|seeded|stochastic
resources: {cpu: 1, memory_mb: 1024, gpu: 0, timeout_s: 60}
license: SPDX-id
sandbox_profile: string
validation_suite: string
```

Initial domains:

- physics: rigid/deformable body, fluid, thermal, optics, electromagnetics, and
  multiphysics adapters;
- electronics: SPICE-class circuit, signal-integrity, PCB/electromagnetic, power,
  and control-system adapters;
- chemistry: cheminformatics, quantum chemistry, molecular dynamics, reaction
  kinetics, thermodynamics, electrochemistry, and process adapters;
- astronomy: coordinates/time, orbit/N-body, radiative/spectral, telescope,
  cosmology, and survey-data adapters.

Adapters run in network-denied sandboxes by default, pin dependencies and engine
digests, enforce CPU/memory/GPU/time/output limits, normalize units, and validate
outputs against engine-specific invariants. Cross-simulator agreement tests are
required for shared benchmark cases. Unsupported fidelity or boundary conditions
produce a refusal, not an extrapolated result.

## 9. Tool and Resource Discovery

Before proposing custom code, `tool_discover` queries the source registry and
allowed internet connectors for maintained libraries, command-line tools,
Ansible collections, model servers, indexes, dataset systems, evaluation
frameworks, build helpers, and deployment/debug scripts.

Candidates are scored on task fit, maintenance activity, release cadence,
security history, license, reproducible installation, platform/accelerator
support, API stability, observability, community issue history, and exit
strategy. The output is a decision record with rejected alternatives and a
minimal integration spike. Popularity alone is not a selection criterion.

Executable examples from repositories, blogs, or forums remain untrusted and
must be reviewed, pinned, sandboxed, scanned, and tested before use.

## 10. Accelerator-Aware Execution

The accelerator planner discovers permitted hardware without provisioning it,
then chooses topology, precision, parallelism, batching, checkpointing, and
serving settings from measured capability. It supports local CPU/GPU and
approved cloud accelerators including Azure A100/H100-class hardware.

Each execution plan declares SKU, region, quota evidence, image digest, driver
and runtime versions, interconnect assumptions, storage/network needs, budget,
timeout, checkpoint path, teardown behavior, and fallback. Provisioning requires
an approval token. Teardown is idempotent and emits proof that resources were
released. Preemption resumes from the last verified checkpoint; it does not
restart spending from zero without approval.

## 11. Security and Failure Behavior

| Condition | Required behavior |
|-----------|-------------------|
| Prompt injection in source/data | Treat as data, mark source, exclude injected instructions, continue only if safe |
| Unknown or incompatible license | Quarantine artifact and refuse training/promotion |
| PII, secret, or unauthorized voice data | Redact or refuse; do not index, log, or train |
| Untrusted model repository code | Disable remote code; sandbox reviewed code if explicitly approved |
| Unsafe serialization | Accept declared safe formats; quarantine pickle-like executable formats |
| Hash/signature mismatch | Hard fail, revoke cache entry, emit security event |
| Retrieval outage | Use explicitly versioned local snapshot and mark `degraded`, or fail if freshness is required |
| Simulator crash/timeout | Terminate sandbox, preserve bounded diagnostics, return no fabricated result |
| GPU OOM/preemption | Save verified checkpoint if possible, release resources, return retry plan |
| Budget/quota exhaustion | Stop before overrun and return `awaiting_approval` or `failed` |
| Evaluation regression | Block promotion and retain current alias |
| Knowledge/index/model canary regression | Automatic rollback within the stated recovery objective |
| Event/audit store unavailable | Fail closed for mutation; read-only answers may degrade only under policy |

All downloaded artifacts are content-addressed and scanned. Network egress,
filesystem mounts, environment variables, tools, and subprocesses are
allowlisted per role. Tenant data and indexes are cryptographically isolated.

## 12. Zero-Downtime Delivery

Knowledge snapshots, indexes, models, adapters, policies, and simulator adapters
are independently versioned. Promotion follows:

1. Build immutable candidate artifacts off the serving path.
2. Validate schema and dependency compatibility.
3. Restore and query the candidate in an isolated environment.
4. Shadow representative traffic with outputs withheld.
5. Canary by stable request hashing while the prior version remains warm.
6. Compare online quality, safety, latency, error, and cost budgets.
7. Atomically swap the alias; in-flight requests finish on their original version.
8. Retain at least the prior two known-good versions and rehearse rollback.

Required objectives: zero dropped accepted requests, zero mixed-version result
manifests, rollback initiation within 60 seconds of a hard threshold breach,
and recovery within 5 minutes for index/knowledge changes or the declared model
load objective for large weights. Database changes use expand/migrate/contract;
destructive contraction waits until every supported reader is upgraded.

## 13. Observability

Every operation emits OpenTelemetry-compatible traces and structured events
with request/run/tenant IDs, role, artifact versions, policy decision, source
and index versions, tool/simulator/model identifiers, accelerator allocation,
cost, retry count, and sanitized error code.

Required metrics include:

- research fetch success, age, novelty, duplicate/retraction rate, and promotion lag;
- retrieval recall proxy, groundedness, citation precision, empty-result rate, and latency;
- ASR/TTS/vision/image quality and safety outcomes by declared evaluation slice;
- training loss/gradient/throughput, checkpoint age, GPU utilization and memory;
- world-model horizon error, calibration, constraint violations, and planning regret;
- simulator queue/runtime/failure/invariant violations;
- evaluation regressions, canary deltas, alias version, rollback time;
- spend and energy proxy by tenant, task, model, adapter, and accelerator.

Progress events occur at least every 30 seconds for long jobs and at every phase
transition. Metric labels must be bounded; source URLs, prompts, and artifact
digests are not labels.

## 14. Implementation Sequence

| Phase | Feature IDs | Deliverables |
|-------|-------------|--------------|
| A | 001-004, 018-020 | Typed schemas, registries, router, evidence ingestion, staged refresh, policy, ZDD aliasing |
| B | 005-007, 016 | Dataset manifests, retrieval backends, reason/verify workflow, evaluation harness |
| C | 008-009, 017 | Adapter training, distillation, accelerator planner, checkpoint and cost controls |
| D | 010-013 | Speech, vision, and image adapters plus consent/provenance controls |
| E | 014-015 | World-model environments and four simulator-domain adapter families |
| F | all | Skill/collection integration, threat tests, load tests, canary/rollback rehearsal, documentation |

Each phase lands behind a disabled-by-default capability flag. No phase depends
on replacing an already working provider configuration.

## 15. File Plan

```text
collections/ansible_collections/general_ludd/ai_ml/
├── galaxy.yml
├── README.md
├── roles/<role>/{defaults,tasks}/main.yml
└── molecule/<role>/{converge,verify}.yml
skills/ai_ml_expert/SKILL.md
src/general_ludd/ai_ml/
├── api.py
├── schemas.py
├── router.py
├── evidence.py
├── research.py
├── registries.py
├── datasets.py
├── retrieval.py
├── reasoning.py
├── adaptation.py
├── distillation.py
├── speech.py
├── vision.py
├── images.py
├── world_models.py
├── simulators.py
├── accelerators.py
├── evaluation.py
├── promotion.py
└── policy.py
tests/unit/ai_ml/
tests/integration/ai_ml/
tests/e2e/test_ai_ml_expert.py
```

## 16. Acceptance Tests

| ID | Measurable acceptance criterion |
|----|---------------------------------|
| `AIML-AT-001` | Schema contract tests reject every invalid enum, missing digest, negative budget, and unknown mutating field |
| `AIML-AT-002` | A 100-source fixture ingests deterministically; duplicate content creates one artifact and multiple source locators |
| `AIML-AT-003` | Prompt-injection fixtures cannot alter tool permissions, policies, query scope, or approval state |
| `AIML-AT-004` | A staged knowledge update that fails one regression never changes the active alias |
| `AIML-AT-005` | Rollback test serves 100% successful requests while atomically returning to the prior snapshot within 60 seconds |
| `AIML-AT-006` | Retrieval benchmark meets suite-pinned recall@10, nDCG@10, citation precision, p95 latency, and spend thresholds |
| `AIML-AT-007` | Reasoning fixtures preserve units and pass an independent numerical check; failed checks never return `succeeded` |
| `AIML-AT-008` | Adapter load fails on a one-byte base-model digest mismatch and succeeds reproducibly with the pinned digest |
| `AIML-AT-009` | Distilled student meets suite-declared retention and safety floors; a below-floor slice blocks promotion |
| `AIML-AT-010` | ASR fixture reports final ordered segments and meets pinned WER, timestamp, diarization, and real-time-factor bounds |
| `AIML-AT-011` | TTS refuses an unconsented custom voice and emits provenance for an approved synthetic voice |
| `AIML-AT-012` | Vision fixtures return grounded regions; image edits retain source, mask, seed, model, and operation graph |
| `AIML-AT-013` | World-model fixtures report multi-horizon error and calibrated uncertainty; unsafe actuation is impossible |
| `AIML-AT-014` | Each simulator family passes schema, units, deterministic/seeded replay, limiting-case, and resource-limit tests |
| `AIML-AT-015` | A simulator timeout kills all children, emits a terminal event, and returns no scientific value |
| `AIML-AT-016` | Accelerator dry-run identifies an approved Azure A100/H100-class plan without provisioning; live path requires approval |
| `AIML-AT-017` | Preempted training resumes from the last verified checkpoint without double-counting spend |
| `AIML-AT-018` | Tool discovery produces a decision record including maintenance, license, security, forum-issue, and exit-strategy evidence |
| `AIML-AT-019` | A 30-minute job emits progress at least every 30 seconds and leaves no unbounded metric labels |
| `AIML-AT-020` | Tenant-isolation tests prove cross-tenant artifacts, indexes, voices, prompts, and traces are inaccessible |
| `AIML-AT-021` | Mutation fails closed when policy/audit storage is unavailable; eligible read-only queries are explicitly `degraded` |
| `AIML-AT-022` | Unit, integration, Molecule, E2E, security, chaos, and ZDD suites are green with >=85% aggregate and >=75% per Python file coverage |

## 17. Research Integration Gate

Before implementation status changes from `PROPOSED`, a serialized research pass
must add a source appendix with primary papers/docs and representative long-lived
forum/issue reports for every domain in Sections 5-10. Each source record must
include URL, title, author/organization, publication/update date, access date,
license where applicable, supported claim IDs, and whether reproduction was
attempted. Candidate libraries and simulators remain examples until that cited
pass confirms maintenance, licensing, API, and platform suitability.
