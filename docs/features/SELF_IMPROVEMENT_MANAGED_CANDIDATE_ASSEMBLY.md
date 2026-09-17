# Managed Self-Improvement Candidate Assembly

## Status and scope

This feature has two bounded layers. The deterministic
`assemble_managed_candidates()` admission boundary consumes the existing
`CandidateTaskClassification`, `LocalGGUFCandidateIdentity`,
`AzureFoundryCandidateIdentity`, and `ModelCandidateProvider` contracts without
discovering, loading, calling, ranking, acquiring, releasing, or promoting a
model. The additive `LiveManagedCandidateWiring` layer now connects that pure
boundary to the managed runner's existing local lease and optional maintained
Azure discovery adapter.

Live wiring remains default-off. Standard local and GitHub Actions runs retain
the existing local-only path and require no Azure subscription, provider SDK,
secret, or environment credential. Supplying a `LiveCandidateWiringPolicy` at
runner construction enables shadow assembly; it does not enable Azure proposal
generation or change which backend produces the proposal.

## Admission contract

`assemble_managed_candidates()` requires:

- one frozen classification and its separately approved digest;
- an immutable tuple containing between one and 16 `ManagedCandidateSource`
  values;
- an immutable, unique set of required providers;
- an explicit Azure opt-in boolean; and
- for every source, the typed identity, its approved identity digest, approved
  and current non-secret configuration digests, and explicit health, budget,
  and privacy states.

The configuration digest is an approval correlation value. Integrators must
derive it from canonical, non-secret selection configuration and must never put
an API key, bearer token, prompt, source content, or other secret into that
preimage. Credential material belongs to the provider effect boundary, not to
candidate assembly. The current digest must be produced by the same versioned
canonicalization as the approved digest.

## Live discovery-to-runner wiring

Protocol `gludd-live-candidate-wiring-v1` performs one bounded vertical slice:

1. the runner rechecks its approval-bound project privacy policy before model
   acquisition and again immediately before live discovery;
2. the existing `ModelLeaseManager` acquires the selected local artifact, and
   `LocalProposalBackendAdapter` exposes its immutable candidate identity;
3. a `BoundedCandidateSession` rechecks local identity and input, output, call,
   cost, total-token, timeout, and provider policy before a local-only assembly
   preflight;
4. non-secret configuration digests are re-derived and compared before any
   Azure effect;
5. only an explicitly enabled `AzureOpenAIConfig` invokes the existing
   `build_azure_openai_candidate_backend()` discovery adapter;
6. a second bounded session authorizes the exact discovered Azure deployment;
   both identities and configurations are rechecked after discovery, then the
   pure assembler admits the final local or mixed set; and
7. the legacy local session alone generates the proposal. The Azure session is
   never invoked or selected as a replacement provider in this slice.

The input-token reservation uses the managed runner's existing bounded prompt
byte estimate, and output tokens come from the signed approved plan. Azure cost
uses the explicit integer estimate in the live policy. A configured Azure
discovery, identity, budget, configuration, privacy, or event-publication
failure is terminal: the runner does not catch it and retry locally. Omitting
the live policy preserves the previous direct local call exactly.

Construction is lazy. `build_managed_self_improve_runner()` stores only policy,
factory, and content-free sink references; it performs no local acquisition,
credential read, or Azure request. Supplying an Azure factory without a live
policy is rejected instead of silently enabling a provider.

Only these positive states are eligible:

| Dimension | Required state | Rejected examples |
| --- | --- | --- |
| Health | `ready` | `unhealthy`, `unknown` |
| Budget | `within_limits` | `exhausted`, `unknown` |
| Privacy | `approved_public` | `blocked`, `unknown` |

Health and budget are caller attestations from the existing provider/session
boundaries; privacy is an attestation from the existing runtime policy guard.
Assembly does not make a network call to manufacture fresh evidence. A later
effectful integration must recheck all three at its own check-to-use boundary.

## Determinism and fail-closed behavior

Protocol `gludd-managed-candidate-assembly-v1` applies a fixed order: local GGUF
before Azure Foundry, then candidate identity digest. Caller input order and
required-provider order therefore cannot affect the assembly, its SHA-256
digest, ordinals, or events.

Assembly rejects the complete set without returning a partial result when it
finds:

- an empty set or more than 16 candidates;
- mutable containers, untyped values, or malformed digests;
- classification, candidate identity, or configuration drift;
- duplicate immutable candidate identities;
- unhealthy, exhausted, private, or unknown eligibility state;
- an Azure candidate without explicit opt-in; or
- any required provider absent from the admitted set.

Failures use fixed `CandidateAssemblyFailure` categories and do not include a
model name, endpoint, filename, task, configuration, provider exception, or
credential. There is no default candidate, implicit provider substitution,
fallback, partial admission, or attempt to repair drift.

## Resource ownership and cleanup

The assembler owns no resource and `assembler_owns_resource` is always false.
Each output makes the outer lifecycle obligation explicit:

| Candidate | Resource owner | Recorded cleanup action |
| --- | --- | --- |
| Local GGUF | Caller | `release_local_lease` |
| Azure Foundry | External provider | `none` |

The caller must keep the local artifact lease alive from assembly through the
last authorized use and release it on success, failure, or cancellation. The
assembler neither opens nor closes the local runtime. An Azure deployment is an
external resource: assembly must not deprovision it or claim ownership. The live
wiring scope does own the Azure SDK backend object it discovers and closes that
client exactly once on successful exit and on every failure path, independently
of the identity-only assembly artifact. The outer lease manager continues to
release the local artifact.

This distinction prevents rollback or error handling from deleting a shared
deployment and prevents local model leases from becoming invisible obligations.

## Security and privacy

