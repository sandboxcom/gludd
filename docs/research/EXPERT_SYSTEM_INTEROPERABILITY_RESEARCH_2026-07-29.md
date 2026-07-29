# Expert-System Interoperability Research

Status: research synthesis for implementation specifications  
Research cutoff: 2026-07-29  
Target: post-`v0.1.0-beta.3` development work  
Release impact: none; this document was produced on the isolated
`research-expert-expansion-2026` branch and is not a beta.3 input.

## 1. Research question

How should gludd's domain experts operate as one dependable system rather than
as isolated prompts?

The design must let experts:

- publish machine-verifiable capability contracts;
- route and hand off work without widening authority;
- plan jointly without colliding on files, resources, or external effects;
- share evidence and useful memory without cross-tenant leakage or prompt
  injection;
- preserve disagreement and arbitrate it using evidence rather than model
  confidence or majority alone;
- contain loops, crashes, retries, compromised experts, and partial failure;
- evaluate the team as a stochastic distributed system; and
- discover improvements without allowing internet content or a model to promote
  its own code, policy, memory, or permissions.

The companion implementation specification is
`docs/specs/FEATURE_EXPERT_SYSTEM_INTEROPERABILITY.md`.

## 2. Evidence policy

Sources are classified as:

| Class | Meaning | Use |
|---|---|---|
| `standard` | Normative specification or government framework | Protocol, security, and provenance baseline |
| `primary` | Peer-reviewed paper, primary technical report, or author publication | Architecture and measured results within the evaluated scope |
| `maintainer` | Current official project documentation | Current interfaces, documented constraints, and version behavior |
| `operational` | Maintainer issue/discussion or practitioner report | Regressions and failure hypotheses, never scientific authority |
| `watchlist` | Recent preprint or proposal without sufficient independent replication | Candidate experiments only |

All sources below were retrieved on 2026-07-29. Claims about “state of the art”
expire and must be re-evaluated against current versions, licenses, hardware,
budgets, and held-out tests.

## 3. Standards and protocol findings

### 3.1 Agent-to-agent tasks

