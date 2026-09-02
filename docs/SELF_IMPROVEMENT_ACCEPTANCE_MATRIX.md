# Local-Model Self-Improvement Acceptance Matrix

Status: reproducible acceptance contract. The document baseline is
`a7b8dce9dc68e047177b6f540991eed6ae6ba9ad`.

## Purpose

This matrix determines whether Gludd's managed local-model self-improvement
path can produce a promotable repository change for representative existing
task shapes. It is an acceptance test for the complete application lifecycle,
not a general model leaderboard and not evidence that one model can perform
unseen work.

Every decision is an objective predicate over immutable inputs and captured
evidence. There is no human quality rating, weighted model score, or preference
between models. A row passes only when every required predicate passes. Numeric
measurements remain visible for regression analysis but never compensate for a
failed correctness or lifecycle gate.

The normative implementation contracts are:

- [Self-Improvement Codex Parity](features/SELF_IMPROVEMENT_CODEX_PARITY.md),
  including the proposal protocol, Codex comparison, resource bounds, and
  cleanup;
- [Self-Improvement Model Acquisition](features/SELF_IMPROVEMENT_MODEL_ACQUISITION.md),
  including authentication, immutable acquisition, cache-hit, and worker
  ownership behavior;
- `TaskType` in `src/general_ludd/schemas/benchmark.py`;
- `DEFAULT_TASK_CONTRACTS` in
  `src/general_ludd/routing_roles/small_model_policy.py`; and
- the deterministic task-shape selection in
  `src/general_ludd/self_improve/task_diversity.py`.

## Fixed identity and replay policy

An acceptance result is comparable only when all identity fields below match.
Any mismatch starts a new result stratum; it must not overwrite, merge with, or
exclude evidence from the earlier identity.

### Fixture identity

Each tracked fixture must contain one canonical JSON manifest serialized with
sorted keys, ASCII escaping, and compact separators. Its SHA-256 is the
`fixture_digest`. The manifest contains:

- a stable fixture ID and exact task text;
- the expected existing `TaskType`, `task_kind`, and role;
- the required checks copied from the selected `DEFAULT_TASK_CONTRACTS` entry;
- the 40-character baseline and independent Codex reference commit SHAs;
- every allowed changed path, required test path, and ordered Make command;
- the SHA-256 and byte count of each baseline input file;
- the reference changed-file set, test-file set, changed-line count, elapsed
  seconds, and patch ID; and
- the expected task-specific assertions and any performance counter.

Before inference, `infer_task_type(task_text)` and
`map_task_to_capabilities(task_text)[0]` must equal the manifest values. An
unknown type, unknown contract, empty capability mapping, changed fixture
digest, missing reference, or mutable Git ref makes the row `invalid_input`.
It is not charged as a model failure.

### Model, runtime, and protocol identity

The result envelope records the model profile ID, exact repository, immutable
40-character revision, GGUF filename, GGUF SHA-256, quantization, and the
ownership-manifest digest. It also records:

- Gludd commit SHA and dirty-tree count;
- Python, `llama-cpp-python`, and vendored llama.cpp identities;
- OS, architecture, backend, accelerator model, offload layers, context size,
  thread/batch settings, and available memory at admission;
- prompt-plan digest and the complete managed attempt-protocol digest;
- compact schema and decoder versions, canary version, token limits, and finish
  policy; and
- deterministic generation values: temperature `0.0`, seed `0`, 32 canary
  tokens, and 1,536 proposal tokens per shard.

The complete attempt-protocol digest, not the prompt digest alone, is the
selection and outcome key. A schema, system prompt, chat template, canary,
sampling value, token bound, operation inference, path rule, or decoder change
therefore invalidates reuse of an earlier failure without deleting its audit
record.

A fixed seed is necessary but does not promise byte-identical output across
CPU, Metal, CUDA, Vulkan, different offload settings, or runtime revisions.
Those are separate runtime strata. Within one exact stratum, replay compares
the binary acceptance tuple, finish reason, token counts, and proposal digest.
A different valid proposal may still pass; prose similarity is never judged.

## Representative task-shape matrix

The base matrix has exactly one row for every existing `TaskType`, matching the
ten-case bound used by `select_representative_evidence`. A fixture may use only
an existing default task contract. Fixture authors choose task text that maps
to the declared pair and pin that mapping in the fixture digest.