`ManagedCandidateSource` hides its identity from its representation. The
assembled candidate replaces raw local paths and Azure routing fields with the
existing identity digest. Assembly payloads and events contain only:

- protocol, task, classification, identity, assembly, and configuration
  digests;
- bounded ordinals and counts;
- provider and eligibility enums; and
- resource ownership and cleanup enums.

They contain no task text, repository content, prompt, response, local path,
endpoint, deployment name, model filename, ETag, API key, bearer token, or
provider error text. Digest comparisons use constant-time comparison where an
approved value is checked against current state. Digests are pseudonymous
correlation values rather than encryption, so event storage still requires
normal authorization and retention controls.

The source identity type check is exact. Arbitrary lookalike objects and
subclasses cannot run provider-controlled properties while admission inspects
them. The boundary performs no live network or filesystem operation.

## Observability and replay

`event_payloads()` returns one ordered
`self_improve_managed_candidate_admitted` record per candidate followed by one
`self_improve_managed_candidates_assembled` record. Admission records bind the
assembly and classification digests, ordinal, candidate/configuration digests,
provider, accepted state, and cleanup obligation. The completion record binds
the complete provider set, required providers, task digest, count, and assembly
digest.

The event sequence is deterministic and sufficient to compare a replay with the
canonical assembly without retaining content. Returned dictionaries are fresh
defensive values; changing one cannot mutate the frozen artifact. The caller is
responsible for sending them to the configured trace sink. Refusal happens
before an assembly exists, so callers should emit only the fixed failure enum and
must not attach the rejected source object or an exception chain.

## Zero-downtime deployment and rollback

This integration is additive and changes no database, queue schema, durable
configuration, or provider deployment. Old workers and callers that omit the
new optional factory argument continue on the current local-only path while new
workers can exercise v1 assembly in shadow mode. A safe zero-downtime sequence
is:

1. deploy v1 readers and hermetic replay tests without invoking the assembler;
2. assemble shadow sets and compare digest-only events while retaining the
   current local selection path;
3. recheck privacy, health, identity, configuration, and session budget at the
   effect boundary;
4. canary explicit mixed-provider plans with Azure opt-in and existing cost
   ceilings; and
5. route production work only after shadow and canary evidence is green.

Rollback omits the live policy at runner construction or deploys the previous
worker version. In-flight wired attempts finish within their existing session
timeouts and close their SDK client; new attempts immediately use the unchanged
local path. No assembly-owned process, lease, deployment, table, migration, or
durable mutable state needs undoing. The outer owner still executes any
already-recorded local lease cleanup. Unknown future protocols fail closed
rather than being interpreted as v1, so mixed-version workers cannot silently
reorder a newer artifact.

## Hermetic test strategy

The original 33-case assembler suite and focused live-wiring suite construct
only typed values and injected fake provider boundaries. Together they cover
local-only and mixed assemblies, input-order independence, identity and
configuration drift, duplicate candidates, every ineligible state, required
providers, Azure opt-in, hard budgets, default-off construction, terminal
discovery failures, resource cleanup, immutable output, defensive payloads,
content censorship, replay events, and both cleanup contracts. The same command
and fixtures run locally and in GitHub Actions without conditional skips or live
provider access.

Focused Ruff, strict mypy, Markdown lint, collection, and branch-aware coverage
checks are part of this tranche's acceptance evidence. The focused report
records 100% line and branch coverage for the implementation, above the
repository's 75% per-file and 85% aggregate thresholds.

## Long-lived practitioner finding

Research checked on 2026-09-05. In
[llama.cpp issue #12986](https://github.com/ggml-org/llama.cpp/issues/12986),
opened in April 2025, a practitioner reported that GPU backend registry
allocations could remain after the documented initialize, load, free, and
backend-free sequence. The report pointed to the upstream warning that backend
resources could not safely unload while threads might still access them. The
issue was eventually closed as stale without an associated fix.

That report is operational evidence, not a normative guarantee about every
backend. Its design implication here is narrow: candidate assembly must never
pretend that observing an identity transfers resource ownership. Local lease
release stays an explicit caller obligation, the pure assembler acquires
nothing, and health cannot be inferred merely from the existence of a model
identity. This makes cleanup reviewable even when an underlying runtime's
teardown behavior changes.

In
[Azure SDK for Python issue #31032](https://github.com/Azure/azure-sdk-for-python/issues/31032),
opened in June 2023, a practitioner reported that an exception from one
`DefaultAzureCredential` link prevented a later credential in the chain from
being tried. The exact upstream behavior can vary by SDK version and host, but
the long-lived operational lesson is stable: credential discovery order and
environment state can make an apparently available provider fail unexpectedly.

The live boundary therefore treats explicit Azure discovery as one exact,
observable attempt. It propagates the adapter's typed, censored failure, closes
any resource already obtained, and never turns an Azure authentication or
discovery failure into an unrecorded local fallback. This keeps routing intent
reviewable when a provider SDK's internal credential chain evolves.

Three additional practitioner reports make exact deployment identity and drift
checks concrete:

- [Codex issue #2025](https://github.com/openai/codex/issues/2025) records
  persistent Azure 404s when the deployment path segment, wire API, or API
  version did not match the configured resource.
- [Codex issue #6670](https://github.com/openai/codex/issues/6670) records
  `DeploymentNotFound` after configured model/deployment identity drift.
- [openai-python issue #3271](https://github.com/openai/openai-python/issues/3271)
  reports that Azure Responses exposed the served snapshot through
  `x-ms-served-model` rather than reliably through `Response.model`.

These reports are environment-specific observations, not universal provider
guarantees. They justify binding the approved endpoint, deployment, API family,
API version, management-plane model version, and ETag into the two existing
digests; re-deriving configuration immediately before discovery; and refusing
to infer identity from a response field or to mask drift with another provider.