The current [A2A Protocol v1.0 specification](https://a2a-protocol.org/latest/specification/)
provides a useful interoperable substrate:

- versioned Agent Cards describe identity, capabilities, skills, media modes,
  interfaces, and security requirements, and MAY carry JWS signatures;
- tasks have explicit states, messages, artifacts, cancellation, streaming, and
  subscription;
- clients send an `A2A-Version` and servers fail on unsupported versions;
- capability support is checked before optional operations;
- `Send Message` is only optionally idempotent, duplicate messages may be
  detected by `messageId`, and webhook consumers must tolerate duplicate
  delivery;
- task history need not retain every message, reconnecting stream clients may
  miss status messages, and messages are explicitly not reliable delivery for
  critical information;
- signed Agent Cards use RFC 8785 canonicalization before signing; and
- production security includes authentication, authorization, tenant scoping,
  resource limits, webhook validation, and input validation.

Design implication: gludd should not invent a text-only handoff format. Its
internal contract should be a strict superset of the A2A task/artifact/card
model, with adapters at the boundary. Natural-language task prompts remain a
payload, not the protocol. Gludd deliberately strengthens the baseline by
requiring trusted cards to be signed, persisting critical task state outside
messages, and making task/effect idempotency mandatory.

A2A does not solve shared multi-writer memory, evidence arbitration, resource
locking, or multi-principal governance. The April 2026
[MPAC preprint](https://arxiv.org/abs/2604.09744) proposes intent, operation,
conflict, governance, causal ordering, and optimistic concurrency layers. It is
useful watchlist evidence, but gludd should implement the required semantics
using established distributed-systems primitives and local tests rather than
adopting an unreplicated protocol wholesale.

### 3.1.1 Schema dialect and evolution

[JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12) supplies a
declared meta-schema/dialect, references, vocabularies, tuple validation, and
unevaluated-property controls. A schema URI without pinned bytes/digest is not
an immutable contract.

Current
[schema-evolution guidance](https://docs.confluent.io/platform/7.7/schema-registry/fundamentals/schema-evolution.html)
distinguishes backward, forward, full, and transitive compatibility. Syntax-only
compatibility does not establish that a new field preserves authorization,
units, terminal-state, or effect semantics.

Design implication:

- every schema has an immutable `$id`, declared 2020-12 dialect, digest,
  signature, owner, compatibility policy, and conformance fixtures;
- validators use pinned local schemas and never dereference model-supplied
  references at runtime;
- durable task/event formats require explicit N/N-1 producer/consumer tests and
  transitive readers for every retained historical version;
- breaking semantics use a new major schema/type plus migration/adapter; and
- unknown optional domain data may be namespaced/preserved, but unknown
  capability, authorization, risk, effect, or terminal-state fields fail closed.

### 3.2 Tool and context interoperability

The current [Model Context Protocol specification dated 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
separates resources, prompts, and tools and includes progress, cancellation,
errors, and logging. Its
[security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
make several boundaries explicit:

- per-client consent is required to avoid confused-deputy behavior;
- token passthrough is forbidden;
- access tokens must be audience-bound to the MCP server;
- authorization and sessions are separate;
- SSRF defenses apply to discovered URLs, redirects, metadata endpoints, and
  webhook-like flows; and
- session identifiers must not authenticate callers.

Design implication: an expert may use MCP tools, but a handoff cannot transfer
the sender's bearer token. Gludd must mint narrower, audience-bound credentials
and enforce authorization outside the model.

### 3.2.1 Delegated identity and resource-bound authority

[RFC 8693 OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693.html)
standardizes an STS exchange with distinct subject and actor tokens. Its
delegation form preserves that actor A acts on behalf of subject B, including an
`act` chain, while impersonation can make A indistinguishable from B. The RFC
also notes that output-token revocation is not automatically coupled to input
token revocation.

[RFC 8707 Resource Indicators](https://www.rfc-editor.org/info/rfc8707/)
lets a client request a token for a specific protected resource. The standard
recommends the most specific resource URI, preferably one resource, and calls
out tenant-specific resource identities as a cross-tenant defense.

[SPIFFE workload identities](https://spiffe.io/docs/latest/spiffe-about/spiffe-concepts/)
provide short-lived, automatically rotated identities for software workloads,
separate from end-user identity.

Design implication:

- expert handoffs use delegation, not identity-erasing impersonation;
- audit preserves user/initiator, every delegated actor, workload, and target;
- child credentials are short-lived, one-resource/audience tokens whose scope
  is the existing capability intersection;
- cancellation, suspension, and parent revocation explicitly revoke
  descendants because token exchange does not provide that automatically; and
- workload identity authenticates the worker, while the task capability token
  authorizes the specific action.

### 3.2.2 Typed interoperable failures

[RFC 9457 Problem Details for HTTP APIs](https://www.rfc-editor.org/info/rfc9457/)
defines `application/problem+json`, a stable problem `type` URI, an occurrence
`instance`, and human-facing `title`/`detail`. Consumers are told to use the type
as the primary identifier and not parse the human detail. The security section
warns against exposing stack traces and implementation internals.

Design implication: HTTP expert adapters use Problem Details with stable,
documented types and typed extensions for reason code, retryability, expected
state/schema, and evidence reference. Models and coordinators never branch on
human error prose, and forbidden/not-found responses do not disclose cross-scope
existence.

### 3.3 Provenance, signatures, and tracing

[W3C PROV-O](https://www.w3.org/TR/prov-o/) is a stable provenance model based
on `Entity`, `Activity`, and `Agent`, including derivation, generation, usage,
association, and responsibility. It can represent:

- a source document or artifact as an Entity;
- retrieval, calculation, model inference, arbitration, or transformation as an
  Activity; and
- the human, expert role, model build, tool, or service responsible as an Agent.

[RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html)
provides deterministic JSON bytes for hashing and signatures. Implementations
must also account for the RFC's verified technical erratum concerning negative
zero.

[W3C Trace Context](https://www.w3.org/TR/trace-context/) supplies interoperable
`traceparent` and `tracestate` propagation. It explicitly forbids putting
personally identifiable information in trace headers.

[Open Policy Agent decision logs](https://www.openpolicyagent.org/docs/management-decision-logs)
record the policy path, input, result, policy-bundle revision, decision ID, and
trace IDs, while supporting policy-driven masking of sensitive fields.
[Signed OPA bundles](https://www.openpolicyagent.org/docs/management-bundles)
fail activation when verification fails and retain the prior policy.

Design implication: expert evidence needs two linked but distinct graphs:

1. a domain claim/evidence graph; and
2. an operational trace of who retrieved, transformed, reviewed, authorized,
   and emitted each artifact.

Neither a transcript nor an LLM-generated citation list is sufficient.

### 3.3.1 Media and AI-content provenance

The current
[C2PA Content Credentials 2.4 specification](https://spec.c2pa.org/specifications/specifications/2.4/specs/C2PA_Specification.html)
provides signed, tamper-evident provenance for images, audio, video, documents,
and structured text. Its hard bindings associate a manifest with exact asset
bytes. Soft bindings such as approved fingerprints or watermarks can help locate
a manifest after transcoding or metadata stripping, but are deliberately not a
substitute for a cryptographic hard binding.

C2PA 2.4 adds a machine-readable `c2pa.ai-disclosure` assertion for model type,
model identity, scientific domain, and declared human-oversight level. Its
actions and version-3 ingredient assertions can describe creation, editing,
transcoding, composition, model inputs, and parent/derived relationships. The
specification's `crJSON` form is a derived view for profile evaluation,
interoperability testing, and validation reporting; it is not independently
verifiable and must never replace validation of the signed manifest.

The official
[C2PA explainer](https://spec.c2pa.org/specifications/specifications/2.4/explainer/Explainer.html)
draws an essential boundary: valid provenance can establish that identified
signers made tamper-evident claims about an asset, but cannot establish that the
asset or claims are factually true. The
[soft-binding API](https://spec.c2pa.org/specifications/specifications/2.4/softbinding/Decoupled.html)
supports recovery of decoupled manifests, while the
[security guidance](https://spec.c2pa.org/specifications/specifications/2.4/security/Security_Considerations.html)
treats manifest stripping, repository lookup, privacy, and trust-list validation
as explicit threats rather than solved assumptions.

The
[C2PA 2.4 AI/ML guidance](https://spec.c2pa.org/specifications/specifications/2.4/ai-ml/ai_ml.html)
extends this lineage to dataset partitions, training and inference software,
multi-file model assets, fine-tuned models, LoRA/PEFT adapters, and model
outputs. A fine-tuned model is derived from both its base model and fine-tuning
dataset; a separately distributed adapter does not erase that dependency.
C2PA credentials complement, rather than replace, the in-toto and SPDX
supply-chain evidence below.

Design implication:

- a generated or modified media artifact gets a new hard-bound active manifest,
  action history, parent/input ingredients, and exact generator/model revision
  when the output format and approved implementation support C2PA;
- `digitalSourceType` and AI disclosure identify trained-model use and declared
  oversight without storing private chain-of-thought, secret prompts, personal
  data, credentials, or disallowed source content;
- crop, re-encode, synthesis, compositing, and format conversion create a new
  derived artifact and digest; an old manifest is never copied as if it still
  hard-bound the transformed bytes;
- an external manifest, repository receipt, or soft-binding recovery is retained
  and visibly labelled with its recovery method when embedded metadata is lost;
- validation covers the asset binding, assertions, ingredients, certificate
  path, trust list, revocation, and time evidence, not only a signature bit;
- C2PA validity does not satisfy factual verification, safety, copyright,
  license, likeness/voice consent, or action-authorization gates, and missing
  C2PA metadata is not evidence that content is false or AI-generated;
- privacy and creator control remain policy constraints, including an
  explainable option not to embed identity-bearing provenance; and
- implementation should use a maintained C2PA 2.4 library and official
  conformance assets rather than a gludd-specific parser or signature format;
  and
- model, dataset, shard, adapter, fine-tune, distillation, merge, and
  quantization handoffs retain base/input identities, partition and collection
  digests, code/environment/configuration lineage, transformations, and
  evaluation evidence.

### 3.4 Software, model, and dataset supply chains

The current [in-toto Attestation Framework v1.2](https://github.com/in-toto/attestation/blob/main/spec/README.md)
separates an attestation into a predicate, a statement binding that predicate to
an immutable subject, an authenticated envelope, and a bundle. That structure
supports policy evaluation without inventing another generic attestation
container.

[SPDX 3.0.1](https://spdx.github.io/spdx-spec/v3.0.1/model/AI/AI/) includes AI,
Dataset, Build, Core, and Licensing profiles. Its AI profile covers model
capability, training, data handling, limitations, explainability, and energy;
the Dataset and Build profiles cover input-data and build identities.

The [OpenSSF Model Signing project](https://openssf.org/projects/model-signing/)
supports format-independent signing of large model artifacts and verification
when a model is uploaded, selected for deployment, or consumed by another
model.

Design implication:

- expert code, cards, policies, prompts, tools, models, datasets, and evaluation
  bundles need digest-bound in-toto attestations;
- AI and dataset bills of materials should use SPDX profiles rather than a
  gludd-only metadata vocabulary;
- large model weights should use a maintained model-signing implementation;
- a valid signature proves integrity and signer identity, not authorization,
  quality, safety, license compatibility, or fitness for a task; and
- policy must bind accepted builders, source/material digests, build type,
  expected tests, licenses, card revision, and target environment.

## 4. Multi-agent architecture findings

### 4.1 Orchestrator-worker systems

Microsoft's November 2024
[Magentic-One technical report](https://www.microsoft.com/en-us/research/publication/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/)
uses an orchestrator that plans, tracks progress, replans, and delegates to
specialists. Its AutoGenBench isolates repeated stochastic trials and their side
effects.

Anthropic's June 2025
[multi-agent research engineering report](https://www.anthropic.com/engineering/multi-agent-research-system)
describes an orchestrator-worker system and reports concrete operational
lessons: vague assignments produce duplicate searches and coverage gaps;
effective task descriptions need an objective, output format, source/tool
guidance, and boundaries; agents may continue after sufficient evidence; and
the system requires trace-based evaluation because failures emerge from
interactions.

The 2024 [AutoGen paper](https://www.microsoft.com/en-us/research/publication/autogen-enabling-next-gen-llm-applications-via-multi-agent-conversation-framework/)
shows that conversable agents can combine models, tools, and humans in flexible
patterns. It is infrastructure evidence, not proof that unconstrained
conversation is a dependable coordination protocol.

Design implication:

- the orchestrator owns a versioned DAG, not a free-form group chat;
- each delegated task must declare scope, exclusions, schema, evidence,
  permissions, budget, resources, and stop conditions;
- the orchestrator cannot attest to a specialist's result without validating
  its receipt and artifacts; and
- specialists return bounded structured products, not their entire context.

### 4.1.1 Events and deterministic recovery

[CloudEvents](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)
standardizes event identity and routing context. Its `source` plus `id`
identifies a distinct event and permits duplicate resend with the same values;
`specversion`, `type`, `subject`, `datacontenttype`, and `dataschema` make event
interpretation explicit. The specification does not promise exactly-once
processing, and one occurrence may produce more than one event.

The [Temporal Python SDK documentation](https://github.com/temporalio/sdk-python)
provides a useful implementation lesson even if gludd does not adopt Temporal:
workflow control code must replay deterministically, nondeterministic I/O belongs
in recorded activities, historical event streams can be replayed against a new
workflow version to detect incompatibility, and long-running activities
heartbeat so cancellation can take effect. Temporal's AI FAQ explicitly warns
that calling an LLM inside replayed workflow code would call it again and
produce nondeterminism.

Design implication:

- task events use CloudEvents-compatible identity/context but retain a separate
  business idempotency/effect key;
- the coordinator is deterministic code; model/tool/network calls are recorded
  activities whose results are replayed rather than reissued;
- critical state is rebuilt from durable task/effect/approval/artifact records,
  not assumed to be present in an event stream or chat history;
- historical plans are replayed under proposed planner/state-machine changes
  before rolling deployment; and
- activity heartbeat, cancellation, and lease-expiry semantics are tested
  together.

Lamport's classic
[Time, Clocks, and the Ordering of Events in a Distributed System](https://www.microsoft.com/en-us/research/publication/time-clocks-ordering-events-distributed-system/)
shows that causality provides only a partial order. CloudEvents' wall-clock
`time` is optional and is not an ordering guarantee.

Design implication: task events carry correlation, causation, per-producer
sequence, accepted-state sequence, and optimistic state version. Wall time is
observability data only. Independent concurrent events remain unordered until a
declared join/reducer combines them, and every valid delivery permutation must
produce the same terminal state and artifacts.

### 4.2 Team topology is task-dependent

[MultiAgentBench](https://arxiv.org/abs/2503.01935) evaluates collaboration and
competition across star, chain, tree, and graph topologies using milestone
metrics. It found topology-dependent results rather than one universally
superior structure.

[Magentic-One](https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/)
uses a lead plus four functional specialists. Anthropic uses parallel research
workers under one lead. These are evidence for selectable patterns:

- direct specialist for narrow, low-risk work;
- map/reduce for independent evidence collection;
- sequential pipeline for typed transformations;
- constructor/auditor for falsifiable review;
- planner/executor/verifier for external effects;
- domain expert plus safety steward for hazardous work; and
- human arbitration for unresolved high-consequence conflicts.

Design implication: routing should select the smallest topology justified by the
task. More agents increase cost, latency, attack surface, and correlated
failure; they are not automatically safer or more capable.

### 4.3 Deadlock, starvation, and wait cycles

The classic
[System Deadlocks](https://doi.org/10.1145/356586.356588) analysis identifies
mutual exclusion, hold-and-wait, no preemption, and circular wait as the
conditions that permit deadlock. Gludd can prevent most planned resource
deadlocks by eliminating hold-and-wait and circular wait: reserve a node's
declared resource set atomically in one canonical order before execution.

The
[Chandy-Misra-Haas distributed deadlock work](https://doi.org/10.1145/357360.357365)
shows why detecting true wait cycles becomes harder when no participant has a
complete view and messages are delayed. Gludd's broker should therefore maintain
the durable authoritative wait-for graph for expert work instead of asking
models to infer liveness from conversation. PostgreSQL's
[deadlock guidance](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-DEADLOCKS)
independently reinforces consistent acquisition order and warns against holding
locks while waiting for user input.

Design implication:

- every resource namespace/key has a canonical acquisition rank;
- a node obtains all declared resources atomically or none, and a dynamic
  resource request causes safe release and versioned replanning;
- the broker records owner/request edges, modes, leases, heartbeats, causal
  state, priority, and safe-preemption/compensation status;
- a detected cycle selects a deterministic safe victim, never silently revokes
  an irreversible committed effect, and emits a replayable deadlock receipt;
- human approval waits release accelerators, equipment, and exclusive locks
  unless an explicit costed lease with a short TTL is approved; and
- bounded fairness, aging, and priority-inversion controls prevent a stream of
  higher-priority tasks from starving valid work indefinitely.

### 4.4 Compensation is a new effect, not history erasure

The original [Sagas paper](https://doi.org/10.1145/38713.38742) decomposes a
long-lived transaction into smaller committed transactions with compensating
transactions. A compensation is application-specific forward work that restores
an acceptable state; it is not an ACID rollback and cannot make a physical,
financial, published, or human-observed effect unhappen.

Operational reports reinforce the edge cases. Temporal users reported
[compensations that can themselves fail](https://community.temporal.io/t/exception-on-compensation/2403)
and asked how cancellation scope interacts with retrying compensations. A later
[maintainer explanation](https://community.temporal.io/t/why-do-we-add-compensation-action-before-action-we-want-to-compensate/19226)
shows why compensation intent and an operation ID may need to be durable before
the forward call: a timeout can hide a successful effect.

Design implication:

- the plan declares the compensation contract, preconditions, authority,
  idempotency/effect key, evidence, and residual non-reversible consequences
  before a compensatable forward effect begins;
- uncertain forward outcomes are reconciled before compensation or retry;
- dependent compensations run in reverse dependency order; independent
  compensations run concurrently only when explicitly commutative and disjoint;
- compensation has its own bounded retry, circuit breaker, authorization,
  verification, and terminal failure state;
- cancellation does not erase already-committed cleanup obligations or grant
  cleanup broader authority; and
- evidence retains both the original effect and every compensation attempt,
  including residual state and required human recovery.

### 4.5 Cross-domain synthesis is an interface-control problem

The
[NASA Systems Engineering Handbook](https://ntrs.nasa.gov/citations/20170001761)
treats interface requirements, interface-control decisions, assumptions,
anomalies, integration, and end-to-end verification as first-class engineering
work products. Component correctness is not system correctness when two correct
components disagree on a unit, frame, tolerance, material state, boundary
condition, protocol revision, or operating envelope.

The [NIST Guide to the SI](https://www.nist.gov/pml/special-publication-811)
provides a deterministic foundation for units and dimensions. The
[JCGM Guide to the Expression of Uncertainty in Measurement](https://www.bipm.org/en/committees/jc/jcgm/publications)
requires significant input correlations to be included in combined uncertainty;
assuming independent expert estimates merely because different roles produced
them can materially understate uncertainty.

Design implication:

- cross-domain work produces a signed synthesis artifact, not a prose
  concatenation of specialist answers;
- every conclusion traces through exact component claims, transformations,
  units/dimensions, frames, reference conditions, tolerances, versions, and
  interface checks;
- deterministic schema, dimensional, range, conservation, and constraint checks
  precede model-mediated synthesis;
- uncertainty propagation records distributions/intervals, sensitivity,
  correlation or justified independence, and the selected analytic or
  Monte-Carlo method;
- the system operating/safety envelope is the verified intersection of domain
  constraints; an empty or unknown intersection is a conflict, not an average;
- system-level verification tests couplings, emergent hazards, common-cause
  failures, and end-to-end behavior in addition to component checks; and
- any participating expert can challenge the synthesis with a typed counterclaim
  and falsifier, which reopens the artifact without rewriting history.

## 5. Shared memory and evidence findings

### 5.1 Evidence needs exact targets and historical state

The [W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/)
provides interoperable target selectors for fragments, text quotes/positions,
data ranges, SVG, and media, plus time and request-header states. A citation can
therefore identify the exact portion and representation used instead of merely
linking a mutable page.

[RFC 7089 Memento](https://datatracker.ietf.org/doc/rfc7089/history/) defines
datetime negotiation for prior states of mutable web resources. It is useful
when a source supports historical representations but does not replace a
retrieval-time content digest or license/retention policy.

The EMNLP 2023 [ALCE benchmark](https://aclanthology.org/2023.emnlp-main.398/)
separates answer correctness from citation completeness and citation
correctness. Its reported best ELI5 systems still lacked complete citation
support about half the time, illustrating that a plausible cited answer is not
automatically evidence-complete.

Design implication:

- every material external claim references an immutable evidence record with
  canonical identity, retrieval time, exact content digest, media type, source
  class, license, freshness, and a precise selector;
- mutable sources create new evidence versions; prior digests and claims remain
  resolvable;
- source selection, retrieval, citation completeness, citation entailment, and
  answer correctness are evaluated separately;
- copied or syndicated pages retain a shared upstream/correlation root rather
  than becoming independent evidence; and
- when storage is not licensed, gludd retains permitted metadata, digest,
  locator, and bounded annotation—not prohibited source bytes.

### 5.2 Memory requires tiers and control

[MemGPT](https://arxiv.org/abs/2310.08560) treats long context as a hierarchy of
memory tiers with explicit movement rather than as an infinite prompt.
[Generative Agents](https://research.google/pubs/generative-agents-interactive-simulacra-of-human-behavior/)
combines an observation stream, retrieval, reflection, and planning, with
ablations showing that each component affects behavior.
[Memory Sandbox](https://arxiv.org/abs/2308.01542) demonstrates user-visible
memory objects that can be inspected, changed, summarized, and shared.

The May 2026 [GroupMemBench preprint](https://arxiv.org/abs/2605.14498) reports
weak multi-party memory results, particularly for knowledge updates and
ambiguous terms; a BM25 baseline matched or exceeded many evaluated memory
systems. The June 2026
[Governed Shared Memory preprint](https://arxiv.org/abs/2606.24535) identifies
unauthorized leakage, stale propagation, persistent contradiction, and
provenance collapse, including a reported direct-get scope bypass and a
pipeline-ordering conflict. Both are watchlist evidence that must be reproduced
locally.

Design implication:

- task scratch, session, project, domain-verified, and user-private memory must
  have separate namespaces and retention;
- exact identifiers and lexical retrieval remain first-class; embeddings are
  not the sole index;
- every read path, including direct get-by-ID, enforces the same tenant,
  project, user, and role scope;
- memory writes are proposed, verified, superseded, retracted, or expired—not
  silently overwritten;
- contradictions remain visible until resolved; and
- retrieved content is untrusted data even when it came from another expert.

### 5.3 Reflection is not verification

[Reflexion](https://papers.nips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html)
uses verbal feedback retained across attempts.
[Self-Refine](https://papers.neurips.cc/paper_files/paper/2023/hash/91edff07232fb1b55a505a9e9f6c0ff3-Abstract-Conference.html)
uses one model as generator, feedback provider, and refiner.

These methods can improve task performance, but the same model's critique is
not independent evidence. Gludd should retain concise lessons and failed-test
evidence, not private chain-of-thought or unverified self-judgments.

Cross-expert learning is therefore a governed publication process. An expert may
propose a lesson containing exact applicability conditions, preconditions,
procedure, postconditions, tool/card/schema revisions, evidence, counterexamples,
and observed failures. A curator and applicable domain verifier decide whether
it becomes scoped procedural memory. Receivers retrieve it only when their
current contract and task conditions match. Peer feedback never directly edits
another expert's prompt, weights, tools, policy, permissions, or verified
memory; those changes enter the separate self-improvement path.

## 6. Conflict arbitration findings

The 2024 ICML paper
[Improving Factuality and Reasoning through Multiagent Debate](https://proceedings.mlr.press/v235/du24e.html)
reports gains on evaluated reasoning and factuality tasks. The 2024 ACL paper
[Rethinking the Bounds of LLM Reasoning](https://aclanthology.org/2024.acl-long.331/)
found that a strongly prompted single agent nearly matched the best discussion
method on many tested tasks, with multi-agent gains dependent on prompt setup.

The 2024 EMNLP paper
[Encouraging Divergent Thinking](https://aclanthology.org/2024.emnlp-main.992/)
identifies degeneration of thought after a model commits to a stance.
[Sparse communication topology research](https://aclanthology.org/2024.findings-emnlp.427/)
shows comparable or better debate results with lower connectivity and cost in
the evaluated settings.

Current 2026 work further warns against naive voting:

- [Demystifying Multi-Agent Debate](https://aclanthology.org/2026.findings-acl.1694/)
  reports vanilla debate can underperform majority vote and emphasizes diverse
  initial hypotheses and calibrated confidence.
- [Minority Sentinel](https://arxiv.org/abs/2606.29270) argues that correlated
  model errors can suppress a correct minority. This remains a watchlist
  preprint.
- [Two LLMs debate, both are certain they won](https://arxiv.org/abs/2505.19184)
  reports increasing and mutually incompatible confidence during debate. This
  is also preprint evidence and needs replication.

Design implication:

- majority vote is never sufficient for high-consequence arbitration;
- model family, training lineage, prompt, source overlap, and shared tool output
  determine whether votes are independent;
- claims are compared through normalized identities, conditions, evidence,
  calculations, and policy—not fluency;
- the arbitrator sees structured arguments and source evidence, with producer
  labels blinded when practical;
- dissent and uncertainty survive the verdict; and
- unresolved safety, legal, release, physical, or chemical conflicts go to a
  qualified human.

### 6.1 Joint verification is not self-critique

The ICLR 2024 paper
[Large Language Models Cannot Self-Correct Reasoning Yet](https://proceedings.iclr.cc/paper_files/paper/2024/hash/8b4add8b0aa8749d80a34ca5d941c355-Abstract-Conference.html)
found that intrinsic self-correction without external feedback can reduce
reasoning performance. In contrast,
[CRITIC](https://proceedings.iclr.cc/paper_files/paper/2024/hash/fef126561bbf9d4467dbb8d27334b8fe-Abstract-Conference.html)
and
[key-condition verification](https://aclanthology.org/2024.emnlp-main.714/)
report gains when critique is grounded in tools or explicitly verified
conditions. These results are task-specific, but they distinguish reflection
from verification.

The NeurIPS 2023
[MT-Bench judge study](https://papers.neurips.cc/paper_files/paper/2023/hash/91f18a1287b398d378ef22505bf41832-Abstract-Datasets_and_Benchmarks.html)
documents position, verbosity, self-enhancement, and reasoning biases in
LLM-as-a-judge evaluation.

[NASA software IV&V guidance](https://swehb.nasa.gov/spaces/SWEHBVB/pages/32604595/SWE-141%2B-%2BSoftware%2BIndependent%2BVerification%2Band%2BValidation)
defines independence along technical, managerial, and financial dimensions and
ties required independence to software criticality.

Design implication:

- an author's reflection is useful diagnostic input but cannot satisfy an
  independent verification requirement;
- the verifier first receives requirements, exact inputs, assumptions, and
  expected properties and records a verification plan before seeing the
  candidate when risk warrants;
- deterministic tests, solvers, schemas, signatures, measurements, and
  independently reproduced calculations outrank a judge preference;
- judge evaluations blind producer identity, permute order, normalize irrelevant
  verbosity, and publish measured calibration/bias;
- cross-domain outputs require each domain verifier plus an integration
  verifier for units, interfaces, boundary conditions, and shared assumptions;
  and
- a verification receipt is scoped to exact artifact digests, conditions,
  versions, and validity interval and is invalidated by material change.

### 6.2 Calibration is local evidence, not a model personality

[On Calibration of Modern Neural Networks](https://proceedings.mlr.press/v70/guo17a.html)
defines calibrated confidence as predicted probability matching empirical
correctness frequency and shows that accuracy and calibration are different
properties. [Language Models (Mostly) Know What They Know](https://arxiv.org/abs/2207.05221)
reports promising self-evaluation in evaluated formats but weaker
generalization of calibration to new tasks. The 2024
[Large Language Models Must Be Taught to Know What They Don't Know](https://arxiv.org/abs/2406.08391)
further reports that prompting alone was insufficient for reliable uncertainty
in its settings.

Design implication:

- free-form or token-level model confidence is never routing, arbitration, or
  effect authority;
- calibration binds the exact expert/card/skill, model, prompt, tools, output
  schema, domain/language/risk slice, dataset, and evaluation interval;
- reports separate correctness, calibration, discrimination, and selective
  prediction using a proper score, reliability/error measure, AUROC or
  equivalent discrimination measure, and risk-versus-coverage/abstention curve;
- sample size, uncertainty intervals, subgroup results, and distribution drift
  determine whether a threshold is usable;
- a model/prompt/tool change or out-of-slice request invalidates the threshold;
  and
- calibrated confidence can choose abstention or a reviewer, but cannot override
  deterministic failure, missing evidence, safety policy, or independent
  verification.

## 7. Evaluation findings

Agent teams are stochastic stateful systems. Evaluation must measure final state,
not merely plausible text:

- [AgentBench](https://proceedings.iclr.cc/paper_files/paper/2024/hash/e9df36b21ff4ee211a8b71ee8b7e9f57-Abstract-Conference.html)
  spans eight interactive environments and identifies long-horizon reasoning,
  decision-making, and instruction following as common failures.
- [`tau`-bench](https://arxiv.org/abs/2406.12045) evaluates agent-user-tool
  interaction using the final database state and `pass^k`, exposing
  inconsistency across repeated trials.
- [MultiAgentBench](https://arxiv.org/abs/2503.01935) adds collaboration,
  competition, and milestone measures.
- [AgentDojo](https://proceedings.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html)
  contains tool-use tasks and indirect prompt-injection attacks.
- [PaperBench](https://openai.com/index/paperbench/) decomposes research
  replication into thousands of rubric items and separately evaluates the
  automated judge.
- AutoGenBench, described with Magentic-One, repeats and isolates agent runs to
  control stochasticity and side effects.
- [NIST AI 600-1](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence)
  supplies cross-lifecycle governance for generative-AI risks; published
  2024-07-26 and its page was updated 2026-04-08.

Design implication: gludd's conformance suite needs deterministic protocol
tests, repeated behavioral trials, final-state invariants, security attacks,
judge calibration, cost/resource budgets, and domain-specialist review.

## 8. Security and failure-containment findings

The February 2025
[OWASP Agentic AI threat guide](https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/)
and May 2026
[OWASP memory-poisoning article](https://genai.owasp.org/2026/05/13/memory-is-a-feature-it-is-also-an-attack-surface/)
identify tool misuse, supply-chain risk, goal manipulation, and durable memory
poisoning.

AgentDojo demonstrates why untrusted tool output must not share one instruction
channel with privileged plans. MCP's security guide adds token passthrough,
confused deputy, SSRF, and session hijacking. A2A adds signed discovery,
tenant-aware tasks, capability validation, duplicate delivery, and secured push
notifications.

Design implication:

- policy and capability checks execute outside all models;
- the child receives the intersection of caller authority, role requirements,
  user authorization, and environmental policy;
- untrusted data is labeled and cannot become a system instruction, tool
  schema, capability, or verified memory without review;
- non-idempotent external actions use prepare/authorize/commit and a durable
  idempotency key;
- every expert has token, time, step, concurrency, memory, disk, network, and
  tool-call limits;
- cancellation propagates to children and tool runs;
- a compromised role can be quarantined without taking down unrelated roles;
  and
- retries never repeat an effect whose outcome is unknown.

## 9. Safe discovery and self-improvement findings

[Voyager](https://arxiv.org/abs/2305.16291) combines an automatic curriculum,
executable skill library, environment feedback, and self-verification.
[Darwin Godel Machine](https://arxiv.org/abs/2505.22954) keeps an archive of
candidate agents, generates modified variants, empirically evaluates them, and
reports sandboxing and human oversight. It is a 2025 preprint, not authorization
for production self-modification.

[OpenScholar](https://www.nature.com/articles/s41586-025-10072-4) demonstrates
large-scale retrieval and citation-backed synthesis over scientific literature,
while [PaperQA2](https://arxiv.org/abs/2409.13740) reports strong performance on
literature-search and synthesis tasks. These are evidence that a discovery role
can systematically retrieve beyond model training, not that its synthesis is
automatically authoritative.

[The AI Scientist](https://arxiv.org/abs/2408.06292) demonstrates an automated
idea, code, experiment, manuscript, and simulated-review loop. Because the same
system family generates and automatically reviews candidate work, its outputs
remain proposals requiring independent domain verification. The NEJM AI
[data-to-paper system](https://doi.org/10.1056/AIoa2400555) instead emphasizes
programmatic information-flow backtracing and human-verifiable outputs.

Reflexion and Self-Refine show useful iterative feedback patterns, but the
generator cannot be the only verifier. Open-ended discovery also creates reward
hacking, benchmark overfitting, skill-supply-chain, permission-expansion, and
rollback risks.

The Science 2015
[Reusable Holdout](https://pubmed.ncbi.nlm.nih.gov/26250683/) work formalizes
why repeatedly adapting proposals to results from the same holdout invalidates
ordinary fixed-analysis assumptions. [LiveBench](https://proceedings.iclr.cc/paper_files/paper/2025/file/e4a46394ba5378b3f9a186a5b4c650d1-Paper-Conference.pdf)
reduces LLM test contamination through objective scoring and regularly updated
questions.

[Sycophancy to Subterfuge](https://arxiv.org/abs/2406.10162) reports an
experimental path from easier specification gaming to reward-tampering
behavior, with explicit caveats about its artificial settings. The result is
not proof that a deployed model will tamper, but it is sufficient to require
that the candidate cannot write its evaluator, expected answers, test overlay,
score, or promotion record.

Design implication:

```text
discover
 -> quarantine source
 -> propose typed change
 -> create isolated candidate
 -> add failing regression first
 -> run frozen + hidden + adversarial evaluations
 -> independent review and policy decision
 -> signed candidate bundle
 -> canary with no new authority
 -> promote or automatically roll back
```

The discovery role has read/search and proposal capabilities only. It cannot
promote memories, prompts, models, policies, skills, tools, permissions, or code.

In addition:

- hidden/holdout suites are delivered from a signed read-only source after the
  candidate workspace is sealed;
- candidate authors receive bounded pass/fail/category feedback, not hidden
  cases, and adaptive queries consume a recorded privacy/evaluation budget;
- evaluation harness, fixtures, result storage, and promotion policy are outside
  candidate authority and integrity-checked before and after each run;
- collection, fixture application, and selected-test omissions fail closed;
- training, retrieval, memory, prompt, and prior-evaluation exposure to each
  benchmark is declared as contamination metadata;
- promotion uses non-compensatory safety/correctness gates plus cost/resource
  comparison, not one gameable scalar reward; and
- repeated seeds, environments, counterfactuals, ablations, adjacent tests, and
  real incident regressions are required before attributing improvement.

### 9.1 Source trust, freshness, and hostile internet research

[Greshake et al.](https://arxiv.org/abs/2302.12173) demonstrate that instructions
placed in remotely retrieved data can redirect an LLM-integrated application's
behavior and tool use. The result is directly relevant to literature, code,
image/OCR, audio/transcript, and web research: the attacker does not need access
to the user's prompt when it can influence a future source. AgentDojo provides
an executable benchmark counterpart. [NIST AI 100-2
E2025](https://doi.org/10.6028/NIST.AI.100-2e2025) supplies a lifecycle-oriented
taxonomy for adversarial ML, poisoning, evasion, privacy, and generative-AI
attacks, but its own errata history also illustrates why a system must pin an
exact source revision and monitor corrections.

Source “trust” cannot be one scalar attached to a website. The relevant
questions differ:

- Did retrieval reach the intended bytes without an SSRF, rebinding, redirect,
  parser, or supply-chain violation?
- Is the source authentic, and what exact claim is the author or publisher
  qualified to establish?
- Does the evidence apply to this identity, version, population, operating
  condition, time, method, and risk?
- Is the source current, corrected, retracted, superseded, or merely historical?
- Are purportedly separate sources independent root observations, or mirrors,
  translations, summaries, shared datasets, and repeated model output?

A standards body can be authoritative about a current standard without being an
experimental replication. A primary paper can establish what its experiment
reported without establishing production safety. A maintainer issue can
establish a reproducible software symptom without rewriting the protocol. A
signature can establish provenance without making the signed description true.

Design implication:

- source class, transport/authenticity, authority scope, applicability,
  freshness, independence, factual verification, and handling trust remain
  separate verdicts;
- domain/risk policies declare maximum age and event-driven refresh for
  correction, retraction, supersession, compromise, and license change;
- all redirects and resolved addresses are validated before each request, and
  active content is parsed without execution;
- search snippets and generated summaries are discovery leads, not reviewed
  evidence;
- inaccessible full text, parser failures, truncation, and blocked access remain
  explicit missing evidence;
- retrieved instructions cannot alter goals, policy, tools, network scope,
  memory, trust, or authorization; and
- internet research produces evidence and proposals only.

### 9.2 Generated-source feedback and regression memory

[Shumailov et al.](https://doi.org/10.1038/s41586-024-07566-y) show model
collapse in recursive generational training when generated outputs pollute later
training data, including early loss of distribution tails. That result concerns
training dynamics under the studied conditions; it does not prove that every
use of synthetic data or retrieval causes collapse. It does establish a
necessary provenance and evaluation problem for a self-improving expert: its
own answer may be published, mirrored, summarized, retrieved, distilled, or
placed in a new dataset and then appear to be independent external support.

The defense is lineage and independent reference data, not an unreliable
“AI-text detector.” Generated, translated, summarized, and model-assisted
sources retain derivation and root-origin groups. Unknown origin remains
unknown; absence of a detected edge is not proof of independence. A candidate's
output and its descendants cannot validate that candidate.

Every material failure, abstention, incident, canary abort, or rollback should
create a quarantined regression proposal with:

- minimized reproducible input and invariant;
- exact implementation, model, prompt, tool, data, source, policy, and
  environment revisions;
- affected conditions and slices, including tails and minority groups;
- observed, expected, negative, and inconclusive outcomes;
- fix, canary, rollback, and source-correlation links; and
- privacy, retention, hidden-evaluation, and visibility controls.

Independent reproduction promotes a regression into the required suite.
Candidate-visible memory never exposes hidden expected answers or evaluator
secrets. Renaming or repackaging a failed candidate cannot erase its lineage.

### 9.3 Signed canaries and rollback are part of the candidate

The [Google SRE canary guidance](https://sre.google/workbook/canarying-releases/)
defines a canary as a partial, time-limited deployment evaluated against a
control. It emphasizes representative size/duration, version-attributed
metrics, comparison with control behavior, and metric windows compatible with
the canary duration. [Argo Rollouts
analysis](https://argo-rollouts.readthedocs.io/en/stable/features/analysis/)
models success, failure, and inconclusive analysis and can return traffic to a
stable revision. Its [rollback-window
documentation](https://argo-rollouts.readthedocs.io/en/latest/features/rollback/)
also makes explicit that rapid rollback depends on retained deployment state.

The long-lived [Argo Rollouts issue
#995](https://github.com/argoproj/argo-rollouts/issues/995), opened in 2021,
reported a healthy canary blocked by an unhealthy stable revision and discussion
of a breaking-glass workaround. It motivates tests in which baseline health,
canary health, control-plane health, and rollback availability fail
independently.

For an expert improvement, the signed canary plan binds candidate/baseline
digests, cohort, traffic ceiling, exclusions, paired comparison, metrics,
thresholds, minimum samples/time, stop conditions, telemetry store, approvers,
and the exact pre-exercised rollback artifact before results are visible.
Candidate authority excludes every one of those controls. Shadow candidates
receive equivalent inputs but cannot perform effects. Favorable early results
are inconclusive, not permission to promote.

Rollback is a tested state transition, not “redeploy the old model.” It must:

- work when the candidate, ordinary approval path, or control-plane component
  is unavailable;
- revoke/drain candidate tasks, credentials, caches, indexes, memories, and
  descendants;
- preserve N/N-1 readability through expand/contract data and schema changes;
- meet declared recovery time and recovery point objectives; and
- emit a signed verification receipt for restored routing, authority, state,
  SLOs, and residual effects.

### 9.4 Abstention, escalation, and cross-expert stress benchmarks

Calibration evidence in Section 6.2 is useful only if the runtime has a terminal
way to withhold an unsupported conclusion. `abstained` should therefore be
distinct from execution failure, policy rejection, a resumable request for
input, and required review. It records the unmet requirement, bounded attempts,
safe partial artifacts, prohibited conclusions/effects, and actionable next
choices.

Escalation is not a budget or authority reset. A policy-defined ladder can try a
compatible alternate expert, evidence retrieval, one focused user question, an
independent verifier/safety role, or a qualified human. Canonical-intent and
unmet-requirement identity detect A-to-B-to-C-to-A cycles even when aliases or
wording change.

The executable cross-expert suite must freeze typed inputs, source/correlation
graphs, cards/tools/policies, schedules, injected faults, budgets, seeds, and
deterministic oracles. Required initial disturbances include:

- contradictory primary evidence hidden behind many mirrors;
- stale official material plus a current correction;
- cyclic delegation and repeated escalation;
- `all_required` versus explicit partial joins under branch failure/abstention;
- unit, condition, and correlated-uncertainty errors at domain interfaces;
- individually passing components with an emergent coupled hazard;
- coordinated indirect injection across text, OCR, audio, code, and tools;
- generated-source recirculation;
- crash/receipt/compensation ambiguity; and
- early favorable canary results followed by slice regression and rollback.

The oracle checks terminal class, deterministic invariants, required
evidence/traces, and forbidden effects. Transcript similarity is not a
conformance oracle.

## 10. Practitioner and maintainer failure evidence

These reports are operational evidence. They motivate tests and do not establish
universal framework behavior.

| Report | Opened | Observed failure | Required gludd regression |
|---|---:|---|---|
| [AutoGen issue #165](https://github.com/microsoft/autogen/issues/165) | 2023-10 | Chat history grows, final output is brittle around a `TERMINATE` message, and callers reach into internal message arrays | Bounded memory; typed terminal states and result schema; no sentinel parsing |
| [AutoGen issue #584](https://github.com/microsoft/autogen/issues/584) | 2023-11-07 | Users needed custom speaker selection to express an eight-agent dependency flow | Versioned plan DAG and deterministic eligible-speaker routing |
| [AutoGen discussion #2301](https://github.com/microsoft/autogen/discussions/2301) | 2024-04-05 | Resuming a group chat required reconstructing messages and speaker transitions, with unclear role semantics | Serialize full control state, schema version, plan version, pending effects, and checkpoints |
| [LangGraph discussion #744](https://github.com/langchain-ai/langgraph/discussions/744) | 2024-06-21 | Converging edges unexpectedly executed a node twice | Declared join reducers, idempotency keys, and exactly-once effect tests |
| [LangGraph discussion #1097](https://github.com/langchain-ai/langgraph/discussions/1097) | 2024-07-23 | Tool output was not recognized as terminal and the agent looped | Machine terminal predicate, max-step circuit breaker, repeated-state detector |
| [LangGraph discussion #1877](https://github.com/langchain-ai/langgraph/discussions/1877) | 2024-09-27 | Shared team message history reduced predictability across models and system-message formats | Per-role private context plus bounded typed handoff; never concatenate all team transcripts |
| [LangChain issue #9394](https://github.com/langchain-ai/langchain/issues/9394) | 2023-08-17 | Conversational retrieval, memory, and returned source documents did not compose reliably | Keep source/evidence records separate from conversation memory and require them in the typed result |
| [LangChain issue #18731](https://github.com/langchain-ai/langchain/issues/18731) | 2024-03-07 | Retrieval integration hard-coded one metadata shape despite indexes exposing several fields | Adapter contract preserves arbitrary source metadata and maps it into canonical typed identity without loss |
| [AutoGen issue #5248](https://github.com/microsoft/autogen/issues/5248) | 2025-01-29 | A tutorial termination condition lost to handoff behavior | State-transition precedence tests and no model-controlled terminal authority |
| [MCP issue #711](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/711) | 2025-06-11 | Trust, sensitivity, malicious-activity, and provenance annotations were not composable across tool boundaries | Mandatory gludd trust labels and provenance propagation independent of optional protocol hints |
| [MCP issue #1087](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1087) | 2025-07-29 | Session IDs alone did not define user-level isolation or certification | Bind session to authenticated tenant/user; cross-tenant tests on list/search/direct-get |
| [MCP issue #1442](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1442) | 2025-09-08 | Stateful initialization coupled clients to server instances and complicated load-balanced failover | Self-contained typed handoffs, external durable control state, and failover/resume tests across worker instances |
| [MassTransit discussion #5489](https://github.com/MassTransit/MassTransit/discussions/5489) | 2024-09-12 | Concurrent saga messages with one correlation ID arrived in intermediate/completed states and moved to an error queue | Model causal/state versions explicitly and test duplicate/out-of-order delivery permutations to the same terminal result |
| [LangGraph issue #6792](https://github.com/langchain-ai/langgraph/issues/6792) | 2026-02-12 | Resume inside a subgraph reran prior work because checkpoint scopes differed | Crash/restart test proving completed effects are not re-executed |
| [Archived MCP Puppeteer issue #3662](https://github.com/modelcontextprotocol/servers/issues/3662) | 2026-03-21 | A still-downloaded archived server was reported with SSRF, indirect injection, and sandbox concerns | Registry rejects archived/unmaintained tools unless explicitly risk-accepted and sandboxed |
| [SWE-bench issue #280](https://github.com/SWE-bench/SWE-bench/issues/280) | 2025-01-10 | Patches marked successful by selected tests failed unchanged developer tests, overstating effectiveness | Candidate promotion runs risk-selected adjacent/unchanged tests and proves exact collection/selection |
| [SWE-bench issue #538](https://github.com/SWE-bench/SWE-bench/issues/538) | 2026-03-13 | A candidate-created path prevented the official test patch from applying, yet the harness continued and reported a false positive | Mount signed tests after sealing candidate, deny candidate writes to evaluator paths, and fail on fixture/patch application error |
| [ImageOptim issue #436](https://github.com/ImageOptim/ImageOptim/issues/436) | 2024-02-07 | Users raised privacy and size concerns about C2PA, and the maintainer confirmed ordinary metadata removal already strips it | Never assume embedded-manifest survival; test stripping/recovery, sidecars, privacy controls, and explicit “provenance unavailable” presentation |
| [Temporal Java issue #871](https://github.com/temporalio/sdk-java/issues/871) | 2021-11-15 | Activity cancellation re-entered an event loop and produced a detected workflow deadlock | Detect wait cycles and non-yielding work; cancellation/deadlock tests must cover state-machine re-entry and preserve replay evidence |
| [Temporal compensation forum report](https://community.temporal.io/t/exception-on-compensation/2403) | 2021-06-16 | Users found that compensations can exhaust retries or stop remaining cleanup, leaving workflow failure semantics unclear | Compensation is a durable typed effect with independent retry, continuation policy, receipt, residual-state report, and human-recovery path |
| [Argo Rollouts issue #995](https://github.com/argoproj/argo-rollouts/issues/995) | 2021-02-17 | An unhealthy stable revision blocked progress of a healthy canary; users discussed breaking-glass recovery | Canary/control health must be attributed separately; pre-exercise rollback and recovery when stable/control paths are degraded |

## 11. Repository-specific conclusions

Gludd already has:

- `agents/types.py`, `agents/dispatcher.py`, and `agents/task_decomposer.py`;
- `schemas/task_definition.py`, `schemas/task_return.py`, and
  `schemas/task_decision.py`;
- `scheduling/planner.py`, `scheduling/scheduler.py`, and
  `coordination/file_claims.py`;
- `security/permissions.py`, `security/capability_lattice.py`,
  `security/capability_guard.py`, and STS narrowing;
- episodic, semantic, procedural, cross-task, cross-conversation, local, and
  banked memory implementations;
- lifecycle task evidence and critical-state compaction hooks;
- observability, run-history, budget, pause, review, sandbox, and policy seams;
  and
- `self_improve` gates, approvals, outcomes, and harnesses.

The correct implementation is an interoperability layer over those components.
Creating a second dispatcher, scheduler, permission system, memory database, or
self-update path would be a defect.

## 12. Decisions carried into the feature specification

1. A2A v1.0-compatible cards, tasks, states, and artifacts are the external
   interoperability baseline.
2. MCP remains a tool/context boundary, not an agent authorization shortcut.
3. Every task is a typed state machine with idempotent event processing.
4. Every plan is a versioned DAG with generic resource claims and declared join
   semantics.
5. Every child receives narrowed capabilities and bounded context.
6. Evidence is append-only, content-addressed, provenance-linked, scoped, and
   supersedable.
7. Memory has explicit namespaces, trust, retention, and direct-ID authorization.
8. Retrieved content never becomes privileged instruction merely through
   repetition or expert origin.
9. Conflict arbitration uses evidence hierarchy, independence checks,
   calibrated uncertainty, and retained dissent.
10. Majority vote alone cannot authorize high-consequence work.
11. Final-state invariants and repeated-run reliability outrank transcript
    plausibility.
12. Loops, retries, crash recovery, cancellation, and partial joins have
    executable conformance tests.
13. Self-improvement produces proposals and isolated candidates; only an
    independent governed path can promote them.
14. Promotion cannot expand authority and always has a tested rollback.
15. All changes remain post-beta3 development work.
16. Source authority, handling trust, freshness, applicability, independence,
    and factual verification remain separate claim-specific decisions.
17. Internet research uses a sandboxed hostile-content boundary and can emit
    evidence/proposals only.
18. Generated and derivative sources retain root lineage and cannot recursively
    corroborate their candidate or ancestor.
19. `abstained` is a typed terminal state with bounded, authority-preserving,
    cycle-aware escalation.
20. Cross-expert conformance uses signed frozen benchmark fixtures, explicit
    schedules/faults, deterministic invariants, and forbidden-effect oracles.
21. Canary policy is signed before results are visible, and promotion requires
    representative evidence plus independently exercised rollback.

## 13. Domain appendix integration

The separate
[`EXPERT_EXPANSION_RESEARCH_2026-07-29.md`](EXPERT_EXPANSION_RESEARCH_2026-07-29.md)
contains the deep domain research and implementation backlog requested for:

- Git graph/recovery mastery, release captain behavior, reproducible release
  artifacts, and safe build/helper-script discovery;
- AI/ML literature, evaluation, speech recognition/synthesis, world models,
  image recognition/generation, LoRA/distillation, scientific simulators, and
  accelerator parity;
- materials selection, metallurgy, polymers, hot/cold/pressure joining,
  machining, additive manufacturing, molding/forming, textiles, structural
  modeling, qualification, and manufacturing safety; and
- chemical identity, authoritative data, analytical/computational chemistry,
  molecular simulation, retrosynthesis, scale-up, and chemical safety.

Those are domain profiles for the common runtime rather than independent agent
platforms. Every `EXP-GIT-*`, `EXP-ML-*`, `EXP-MAT-*`, and `EXP-CHEM-*` backlog
item binds to the `ExpertCard`, typed task/result, team/handoff, source/evidence,
memory, safety, abstention, benchmark, promotion, canary, and rollback contracts
in the feature specification. The domain acceptance cases provide specialized
fixtures; the common XEB suite proves their cross-domain composition.

The domain appendix and interoperability feature are explicitly post-beta3.
Research branches must not alter the beta.3 release commit, workflow, artifacts,
or deployment state.
