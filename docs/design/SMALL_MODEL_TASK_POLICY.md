# Small and Local Model Task Capability Policy

Status: implemented policy library and local contract tests (2026-08-02)

## Decision

Gludd does not authorize a model because it is called `small`, `local`, `weak`,
cheap, or fast. Those are deployment properties, not capability evidence. A
constrained model may receive work only when all of these are true:

1. the task kind is present in the bounded-task registry;
2. the routing role, collection, and declared impacts match that task contract;
3. a deterministic offline suite passed every case for the exact acceptance
   contract;
4. the proof is bound to the exact weights, serving configuration, and prompt
   contract digests currently loaded;
5. the task has not already been claimed; and
6. completion evidence passes every declared check within the retry bound.

`general_ludd.routing_roles.SmallModelTaskPolicy` implements this decision. A
caller that proposes a constrained or locally served model must use
`authorize()` before dispatch and `record_completion()` before accepting its
result. `ESCALATE` is a routing decision, not an error to suppress.

## Threat model

The boundary assumes that a model can:

- emit plausible but incomplete prose;
- produce well-formed output on one prompt and malformed output on another;
- lose tool or schema behavior after a weight, quantization, template, runtime,
  or prompt change;
- claim that tests passed without producing collection/test evidence;
- repeat a task after a timeout and create conflicting artifacts; and
- consume an unbounded retry budget when a task is outside its capability.

The model is not trusted to classify its own capability, impacts, completion,
or need for escalation.

## Default task and routing-role matrix

All default tasks are non-authoritative. They may read source material and write
an isolated artifact; they may not apply, commit, execute, deploy, publish, or
make a security decision.

| Task kind | Routing role | Required acceptance checks |
|---|---|---|
| `bounded_enumeration` | `enumerator` | bounded coverage, no duplicates, valid schema |
| `context_compaction` | `compactor` | facts preserved, token budget met, valid schema |
| `documentation_draft` | `editor` | facts traceable, links valid, valid schema |
| `failure_classification` | `reviewer` | evidence cited, label in taxonomy, valid schema |
| `format_normalization` | `editor` | idempotent, semantic equivalence, valid schema |
| `schema_extraction` | `editor` | all required fields, source traceable, valid schema |

`coder` and `planner` are intentionally absent. A future safe task kind may be
added through an injected `TaskContract`, but a contract cannot override the
global high-impact exclusions. Its exact local suite must land before the task
contract is promoted.

## Permanently excluded impacts

The policy rejects these impacts even if supplied evidence says the model
passed:

- repository mutation;
- command or tool execution;
- network writes;
- credential access;
- deployment or resource lifecycle changes;
- release/publish operations; and
- authoritative security decisions.

A small model may draft an artifact consumed by a stronger model or a
deterministic checker. It may not cross the side-effect boundary itself.

## Model identity and proof binding

`ModelIdentity` is the digest tuple for:

- the concrete model artifact/weights;
- the full serving runtime and decoding configuration; and
- the exact prompt/tool/schema contract.

The profile ID is descriptive. The identity fingerprint is authoritative. If a
mutable profile alias points to new weights, quantization, chat template,
runtime flags, or prompt instructions, its previous proof no longer matches and
dispatch fails closed.

`CapabilityEvidence` additionally binds:

- task kind;
- routing role;
- Ansible collection;
- acceptance-contract digest;
- suite ID and revision;
- passed and total case counts;
- successful test collection; and
- an evidence artifact digest.

The default minimum is 20/20 local cases. Partial success, a collection error,
an online/API-dependent evaluation, or evidence for a neighboring role or
collection is not promotable.

## Local evaluation contract

