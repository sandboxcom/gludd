# Managed Self-Improvement Candidate Assembly

## Status and scope

This additive tranche implements a deterministic admission boundary for bounded
local GGUF, Azure Foundry, or mixed candidate sets. It consumes the existing
`CandidateTaskClassification`, `LocalGGUFCandidateIdentity`,
`AzureFoundryCandidateIdentity`, and `ModelCandidateProvider` contracts. It does
not discover, load, call, rank, acquire, release, or promote a model, and it is
not yet wired into the managed runner.

The boundary is deliberately pure. Standard local and GitHub Actions runs use
only in-process values and require no network, Azure subscription, model file,
GPU, provider SDK, secret, or environment credential.

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
external resource: assembly must not deprovision it, create an SDK client, or
claim ownership. A later inference adapter remains responsible for closing any
client resources it creates, independently of this identity-only artifact.

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

This module is additive and changes no database, queue schema, configuration,
public package export, provider client, or runner path. Old workers continue on
the current local-only path while new code can exercise v1 assembly in shadow
mode. A safe zero-downtime integration sequence is:

1. deploy v1 readers and hermetic replay tests without invoking the assembler;
2. assemble shadow sets and compare digest-only events while retaining the
   current local selection path;
3. recheck privacy, health, identity, configuration, and session budget at the
   effect boundary;
4. canary explicit mixed-provider plans with Azure opt-in and existing cost
   ceilings; and
5. route production work only after shadow and canary evidence is green.

Rollback disables the future call site. No assembly-owned process, lease,
deployment, table, migration, or durable mutable state needs undoing. The outer
owner still executes any already-recorded local lease cleanup. Unknown future
protocols fail closed rather than being interpreted as v1, so mixed-version
workers cannot silently reorder a newer artifact.

## Hermetic test strategy

The 33-case focused warning-strict unit suite constructs only typed value
objects. It covers local-only and mixed assemblies, input-order independence,
identity and configuration drift, duplicate candidates, every ineligible state,
required providers, Azure opt-in, hard bounds, immutable output, defensive
payloads, content censorship, replay events, and both cleanup contracts. The
same command and fixtures run locally and in GitHub Actions without conditional
skips or live provider access.

Focused Ruff, strict mypy, Markdown lint, collection, and branch-aware coverage
checks are part of this tranche's acceptance evidence. The focused report
records 100% line and branch coverage for the implementation, above the
repository's 75% per-file and 85% aggregate thresholds.

## Long-lived practitioner finding

Research checked on 2026-09-04. In
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
