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
| `AIML-021` | Cross-expert workflow fabric | Compose typed, policy-bounded expert DAGs with explicit handoff and failure semantics |
| `AIML-022` | Lab-on-chip campaign bridge | Plan, simulate, observe, and optimize approved microfluidic campaigns without autonomous actuation |
| `AIML-023` | Systems diagnosis | Correlate Linux inventory, logs, traces, metrics, and approved network/eBPF observations |
| `AIML-024` | Governed research registry | Maintain fresh, licensed, correction-aware internet and offline research resources |

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
| `cross_expert_conduct` | Validate and execute typed expert DAGs with budgets, approvals, deadlines, and compensation |
| `lab_campaign_plan` | Compose chemistry, automation, vision, simulation, and active-learning campaign artifacts |
| `systems_diagnose` | Run read-mostly Linux/log/trace/network diagnosis and return evidence-linked hypotheses |

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
  "task": "question|research|dataset|train|distill|speech|vision|image|world_model|simulate|evaluate|deploy|lab_campaign|systems_diagnose",
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

### 4.4 Governed source record

A `Source` additionally records canonical URL/DOI/repository, source class,
publisher and authors, published/updated/accessed timestamps, content digest or
snapshot locator, release/commit, SPDX expression or terms URI, and separate
rights decisions for indexing, model training, quotation, and redistribution.
It also records robots/terms outcome, trust tier, supported claim IDs, correction
and retraction state, replication evidence, freshness class and deadline,
last-revalidated time, supersedes links, and a tombstone reason.

Missing rights never means permitted. A source with unknown training rights may
support citation-only retrieval but cannot enter a training corpus. Copyrighted
content is stored only to the extent permitted; otherwise the record retains
metadata, digest, permitted excerpt, and locator. Security advisories and
retractions are checked within 24 hours, tool/API records within 7 days, and
papers, standards, datasets, and benchmark records within 30 days unless a
source-specific service level is stricter. Stale evidence remains visible but
cannot solely support a high-impact claim.

### 4.5 Cross-expert handoff

Every handoff is a versioned `ExpertHandoff` containing parent/child run IDs,
producer and consumer capability/version, request/result schema digests,
artifact URIs and hashes, ontology and UCUM unit versions, assumptions,
uncertainty, evidence locators, data classification, delegated permissions,
budget/deadline/lease, approval references, retry policy, and compensation or
safe-state action. Free-form text may explain a handoff but cannot carry
authority, scientific values, executable commands, or undeclared artifacts.

The conductor validates schemas and policy before dispatch, rejects cycles and
unbounded fan-out, propagates cancellation, expires leases, and records terminal
state for every node. A consumer may refuse incompatible units, stale evidence,
unsupported fidelity, missing approvals, or an unverifiable producer result.
High-impact results require a verifier that did not author the candidate.

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

### 5.3 Continuous research loop

Self-research is a bounded, repeatable workflow: derive questions from explicit
capability gaps and failed evaluation slices; expand queries with identifiers,
citations, synonyms, and contrary hypotheses; search at least one primary-index,
official-source, code/issue, and correction/retraction channel; reconcile DOI,
version, author, dataset, and repository identities; then stop at a declared
time, source-count, novelty, or confidence budget. The output distinguishes
observed fact, source claim, model inference, unresolved conflict, and proposed
experiment.

The expert maintains coverage and disagreement maps, not a claim of universal
mastery. It may use active learning or Bayesian optimization to choose the next
evaluation or approved experiment inside a declared safe domain. It may propose
new queries, tests, tools, adapters, datasets, prompts, or training runs, but it
cannot expand its own permissions, change its evaluator, self-approve, train on
new rights-unknown data, or write an active production alias.