| Case | Existing task shape | Fixture boundary | Contract checks |
| --- | --- | --- | --- |
| `AM-BUG-01` | `bug_fix` x `coding` / coder | One Python defect, one source path, one regression-test path | `syntax_valid`, `import_ok`, `run_without_crash` |
| `AM-FEATURE-01` | `feature` x `coding` / coder | One bounded behavior, at most two source paths and one test path | `syntax_valid`, `import_ok`, `run_without_crash` |
| `AM-REFACTOR-01` | `refactor` x `format_normalization` / editor | One behavior-preserving normalization and one invariant test | `idempotent`, `schema_valid`, `semantic_equivalence` |
| `AM-TEST-01` | `test_write` x `bounded_enumeration` / enumerator | Enumerate a finite branch table and add only the missing parameter cases | `coverage_bounded`, `no_duplicates`, `schema_valid` |
| `AM-REVIEW-01` | `code_review` x `failure_classification` / reviewer | Classify one fixture diff and encode the proven defect in a regression test | `evidence_cited`, `label_in_taxonomy`, `schema_valid` |
| `AM-DOC-01` | `documentation` x `documentation_draft` / editor | One feature document, exact source citations, no runtime file | `facts_traceable`, `links_valid`, `schema_valid` |
| `AM-DEBUG-01` | `debugging` x `context_compaction` / compactor | One captured failure trace, bounded diagnosis artifact, and minimal fix | `facts_preserved`, `token_budget_met`, `schema_valid` |
| `AM-OPT-01` | `optimization` x `coding` / coder | One deterministic counter or timing fixture; behavior must remain equal | `syntax_valid`, `import_ok`, `run_without_crash` |
| `AM-SEC-01` | `security_fix` x `coding` / coder | One synthetic exploit regression with no credential, network, or security-decision authority | `syntax_valid`, `import_ok`, `run_without_crash` |
| `AM-INTEGRATION-01` | `integration` x `schema_extraction` / editor | Four to six paths across two prompt shards and one end-to-end contract test | `all_required_fields`, `schema_valid`, `source_traceable` |

`game_logic` remains an existing contract but is not selected by the current
`map_task_to_capabilities` mapper. The base matrix must not fabricate evidence
for an unreachable pair. It can replace a row only after the existing mapper
and policy expose that contract through the same versioned route.

Nine cases use at most three total focus paths and therefore one shard. The
integration case deliberately uses two disjoint shards to exercise retained
worker/model reuse and strict merge behavior. Every case remains within the
global proposal limits; the matrix does not multiply task size, model size,
backend, and cache state into an unbounded Cartesian product.

## Execution plan and bounds

Run cases in case-ID order. Select at most two candidates per case and stop the
case after the first full pass. Thus the base matrix admits at most ten cases
and twenty candidate attempts. Run live inference serially; unit-level fault
injection may run separately without loading a model.

`AM-BUG-01` is the lifecycle sentinel:

1. Its first eligible attempt is a cold-cache trial.
2. Its exact replay is a warm-cache trial using the admitted artifact.
3. The replay must retain the same fixture, model, runtime, hardware, prompt,
   and full attempt-protocol identities.
4. All later cases use normal managed-cache policy; cache state is recorded
   but not manipulated to manufacture a hit or miss.

Cold and warm latency are separate strata. Cold transfer time must never be
compared to a warm cache-hit time as model inference performance.

Current hard ceilings are:

- 32 prompt shards, 262,144 total request bytes, 16,384 bytes per retry shard,
  and no more than three focus paths per base shard;
- 16 compact edits per shard, 32 merged edits, 64 tests, 32 Make commands, and
  1 MiB of merged edit text;
- one owned worker, one lazily constructed `Llama` instance, sequential shards,
  no llama.cpp server, and no auxiliary cleanup process;
- one 32-token same-instance canary and 1,536 proposal tokens per managed
  shard;
- a 300-second deadline for the complete candidate inference attempt;
- a 600-second deadline for each cold Hub operation, with a visible heartbeat
  at least every 15 seconds;
- an 8 GiB managed cache quota and 2 GiB filesystem free-space reserve; and
- one isolated candidate worktree and one atomic commit per accepted attempt.

Resource admission runs before every case. Insufficient memory, context, disk
reserve, quota, or immutable artifact identity is a fail-closed lifecycle
result, never permission to widen limits or start an external server.

## Objective evidence and pass predicates

One canonical result envelope contains the identities above and the following
measurements. Durations use one monotonic clock and are non-negative seconds;
bytes and token counts are non-negative integers.

