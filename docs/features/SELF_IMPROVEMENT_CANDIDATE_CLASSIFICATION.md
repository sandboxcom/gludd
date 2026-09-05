# Managed Self-Improvement Candidate Classification

## Status and scope

This tranche supplies a deterministic classification boundary for managed
self-improvement model selection. It does not select, call, or promote a model.
The managed candidate assembler can consume the artifact in a later tranche.

The implementation composes two existing, local Gludd primitives:

- `infer_task_type()` assigns one canonical `TaskType` with the repository's
  deterministic hash embedder.
- `map_task_to_capabilities()` maps task language to bounded task-kind and
  `TaskRole` pairs.

Classification performs no network, credential, filesystem, or provider access.

## Contract

`classify_candidate_task()` accepts one non-empty UTF-8 task description and
returns a frozen `CandidateTaskClassification`. The artifact contains:

- SHA-256 of the exact task text;
- canonical task type;
- primary task kind and role;
- all matched, bounded capability categories;
- classification and precedence protocol versions; and
- a stable digest over the complete canonical artifact.

It deliberately contains no task text, prompt, completion, endpoint, API key,
credential, model response, or repository content. The optional expected input
digest protects an approved caller from classifying substituted content.
`verify_candidate_task_classification()` reclassifies exact input and rejects
both input substitution and categorical drift.

### Versioned precedence

Mapper result order is not policy. Protocol
`gludd-capability-precedence-v1` applies this explicit order:

| Priority | Task kind | Role |
| ---: | --- | --- |
| 1 | `context_compaction` | `compactor` |
| 2 | `documentation_draft` | `editor` |
| 3 | `bounded_enumeration` | `enumerator` |
| 4 | `failure_classification` | `reviewer` |
| 5 | `format_normalization` | `editor` |
| 6 | `schema_extraction` | `editor` |
| 7 | `coding` | `coder` |

The first matched category is primary. A policy reorder requires a new version;
silently changing v1 would make old routing evidence impossible to reproduce.

### Fail-closed behavior

Classification rejects:

- non-string, blank, NUL-bearing, or over-256,000-byte task input;
- vocabulary-disjoint input rejected by canonical task-type inference;
- input with no mapped capability;
- unknown, duplicate, malformed, or role-substituted capabilities;
- mutable or incorrectly ordered capability collections;
- unsupported protocol versions; and
- malformed, uppercase, stale, or substituted digests.

No fallback category or default model is manufactured after rejection.

## Observability and privacy

`event_payload()` exposes one bounded event:
`self_improve_candidate_classified`. It includes task and classification digests,
protocol versions, and categorical type/kind/role. It excludes the task text and
all provider data. The caller remains responsible for emitting the event through
Gludd's configured trace sink and correlating later candidate, trial, and
promotion events with `classification_digest`.

A task digest is pseudonymous correlation data, not encryption. Low-entropy task
text may be guessable, so operators should retain and authorize this event like
other routing metadata. Raw task content must never be added to the event to make
diagnosis easier.

## ZDD and rollback

This module is additive and has no database or configuration migration. During a
zero-downtime deployment, old workers can continue without classification while
new workers create v1 artifacts. A future managed-runner integration must:

1. deploy readers that understand v1 before producing persisted v1 references;
2. keep version dispatch explicit if v2 is introduced;
3. route only newly admitted work through the new classifier; and
4. roll back by disabling the integration, without rewriting prior evidence.

Unknown versions fail closed, so a partially rolled-back fleet cannot silently
reinterpret a newer classification.

## Test evidence

The unit suite covers composition with both existing classifiers, deterministic
multi-match precedence, complete-artifact digest binding, exact-input replay,
frozen artifacts, content-free events, invalid input, mismatched digests, unknown
categories, role substitution, duplicates, mutable collections, and version
rejection. Tests are hermetic and run identically locally and in GitHub Actions.

## Practitioner and authoritative findings

A long-running routing reliability concern is correct identity attribution. In
[LiteLLM issue #1518](https://github.com/BerriAI/litellm/issues/1518), opened in
January 2024, a practitioner reported that fallback success accounting continued
to identify `model0` even when another model actually served the request. That
made rate evidence for fallback models incorrect. Gludd's design response is to
bind every classification to immutable input and artifact digests and require
downstream events to carry that identity instead of inferring it from mutable
router state.

The upstream
[vLLM Semantic Router overview](https://github.com/vllm-project/semantic-router)
describes routing across heterogeneous model infrastructure by evaluating request
signals and application policy. Its
[official troubleshooting guide](https://github.com/vllm-project/semantic-router/blob/main/website/docs/troubleshooting/common-errors.md)
also warns operators to verify classifier artifact/label mappings and to measure
precision and recall rather than casually lowering thresholds. Gludd therefore
versions its category precedence and rejects unknown labels; calibration remains
a downstream evidence task, not a hidden classifier fallback.

SHA-256 follows the
[NIST Secure Hash Standard, FIPS 180-4](https://csrc.nist.gov/pubs/fips/180-4/upd1/final),
and the structural immutability boundary uses Python's documented
[`frozen` data-class behavior](https://docs.python.org/3/library/dataclasses.html#frozen-instances).