Open discovery connectors include
[arXiv](https://info.arxiv.org/help/api/index.html),
[Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/),
[OpenAlex](https://developers.openalex.org/api-reference/introduction), and
[Semantic Scholar](https://www.semanticscholar.org/product/api). Connectors pin
API/schema versions, identify terms and rate limits, cache resumable snapshots,
and reconcile records across providers; no one metadata provider is authoritative.

### 5.4 Self-improvement proposals

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

Every answer records the query rewrite, index version, filter policy, retrieved
source IDs, scores, reranker version, and chosen citation spans. Raw confidential
queries are encrypted and excluded from training unless explicit consent exists.

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

## 14. Cross-Expert Operational Fabric

### 14.1 Composition protocol

The conductor compiles a request into a typed, acyclic execution graph. Every
node declares required and provided capabilities, schemas, evidence and
freshness needs, permissions, side-effect class, resources, deadline, retry
limit, idempotency key, and compensation action. Admission simulates the graph's
maximum cost and privilege envelope before any node runs. A child receives only
the artifacts and short-lived capabilities its schema requires.

Parallel branches join only when declared invariants pass. Partial success never
masquerades as complete success: the result names skipped, refused, cancelled,
and degraded nodes. Retries are limited to idempotent operations or use a
deduplication token. Cancellation and deadline expiry flow to descendants;
leases and facility reservations are released even when audit publication
fails. The graph and each state transition are replayable from immutable events,
but secrets and restricted payloads remain referenced rather than copied.

Experts can request one another by capability rather than implementation name.
For example, a chemistry campaign can request materials compatibility, a
microfluidic simulator, vision-based observation, and statistical design. A
Linux expert can request a network-observation capability, but the conductor
does not grant packet capture, root, eBPF attachment, or remediation rights
unless the caller already has an approval scoped to the host, interface,
duration, and data class.

### 14.2 Lab-on-chip campaign workflow

The AI/ML expert may optimize and interpret a lab-on-chip campaign; the
chemistry expert remains the domain and safety authority, and a qualified human
remains the actuation authority. The interoperable lifecycle is:

1. Define objective, safe search domain, materials/fluid compatibility,
   response variables, uncertainty target, sample/waste limits, and stop rules.
2. Compile a versioned protocol graph and simulate geometry, timing, volumes,
   pressure/flow, mixing, carryover, sensor range, and failure states.
3. Obtain chemistry hazard review, facility capability decision, instrument
   reservation/lease, calibration, inventory, and exact-protocol approval.
4. Produce a vendor-neutral command plan; a reviewed adapter may translate it
   to SiLA 2, Opentrons, DropBot, Fluigent, or another pinned capability.
5. Require a short-lived human arm token before transmission. Stream immutable
   commanded and observed state separately and compare each bounded step.
6. Pause on drift, bubble, clog, leak, pressure, temperature, volume, image,
   contamination, sensor, heartbeat, or interlock limits; execute the declared
   safe-state/containment action without asking a model for improvisation.
7. Link raw observations, microscopy, calibration, deviations, and derived
   values. An active learner may propose the next point inside the approved
   domain, but cannot approve or transmit it.
8. End with verified safe state, released lease, material/waste accounting,
   signed electronic-lab record, and an explicit incomplete state if evidence
   is missing.

Command plans identify device/firmware/adapter digests, channel and well maps,
UCUM units, coordinate frame, consumables/lots, calibration, pre/postconditions,
idempotency, acknowledgement timeout, maximum retries, and abort/safe-state
semantics. Digital twins and simulators are advisory: mismatch, novelty, or
out-of-distribution uncertainty tightens limits or blocks execution.

### 14.3 Linux, log, and network diagnosis

Systems diagnosis starts read-only and follows least-invasive evidence order:
inventory and declared change history; service health and bounded journal/log
windows; metrics and traces; configuration and dependency versions; then
approved osquery, Zeek, packet metadata, or eBPF probes. OpenTelemetry schema
and semantic-convention versions are recorded so field drift is explicit.

The result is a ranked hypothesis graph. Each hypothesis links supporting and
contrary events, synchronized clock assumptions, missing telemetry, confidence,
and one bounded discriminating probe. Packet content is sensitive and disabled
by default; capture filters, interface, byte/duration limits, retention, and
redaction are admission inputs. Active network traffic, root access, eBPF
attachment, service restart, configuration mutation, and release rollback are
separate approval-bound capabilities.

For an intermittent HTTP 5xx case, the E2E fixture must correlate request and
trace IDs across gateway/application/database logs, resource saturation,
deployment and dependency versions, and scoped network symptoms; distinguish
DNS, TLS, loss/retransmit, queue, application, and downstream hypotheses; invoke
a network observer only when existing telemetry cannot discriminate; propose a
reversible change through the release/operations expert; and verify recovery
and rollback criteria without exposing payloads.

## 15. Research and Evidence Governance

The source registry is itself an evaluated expert asset. Scheduled refresh
produces a diff of new, changed, stale, corrected, retracted, inaccessible, and
rights-changed records. A source disappearance does not erase its evidence;
policy determines whether a permitted snapshot can remain, otherwise only
metadata and prior claim impact are retained. Correction and retraction events
open an impact graph over answers, datasets, evaluations, models, adapters, and
active aliases.

Source quality is claim-specific. Peer review does not prove code reproducibility;
official documentation does not prove field reliability; a forum report does
not establish causality. The registry labels each source as primary research,
standard, official documentation, maintained code, benchmark/dataset, secondary
analysis, or operator report and requires independent support for high-impact
claims. Forum and issue evidence creates regression fixtures and operational
mitigations; it never overrides a standard, safety rule, or validated primary
result.

Knowledge refreshes publish coverage, citation precision, contradictory-claim
recall, correction/retraction latency, source diversity, rights completeness,
freshness, and answer-regression results. An update with improved aggregate
score but a newly failing safety, licensing, citation, or protected evaluation
slice is rejected.

## 16. Implementation Sequence

| Phase | Feature IDs | Deliverables |
|-------|-------------|--------------|
| A | 001-004, 018-021, 024 | Typed schemas, registries, router, evidence ingestion, expert DAGs, staged refresh, policy, ZDD aliasing |
| B | 005-007, 016 | Dataset manifests, retrieval backends, reason/verify workflow, evaluation harness |
| C | 008-009, 017 | Adapter training, distillation, accelerator planner, checkpoint and cost controls |
| D | 010-013 | Speech, vision, and image adapters plus consent/provenance controls |
| E | 014-015 | World-model environments and four simulator-domain adapter families |
| F | 021-023 and all | Cross-expert, lab campaign, systems-diagnosis, threat, load, canary/rollback, and documentation suites |

Each phase lands behind a disabled-by-default capability flag. No phase depends
on replacing an already working provider configuration.

## 17. File Plan

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
├── resources.py
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
├── orchestration.py
├── lab_automation.py
├── systems_diagnosis.py
├── accelerators.py
├── evaluation.py
├── promotion.py
└── policy.py
tests/unit/ai_ml/
tests/integration/ai_ml/
tests/e2e/test_ai_ml_expert.py
```

## 18. Acceptance Tests

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
| `AIML-AT-023` | Source-record property tests reject absent dates, unresolved rights, missing claim links, invalid freshness, and mutable snapshots |
| `AIML-AT-024` | A correction/retraction fixture is detected within its 24-hour SLO and blocks every impacted high-impact answer and alias |
| `AIML-AT-025` | Merged LoRA/QLoRA candidates are compared with the unmerged adapter on logits and every protected task slice; any pinned drift breach blocks promotion |
| `AIML-AT-026` | Hybrid retrieval fixtures recover lexical and semantic evidence, surface a contradictory primary source, and cite exact spans without one-provider dependence |
| `AIML-AT-027` | Silence, music, noise, repeated-token, and code-switch ASR fixtures stay within suite-pinned hallucination, timestamp, and loop bounds |
| `AIML-AT-028` | Generated and edited image fixtures retain the immutable source graph and validate C2PA provenance; stripped or invalid provenance is explicit |
| `AIML-AT-029` | World-model and simulator fixtures detect an out-of-domain state, refuse actuation, and preserve the last validated physical state |
| `AIML-AT-030` | Expert-DAG tests reject cycles, schema/unit mismatch, privilege escalation, unbounded fan-out, expired leases, and undeclared artifacts |
| `AIML-AT-031` | Cancelling a fan-out workflow reaches every child, runs each declared compensation once, releases leases, and emits one terminal state per node |
| `AIML-AT-032` | A lab-on-chip dry run covers calibration, simulation, approval binding, command/observation separation, device timeout, clog/bubble drift, abort, safe state, and signed record without real actuation |
| `AIML-AT-033` | An active learner proposes only points inside the approved safe domain and cannot approve, arm, transmit, or widen that domain |
| `AIML-AT-034` | The intermittent-5xx E2E fixture correlates logs/metrics/traces, requests only a scoped network probe, redacts payloads, and routes mutation to an approved operations expert |
| `AIML-AT-035` | Offline research replay uses the declared immutable snapshot, marks freshness degradation, and produces the same evidence ordering and claim links |
| `AIML-AT-036` | A self-research proposal can add queries and tests but cannot change permissions, evaluator, training rights, active aliases, or approval state |

## 19. Research Integration Gate

Section 20 satisfies the initial serialized research baseline, not implementation
selection. Before status changes from `PROPOSED`, the implementation branch must
refresh every selected record, pin exact versions/commits and dependency
digests, resolve redistribution/training rights, reproduce the relevant example
or benchmark in the target environment, and attach the artifacts to acceptance
tests. Named tools remain candidates until those checks pass.

## 20. Cited Research Baseline

### 20.1 Record conventions

All records below were accessed on 2026-07-30. `Screened` means the linked
landing page/metadata showed no correction or retraction notice during this
research pass; it is not a permanent integrity guarantee. `N/A` is used for
living standards, documentation, or code where retraction does not apply.
`Citation only` means publisher/site terms govern the content and no training or
redistribution right is inferred. Reproduction was not attempted in this
documentation-only pass (`repro: no`) and is a hard implementation gate.

### 20.2 Adaptation, retrieval, reasoning, and self-research

| Source ID | Source, steward, publication/update date | Supports | Rights, integrity, reproduction |
|-----------|------------------------------------------|----------|---------------------------------|
| `AI-SRC-LORA` | [LoRA: Low-Rank Adaptation](https://arxiv.org/abs/2106.09685), Hu et al., 2021 | `AIML-008`, adapter rank/base binding | Citation only; screened; repro: no |
| `AI-SRC-QLORA` | [QLoRA](https://arxiv.org/abs/2305.14314), Dettmers et al., 2023 | `AIML-008`, quantized adaptation | Citation only; screened; repro: no |
| `AI-SRC-KD` | [Distilling the Knowledge in a Neural Network](https://arxiv.org/abs/1503.02531), Hinton et al., 2015 | `AIML-009`, student retention | Citation only; screened; repro: no |
| `AI-SRC-RAG` | [Retrieval-Augmented Generation](https://arxiv.org/abs/2005.11401), Lewis et al., 2020 | `AIML-006`, provenance-aware retrieval | Citation only; screened; repro: no |
| `AI-SRC-COLBERT` | [ColBERT](https://arxiv.org/abs/2004.12832), Khattab and Zaharia, 2020 | `AIML-006`, late-interaction retrieval | Citation only; screened; repro: no |
| `AI-SRC-COT` | [Chain-of-Thought Prompting](https://arxiv.org/abs/2201.11903), Wei et al., 2022 | `AIML-007`, reasoning evaluation | Citation only; screened; repro: no |
| `AI-SRC-SC` | [Self-Consistency Improves Chain of Thought Reasoning](https://arxiv.org/abs/2203.11171), Wang et al., 2022 | `AIML-007`, sampled verification | Citation only; screened; repro: no |
| `AI-SRC-REACT` | [ReAct](https://arxiv.org/abs/2210.03629), Yao et al., 2022 | `AIML-007`, bounded act/observe loop | Citation only; screened; repro: no |
| `AI-SRC-PRM` | [Let's Verify Step by Step](https://arxiv.org/abs/2305.20050), Lightman et al., 2023 | `AIML-007`, process supervision | Citation only; screened; repro: no |
| `AI-SRC-REFLEXION` | [Reflexion](https://arxiv.org/abs/2303.11366), Shinn et al., 2023 | `AIML-004`, episodic improvement candidate | Citation only; screened; repro: no |
| `AI-SRC-PAPERQA` | [PaperQA](https://arxiv.org/abs/2312.07559), Lála et al., 2023 | `AIML-002`, literature QA evaluation | Citation only; screened; repro: no |
| `AI-SRC-PAPERQA2` | [Language agents achieve superhuman synthesis of scientific knowledge](https://arxiv.org/abs/2409.13740), Skarlinski et al., 2024 | `AIML-002`, agentic search benchmark | Citation only; screened; repro: no |
| `AI-SRC-AISCI` | [The AI Scientist](https://arxiv.org/abs/2408.06292), Lu et al., 2024 | `AIML-004`, candidate research loop, not autonomous authority | Citation only; screened; repro: no |
| `AI-SRC-BO` | [A Tutorial on Bayesian Optimization](https://arxiv.org/abs/1807.02811), Frazier, 2018 | `AIML-004`, bounded experiment selection | Citation only; screened; repro: no |

These methods are hypotheses to evaluate, not universal defaults. In particular,
the implementation must compare concise tool-verifiable rationales against
direct answering, sampling, retrieval, and external solvers by task slice; it
must not expose private token-level reasoning or treat longer reasoning as more
correct.

### 20.3 Data, provenance, discovery, and supply chain

| Source ID | Source, steward, publication/update date | Supports | Rights, integrity, reproduction |
|-----------|------------------------------------------|----------|---------------------------------|
| `AI-SRC-ARROW` | [Arrow Columnar Format](https://arrow.apache.org/docs/format/Columnar.html), Apache Arrow, rolling docs | `AIML-005`, columnar interchange | Apache-2.0 project; N/A; repro: no |
| `AI-SRC-PARQUET` | [Apache Parquet documentation](https://parquet.apache.org/docs/), Apache, rolling docs | `AIML-005`, persisted columnar datasets | Apache-2.0 project; N/A; repro: no |
| `AI-SRC-ZARR` | [OGC Zarr Storage Specification](https://www.ogc.org/standards/zarr-storage-specification/), OGC, living standard | `AIML-005`, chunked arrays | OGC terms; N/A; repro: no |
| `AI-SRC-CROISSANT` | [Croissant specification](https://docs.mlcommons.org/croissant/docs/croissant-spec.html), MLCommons, living spec | `AIML-005`, dataset metadata | Specification terms; N/A; repro: no |
| `AI-SRC-PROV` | [PROV-O](https://www.w3.org/TR/prov-o/), W3C, 2013 | `AIML-003`, provenance graph | W3C document terms; N/A; repro: no |
| `AI-SRC-ROCRATE` | [RO-Crate 1.3](https://www.researchobject.org/ro-crate/specification/1.3/index.html), RO-Crate Community, 2026-06-22 | `AIML-003`, portable research packages | Apache-2.0 specification; N/A; repro: no |
| `AI-SRC-SLSA` | [SLSA Provenance v1.2](https://slsa.dev/spec/v1.2/provenance), OpenSSF, current v1.2 | `AIML-003`, build provenance | Specification terms; N/A; repro: no |
| `AI-SRC-SPDX` | [SPDX License List](https://spdx.org/licenses/), Linux Foundation, rolling | `AIML-003`, machine-readable licensing | Published SPDX terms; N/A; repro: no |
| `AI-SRC-MODELCARDS` | [Model Cards for Model Reporting](https://arxiv.org/abs/1810.03993), Mitchell et al., 2018 | `AIML-005`, `AIML-016`, model documentation | Citation only; screened; repro: no |
| `AI-SRC-ARXIVAPI` | [arXiv API documentation](https://info.arxiv.org/help/api/index.html), arXiv, rolling docs | `AIML-002`, paper discovery | API/site terms; N/A; repro: no |
| `AI-SRC-CROSSREF` | [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/), Crossref, rolling docs | `AIML-002`, DOI metadata | API/site terms; N/A; repro: no |
| `AI-SRC-OPENALEX` | [OpenAlex API](https://developers.openalex.org/api-reference/introduction), OurResearch, rolling docs | `AIML-002`, scholarly graph | API/data terms; N/A; repro: no |
| `AI-SRC-S2` | [Semantic Scholar API](https://www.semanticscholar.org/product/api), Allen Institute for AI, rolling docs | `AIML-002`, citation discovery | API/site terms; N/A; repro: no |

### 20.4 Speech, vision, image provenance, and world models

| Source ID | Source, steward, publication/update date | Supports | Rights, integrity, reproduction |
|-----------|------------------------------------------|----------|---------------------------------|
| `AI-SRC-WHISPER` | [Whisper](https://openai.com/index/whisper/), OpenAI, 2022 | `AIML-010`, multilingual ASR baseline | Site/paper terms; screened; repro: no |
| `AI-SRC-VITS` | [Conditional Variational Autoencoder with Adversarial Learning for End-to-End TTS](https://arxiv.org/abs/2106.06103), Kim et al., 2021 | `AIML-011`, TTS architecture evidence | Citation only; screened; repro: no |
| `AI-SRC-CLIP` | [Learning Transferable Visual Models From Natural Language Supervision](https://arxiv.org/abs/2103.00020), Radford et al., 2021 | `AIML-012`, multimodal retrieval | Citation only; screened; repro: no |
| `AI-SRC-SAM` | [Segment Anything](https://arxiv.org/abs/2304.02643), Kirillov et al., 2023 | `AIML-012`, segmentation | Citation only; screened; repro: no |
| `AI-SRC-LDM` | [High-Resolution Image Synthesis with Latent Diffusion Models](https://arxiv.org/abs/2112.10752), Rombach et al., 2021 | `AIML-013`, image generation/editing | Citation only; screened; repro: no |
| `AI-SRC-C2PA` | [C2PA technical specifications](https://spec.c2pa.org/specifications/), C2PA, living specs | `AIML-013`, content provenance | Specification terms; N/A; repro: no |
| `AI-SRC-DREAMER3` | [Mastering Diverse Domains through World Models](https://arxiv.org/abs/2301.04104), Hafner et al., 2023 | `AIML-014`, latent dynamics | Citation only; screened; repro: no |
| `AI-SRC-MUZERO` | [Mastering Atari, Go, Chess and Shogi by Planning with a Learned Model](https://arxiv.org/abs/1911.08265), Schrittwieser et al., 2019 | `AIML-014`, learned planning | Citation only; screened; repro: no |
| `AI-SRC-PINN` | [Physics-Informed Neural Networks](https://arxiv.org/abs/1711.10561), Raissi et al., 2017 | `AIML-014`, scientific learned models | Citation only; screened; repro: no |

### 20.5 Scientific simulators and cross-domain operations

| Source ID | Source, steward, publication/update date | Supports | Rights, integrity, reproduction |
|-----------|------------------------------------------|----------|---------------------------------|
| `AI-SRC-MUJOCO` | [MuJoCo](https://github.com/google-deepmind/mujoco), Google DeepMind, rolling code | `AIML-015`, rigid-body simulation | Apache-2.0; N/A; repro: no |
| `AI-SRC-OPENMM` | [OpenMM user guide](https://docs.openmm.org/latest/userguide/introduction.html), OpenMM, rolling docs | `AIML-015`, molecular simulation | Project/site terms; N/A; repro: no |
| `AI-SRC-CANTERA` | [Cantera science reference](https://cantera.org/stable/reference/), Cantera, rolling docs | `AIML-015`, kinetics/thermodynamics | Project/site terms; N/A; repro: no |
| `AI-SRC-NGSPICE` | [ngspice documentation](https://ngspice.sourceforge.io/docs.html), ngspice, rolling docs | `AIML-015`, electronics simulation | Project/site terms; N/A; repro: no |
| `AI-SRC-REBOUND` | [REBOUND](https://rebound.hanno-rein.de/), Rein and contributors, rolling docs | `AIML-015`, N-body simulation | GPL project; N/A; repro: no |
| `AI-SRC-ASTROPY` | [Astropy documentation](https://docs.astropy.org/en/stable/index.html), Astropy, rolling docs | `AIML-015`, astronomy units/time/coordinates | BSD project; N/A; repro: no |
| `AI-SRC-SILA` | [SiLA 2 base documentation](https://sila2.gitlab.io/sila_base/), SiLA Consortium, rolling docs | `AIML-021`, `AIML-022`, lab device interoperability | Project/spec terms; N/A; repro: no |
| `AI-SRC-OPENTRONS` | [Opentrons Python Protocol API](https://opentrons.com/pythonapi), Opentrons, rolling docs | `AIML-022`, simulated liquid handling | API/site terms; N/A; repro: no |
| `AI-SRC-PAML` | [PAML: Protocol Activity Markup Language](https://www.biorxiv.org/content/10.1101/2022.07.05.498808v1), Myers et al., 2022 | `AIML-022`, protocol graphs | Publisher terms; screened; repro: no |
| `AI-SRC-DROPBOT` | [DropBot](https://microfluidics.utoronto.ca/dropbot/), Wheeler Microfluidics Laboratory, rolling docs | `AIML-022`, digital microfluidics feedback | Site/project terms; N/A; repro: no |
| `AI-SRC-FLUIGENT` | [Fluigent SDK manual](https://store.fluigent.com/wp-content/uploads/2021/06/Fluigent-SDK-User-Manual.pdf), Fluigent, 2021 | `AIML-022`, pressure/flow adapter evidence | Vendor terms; N/A; repro: no |
| `AI-SRC-OPENFLEX` | [OpenFlexure Microscope documentation](https://openflexure.org/projects/microscope/documentation), OpenFlexure, rolling docs | `AIML-022`, microscope observation | Project/site terms; N/A; repro: no |
| `AI-SRC-DMFML` | [Machine learning for digital microfluidics](https://pubs.rsc.org/en/content/articlelanding/2023/lc/d2lc00764a), RSC authors, 2023 | `AIML-022`, adaptive microfluidic control evidence | Citation only; screened; repro: no |
| `AI-SRC-OTELLOG` | [OpenTelemetry Logs data model](https://opentelemetry.io/docs/specs/otel/logs/data-model/), OpenTelemetry, rolling spec | `AIML-023`, normalized logs | Apache-2.0 project; N/A; repro: no |
| `AI-SRC-OTELSEM` | [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/), OpenTelemetry, rolling spec | `AIML-023`, telemetry schema versions | Apache-2.0 project; N/A; repro: no |
| `AI-SRC-OSQUERY` | [osquery documentation](https://osquery.readthedocs.io/en/stable/), osquery, rolling docs | `AIML-023`, read-mostly host evidence | Project terms; N/A; repro: no |
| `AI-SRC-ZEEK` | [Zeek log files](https://docs.zeek.org/en/current/logs/), Zeek, rolling docs | `AIML-023`, network observations | Project terms; N/A; repro: no |
| `AI-SRC-EBPF` | [Linux eBPF documentation](https://docs.kernel.org/bpf/), Linux kernel, rolling docs | `AIML-023`, scoped kernel telemetry | Kernel documentation terms; N/A; repro: no |
| `AI-SRC-BPFTRACE` | [bpftrace documentation](https://bpftrace.org/), bpftrace, rolling docs | `AIML-023`, bounded probes and privilege risk | Project terms; N/A; repro: no |

### 20.6 Operator evidence and required regression fixtures

Operator reports are untrusted observations. They establish failure hypotheses
and regression tests, not product defects or general causal conclusions.

| Evidence ID | Report and observed date | Required implementation response |
|-------------|--------------------------|----------------------------------|
| `AI-OPS-PEFT-MERGE` | [PEFT issue 1836](https://github.com/huggingface/peft/issues/1836), reported 2024, observed 2026-07-30 | Compare merged and unmerged quantized adapters on logits and protected slices before promotion |
| `AI-OPS-WHISPER-SILENCE` | [Whisper discussion 1783](https://github.com/openai/whisper/discussions/1783), reported 2023, observed 2026-07-30 | Add VAD, silence/music/noise, repeated-token, timestamp, and hallucination fixtures |
| `AI-OPS-FAISS-OOM` | [FAISS issue 4222](https://github.com/facebookresearch/faiss/issues/4222), reported 2025, observed 2026-07-30 | Bound GPU temporary memory, ingestion batches, cleanup, and CPU/degraded fallback |
| `AI-OPS-FAISS-UM` | [FAISS issue 474](https://github.com/facebookresearch/faiss/issues/474), reported 2018, observed 2026-07-30 | Never assume unified-memory oversubscription; capability-probe capacity before index placement |
| `AI-OPS-OPENTRONS` | [Opentrons field discussion](https://www.reddit.com/r/labrats/comments/10jllne/), reported 2023, observed 2026-07-30 | Pin API/runtime compatibility and simulate alignment, calibration, low-volume, and vague-error cases |
| `AI-OPS-OTEL-DRIFT` | [OpenTelemetry convention packaging discussion](https://www.reddit.com/r/OpenTelemetry/comments/1uj0nrw/why_arent_the_otel_semantic_conventions_shipped/), observed 2026-07-30 | Pin semantic-convention schema, retain original fields, and test explicit migrations |