| Category | Required measurements | Pass predicate |
| --- | --- | --- |
| Proposal success | canary finish, proposal finish, output tokens, shard count, schema/manifest validation | Canary and every proposal finish with `stop`; all strict decoders pass; no partial shard is published |
| Functional quality | named task tests, full required tests, warnings, Ruff, mypy, docstrings, Markdown | Every required check passes and warnings equal zero |
| Coverage | aggregate branch coverage, minimum individual-file coverage | Aggregate is at least 85%; every measured repository file is at least 75% |
| Codex scope | changed-file intersection, candidate/reference file counts, required reference tests | Changed-file precision and recall both equal 1.0; every reference test file is proposed |
| Diff economy | candidate and reference changed lines | Candidate lines are no more than 1.5 times the non-zero reference count |
| Patch identity | candidate and reference stable patch IDs | Record exact equality; require it only when the fixture declares `exact_patch_required=true` |
| Latency | resolution, transfer, readiness, canary, proposal, apply, validation, cleanup, total | No phase exceeds its hard deadline; candidate evaluation is no more than 2.0 times the non-zero Codex reference elapsed time |
| Download | auth mode, resolved revision, bytes, digest, worker start/end | Immutable identity and digest validate; each started worker has one terminal event and is joined |
| Warm cache | Hub/auth calls, acquisition children, manifest/digest validation | Zero Hub calls, zero auth selection, zero acquisition child, and one validated cache-hit lease |
| Cache pressure | accounted bytes, actual free bytes, quota, reserve, eviction identities | Quota and reserve hold; only exact owned, unleased identities can be reclaimed |
| Cleanup | child/process-group count, leases, exchange dirs, candidate worktrees, worktree status | Zero owned children, leases, exchange dirs, and rejected worktrees remain; accepted worktree is clean after its single commit |
| Git | baseline, candidate commit count, dirty count, patch ID | Exact baseline, exactly one candidate commit, zero dirty paths, all Git operations through Make |

A persistent, admitted model artifact is cache state, not a leaked resource.
Cleanup must release its lease and worker but must not delete it merely to make
the post-run filesystem look empty.

### Codex reference without subjective scoring

The Codex reference is produced independently from the same fixture and exact
baseline before the local proposal is exposed. The matrix consumes only
reference facts: file/test sets, changed lines, elapsed seconds, test and gate
results, and stable patch ID.

Case acceptance is the conjunction of the pass predicates above and an empty
`compare_with_codex` blocker set. The existing numeric `score` may be retained
for backward-compatible storage, but this matrix neither displays nor ranks by
it. A model cannot offset a warning, missing file, failed test, coverage miss,
leak, extra commit, bloated diff, or excessive elapsed time with a stronger
result elsewhere.

If the local patch is not patch-equivalent but every declared binary predicate
passes, the result is reported exactly as configured by the fixture. There is
no ad hoc semantic-review vote. A fixture requiring exact behavior must encode
that behavior in tests or set `exact_patch_required` before either agent runs.

## Failure attribution

Use the first terminal phase below as the single primary failure class. Preserve
all later observable facts as secondary fields, but do not blame a model for a
phase it never reached.

1. `input_admission`: fixture, mapping, Git SHA, reference, or protocol identity
   is invalid.
2. `artifact_acquisition`: authentication policy, immutable resolution,
   transfer, digest, quota, or reserve fails.
3. `runtime_admission`: hardware fit, context fit, or worker start fails.
4. `structured_canary`: chat or grammar capability does not return the exact
   bounded canary.
5. `proposal_generation`: timeout, native exit, non-`stop` finish, invalid
   compact JSON, or strict expansion fails.
6. `transactional_apply`: exact old text, path, or isolated write fails.
7. `candidate_validation`: tests, warnings, static checks, coverage, or
   task-specific assertion fails.
8. `codex_parity`: exact scope, reference-test, diff, elapsed, or required patch
   predicate fails.
9. `lifecycle_cleanup`: a lease, worker, process group, exchange directory, or
   rejected worktree remains.
10. `evidence_commit`: the canonical result cannot be durably written and
    re-read with the same digest.

`input_admission` and missing external prerequisites invalidate the case. Phases
3 through 8 are model/runtime evidence for the exact identity. Phase 9 is an
application lifecycle defect and must not be hidden by relabeling it as poor
model output.

## Result states and aggregation

Allowed case states are `invalid_input`, `ineligible`, `failed`, and `passed`.
A matrix run is `passed` only when all ten valid cases pass and the lifecycle
sentinel passes cold and warm predicates. Report raw counts and case IDs only;
do not average task types or rank candidates.

Model evidence remains scoped to the exact tuple:

`(model identity, runtime identity, TaskType, task_kind, fixture digest, full
attempt-protocol digest)`.

Historical failure is eligible for planner exclusion only on that exact tuple.
A newer protocol, changed fixture, backend, quantization, or revision must not
inherit it. A success on one row must not authorize a different task shape.

## Historical evidence and verification status

The historical measurements below are mechanically present in
[Self-Improvement Codex Parity](features/SELF_IMPROVEMENT_CODEX_PARITY.md). They
explain the matrix design but are not current matrix passes because their
prompt or output protocols differ from this document's baseline.

