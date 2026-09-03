# Multi-File Local-Model Acceptance Fixture

Status: tracked, deterministic sentinel for the managed self-improvement path.

## Immutable identity

| Field | Pinned value |
| --- | --- |
| Task | `S83.133` (`multifile-context-lifecycle`) |
| Fixture | `config/self-improve/context-budget-lifecycle.json` |
| Fixture SHA-256 | `33e86fabb8514219b463ee3e95e45656dcf8b069b33f180f09ea566dbff52f35` |
| Baseline | `80b381bd87f32487d784964ce93566e3b016b191` |
| Independent Codex reference | `6463324cfcf6db9b9a2f9ec203e0bd3862a1e80e` |
| Candidate limit | Two attempts, stopped after the first accepted candidate |
| Reference time | 600 seconds |

The reference is an eight-file, 183-insertion, 30-deletion change whose direct
parent is the pinned baseline. It exercises a genuinely multi-file change across
the runner, comparison contract, model planner, and five focused test files. The
task requires native-context admission, a five-minute worker deadline, explicit
lease-release and persistent outcome evidence, and a bounded terminal diagnostic.
Its six canonical Make commands preserve the same exact protocol, test, static,
and branch-coverage obligations given to the independent Codex reference. The
coverage command reuses the deterministic repository-binding selector, including
all self-improvement contracts and the adjacent project, job, router, EventLoop,
worker, promotion, and daemon seams. It measures all 20 managed-lifecycle sources
in `config/coverage_self_improve.ini`.

The compact, sorted-key, newline-terminated JSON and its digest are immutable
inputs. The wrapper checks the raw bytes before the shared strict parser or any
inference can run.
Missing Git objects, changed fixture bytes, invalid live mode, or an invalid task
fail closed.

## Reproducible execution

The safe behavioral example resolves and prints the complete plan without loading
or downloading a model:

```console
make test-self-improve-multifile SELF_IMPROVE_MULTIFILE_LIVE=0
```

Live local inference is an explicit opt-in:

```console
make test-self-improve-multifile SELF_IMPROVE_MULTIFILE_LIVE=1
```

The target pins both full Git identities, the task file, and two attempts. It
passes an empty model-path override so Gludd selects, acquires, leases, and
releases the best eligible local model itself. There is no external server:
Gludd owns the Python llama.cpp worker process and one retained model instance
across prompt shards. It tears down the process group and transient
exchange paths on every exit path.

## Zero-downtime and resource bounds

The run is zero-downtime: every candidate is built and verified in an
isolated worktree while the active application revision remains untouched. Failed
candidates cannot replace deployed state, and candidate attempts are serial so
model memory is bounded.

The managed cache stays within its 8 GiB quota and 2 GiB filesystem reserve.
Exact leases prevent eviction during inference. A valid cached GGUF may persist
for reuse, but an owned worker, lease, reservation, proposal exchange file, or
rejected worktree surviving completion is a failed cleanup result.

Acceptance requires all canonical tests and static checks, at least 85% aggregate
branch-aware coverage, and at least 75% line-and-branch coverage in each measured
file. Warnings and incomplete model output fail the candidate.

## Official and long-lived practitioner evidence

The
[lm-evaluation-harness configuration guide](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/config_files.md)
supports versioning exact task, generation, cache, output, and seed settings with
results. Its
[model guide](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/model_guide.md#chat-templating)
also separates cache identity by tokenizer and chat template. Those official
practices support pinning the complete fixture and protocol instead of only the
model name. The
[llama.cpp benchmark guide](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/bench/README.md#metrics)
supports recording prompt/completion tokens and stop versus length outcomes.

Two long-lived user reports motivate the stricter identity without being treated
as proof of a Gludd defect:

- [lm-evaluation-harness issue 1098](https://github.com/EleutherAI/lm-evaluation-harness/issues/1098),
  opened in December 2023, records practitioner discussion about chat-template
  placement, system prompts, tokenization, and reproducibility. It supports
  binding the fully rendered protocol to each attempt.
- [llama.cpp discussion 4020](https://github.com/ggml-org/llama.cpp/discussions/4020),
  opened in November 2023, reports same-seed output variation across GPU-offload
  and hardware settings. It supports treating backend and hardware identity as a
  result stratum rather than demanding cross-platform byte equality.

The broader rationale remains centralized in the
[acceptance matrix evidence](../SELF_IMPROVEMENT_ACCEPTANCE_MATRIX.md#evaluation-practice-and-practitioner-evidence).

## Rollback

The rollback is a normal revert of this fixture commit. The target does not tag,
release, merge, promote, deploy, or restart a daemon, so reverting cannot
interrupt service. Restoring the previous commit restores the previous fixture
digest; valid cached models remain governed by the normal lease-aware cache owner.