Do not build a second general-purpose benchmark runner. Use the mature
[EleutherAI Language Model Evaluation Harness](https://github.com/EleutherAI/lm-evaluation-harness)
for model/corpus execution when generation is required, and use pytest for
Gludd's deterministic policy/adapter assertions. The harness's
[task guide](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/task_guide.md)
supports versioned YAML task definitions and local data files; retain the task
YAML digest and harness revision in the evidence artifact.

Every promotion suite must:

1. run without cloud credentials or a paid/model API;
2. pin model, runtime, decoding, prompt, corpus, and checker revisions;
3. collect successfully before any result is considered;
4. contain at least 20 representative and adversarial cases;
5. score 100% on the exact acceptance checks;
6. include malformed, missing-field, duplicate, oversized, and ambiguous-input
   cases where applicable;
7. run twice with identical inputs and compare normalized outcomes; and
8. write a content-addressed evidence artifact.

General benchmark scores are useful discovery signals but do not grant a Gludd
role. Promotion requires the exact role/collection contract suite.

## Dispatch, deduplication, retry, and completion

The task fingerprint is a canonical SHA-256 over task ID, input digest, impacts,
and acceptance-contract digest. The request-scope registry admits only one claim
for a task ID. Durable schedulers must persist that fingerprint beside their
lease so worker restarts preserve the same invariant.

Completion requires:

- the authorized task fingerprint;
- the next sequential attempt number;
- an artifact digest;
- exactly the declared acceptance-result keys, all `true`;
- successful test collection; and
- a completion-evidence digest.

The default retry bound is two and the configurable hard range is one to three.
Replaying the same completion evidence is idempotent and does not spend another
attempt. Conflicting evidence after acceptance escalates. Exhausting the retry
budget escalates to a stronger model; it never silently accepts partial work.

## Collection and role guidance

Ansible role names and routing roles are different namespaces. Role authors must
declare the routing role and task kind explicitly and must not infer either from
the selected model profile.

Candidate collection surfaces, only in artifact-only/read-only mode:

| Ansible surface | Eligible bounded task |
|---|---|
| `document_change` | `documentation_draft` when repository writes are disabled |
| `debug_failure` | `failure_classification` for an evidence-backed handoff artifact |
| report/audit composers | bounded enumeration, extraction, or normalization only |
| context compaction helper | `context_compaction` |

Ineligible surfaces include `agent_task`, `implement_change`, `refactor_code`,
`write_tests`, `validate_and_push`, deployment roles, release roles, and any role
that executes commands, uses credentials, or mutates infrastructure. These may
consume a constrained model's accepted draft, but a stronger authorized actor
must perform and verify the side effect.

Every constrained-model prompt envelope must include the task ID, task kind,
routing role, collection, allowed impacts, exact acceptance checks, remaining
attempt count, and the instruction to escalate rather than invent evidence.

## ZDD rollout and rollback

This policy is additive and content-addressed:

1. evaluate a candidate offline while the existing route serves work;
2. register proof without changing active traffic;
3. shadow-authorize representative tasks and compare decisions;
4. canary only bounded artifact work;
5. promote by publishing the proof digest; and
6. roll back immediately by removing the proof digest or changing the model
   identity, with no process restart or queue drain.

In-flight tasks retain their task fingerprint and retry bound. New dispatches
observe the new proof set, which avoids mixed acceptance contracts during a
rollout.

## Long-lived upstream user reports

These reports are why serving support or a model label is not treated as proof:

- [Ollama #6704](https://github.com/ollama/ollama/issues/6704), opened in
  September 2024, records users working through model response shape, templates,
  and instructions before tool calls are recognized.
- [vLLM #13683](https://github.com/vllm-project/vllm/issues/13683), opened in
  February 2025 with follow-up reports months later, shows guided JSON decoding
  producing incomplete output across configurations.
- [Ollama #12064](https://github.com/ollama/ollama/issues/12064), opened in
  August 2025, reports frequent malformed tool-call JSON during write-file use.
- [llama.cpp #22072](https://github.com/ggml-org/llama.cpp/issues/22072), opened
  in April 2026 and reproduced on another backend in May, shows malformed or
  truncated arguments even for small schemas and a tool-capable model.

The common operational lesson is that weights, templates, parser/runtime, and
the exact task all affect capability. Local contract probes must cover the full
combination.

## Implemented verification

`tests/unit/test_small_model_task_policy.py` verifies the default matrix,
identity binding, exact proof matching, collection success, the 20-case floor,
100% pass requirement, all high-impact exclusions, unknown-task rejection,
task deduplication, exact completion checks, bounded retries, idempotent replay,
conflicting completion rejection, digest validation, and public package exports.