| Recorded historical observation | Evidence status | Matrix treatment |
| --- | --- | --- |
| Qwen2.5-Coder 0.5B and DeepSeek-Coder 1.3B consumed 4,096 tokens in 176.62 and 154.80 seconds without a complete proposal | Repository-documented | Historical `proposal_generation` evidence; obsolete after compact protocol and token-bound changes |
| Qwen2.5-Coder 1.5B returned `stop` at 1,142 tokens in 47.31 seconds and SmolLM2 1.7B returned `stop` at 135 tokens in 14.62 seconds, but each supplied a contradictory operation label | Repository-documented | Historical strict-decoder evidence; obsolete after operation became parent-inferred |
| StarCoder2 3B, Qwen2.5-Coder 3B, and CodeLlama 7B each timed out on a measured 52,017-byte eight-file prompt | Repository-documented | Historical input-boundary evidence; obsolete after deterministic prompt sharding |
| Qwen2.5 0.5B twice exhausted the older 4,096-token small-fixture budget at about 126-128 seconds; the mechanical route produced the exact one-file, two-line reference patch | Repository-documented | Retain as routing evidence for that old fixture and protocol only |

Terminal scrollback, chat recollection, mutable cache contents, and a model name
without immutable revision/runtime/protocol evidence are `unverified`. They may
motivate a new run but cannot populate a result row, exclude a candidate, or
support a capability claim.

## Zero-downtime operation and rollback

The matrix runs only in exact-SHA isolated worktrees. It does not stop or
reconfigure the Gludd daemon, mutate development or master, deploy, tag, or
promote. Model acquisition admits a new immutable artifact before selection;
a current leased artifact remains usable while another is evaluated.

On any failure:

- stop at the first failed Make command;
- terminate and join only the owned process group;
- release the exact lease and remove the exchange directory;
- discard the rejected candidate worktree through the repository Make seam;
- retain validated cache artifacts and immutable evidence records; and
- leave the previously selected model and deployed application untouched.

Protocol rollback restores the prior code and naturally selects only evidence
with the prior full attempt identity. Fixture rollback restores the prior
tracked manifest SHA. Neither action rewrites outcome history or requires a
database migration. Cache rollback does not delete a valid artifact; quota
reclamation remains the sole lifecycle owner for safe eviction.

Before and after the matrix, capture project process ownership, disk free bytes,
cache bytes, active leases, worktree state, and the exact Git SHA. A post-run
cleanup failure blocks the matrix even when every candidate test passed.

## Evaluation practice and practitioner evidence

Official evaluation practice:

- The [lm-evaluation-harness configuration guide](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/config_files.md)
  exposes model/task settings, exact sample selection, generation arguments,
  cache settings, output paths, and separate random seeds, and recommends
  versioning configs with results. Gludd likewise digests the complete fixture
  and protocol rather than recording only a model name.
- The [lm-evaluation-harness model guide](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/model_guide.md#chat-templating)
  records the selected chat template for reproducibility and separates caches
  by tokenizer/template identity. Gludd includes its system/schema protocol in
  the attempt identity for the same reason.
- The [llama.cpp server benchmark guide](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/bench/README.md#metrics)
  records prompt/completion tokens, throughput, and `stop` versus `length`
  completion rates. Gludd records those raw measures but runs its owned Python
  worker rather than introducing a server.

Long-lived practitioner reports are design inputs, not proof that Gludd has the
same defect:

- [lm-evaluation-harness issue 475](https://github.com/EleutherAI/lm-evaluation-harness/issues/475)
  (opened May 2023) traces materially different benchmark results to prompt and
  likelihood semantics. This supports exact fixture and protocol identity.
- [lm-evaluation-harness issue 1098](https://github.com/EleutherAI/lm-evaluation-harness/issues/1098)
  (opened December 2023) records the long design discussion around chat-template
  placement, tokenization, system prompts, and reproducibility. This supports
  storing the complete rendered protocol rather than a boolean template flag.
- [llama.cpp discussion 4020](https://github.com/ggml-org/llama.cpp/discussions/4020)
  (opened November 2023) reports that the same seed can produce different output
  when GPU offload or hardware changes. This is why hardware/backend identity
  defines a result stratum and byte equality is not a cross-platform gate.
- [llama.cpp issue 7381](https://github.com/ggml-org/llama.cpp/issues/7381)
  (opened May 2024) reported a server path ignoring request seed values. Gludd
  records the effective seed inside its full attempt protocol and avoids using
  an unowned external server for acceptance.

## Completion checklist

A matrix claim is valid only when its evidence bundle contains:

- all ten canonical case envelopes plus the cold/warm lifecycle sentinel;
- exact fixture, baseline, reference, model, runtime, hardware, and protocol
  identities;
- every binary pass predicate and raw non-negative measurement;
- no warnings, incomplete output, missing coverage, or cleanup exception;
- one atomic commit and clean worktree for every accepted candidate;
- the repository-wide 85% aggregate and 75% per-file coverage evidence; and
- a final digest re-read proving the result bundle was durably written.

Anything less is an incomplete run, not a partial capability claim.
