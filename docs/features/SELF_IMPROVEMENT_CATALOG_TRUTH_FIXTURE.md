# Catalog-Truth Local-Model Acceptance Fixture

Status: tracked, reproducible sentinel for the managed self-improvement path.

## Immutable identity

| Field | Pinned value |
| --- | --- |
| Task | `S83.134` (`catalog-truth`) |
| Fixture | `config/self-improve/catalog-truth.json` |
| Fixture SHA-256 | `67e59f242aba0ade9b5992354daf5f0ec2392df3627ef0c929596011cfe5c30e` |
| Baseline | `eac05dc88c03f14fbd7dd5f4c6d72943609d9e26` |
| Independent Codex reference | `80b381bd87f32487d784964ce93566e3b016b191` |
| Candidate limit | Two attempts, stopped after the first accepted candidate |
| Reference time | 600 seconds |

The fixture is the previously exercised small catalog-correction task. Its JSON
uses sorted keys and compact separators, while the existing
`TaskSpec.from_path` parser remains the single schema and bounded-command
validator. The Make target verifies the exact raw-byte digest before delegating
to the existing `test-self-improve` target. A changed or unreadable fixture,
missing Git object, unresolved full SHA, invalid task, or unsupported live-mode
value fails closed before inference.

The reference changes only
`src/general_ludd/local_model/_local_model_configs.py` and
`tests/unit/test_e2e_model_configs.py`. Its required regression test and
static checks are embedded in the fixture rather than reimplemented by the
wrapper target.

## Input parity

This input parity is deliberate: Codex and managed local models receive the
same exact acceptance facts. The canonical objective names the exact DeepSeek and
StarCoder2 repository/filename identities, excludes both stale identities,
preserves the native SmolLM2 context of 8192, and bounds the change to the two
reference paths and one regression-test file. The previous shorthand to "fix
mappings" left a local candidate to infer facts that the independent reference
already received, so the comparison did not isolate model behavior.

The fixture exposes requirements, not answers: neither a Codex patch/diff nor
raw model output enters the candidate prompt. Exact compact JSON bytes and
the pinned digest make any future change to this shared input an explicit,
reviewable fixture revision.

## Reproducible execution

The safe behavioral example performs input admission and prints the resolved
plan without loading or downloading a model:

```console
make test-self-improve-catalog-truth SELF_IMPROVE_CATALOG_LIVE=0
```

Live local inference requires an explicit opt-in:

```console
make test-self-improve-catalog-truth SELF_IMPROVE_CATALOG_LIVE=1
```

The wrapper always supplies an empty model-path override, so Gludd selects,
acquires, leases, and releases its own eligible local model. It pins both Git
SHAs, the fixture path, and `SELF_IMPROVE_MAX_ATTEMPTS=2`; callers cannot turn
the sentinel into a different benchmark through inherited variables. The
target never tags, releases, deploys, promotes, merges, or changes a daemon.

## ZDD and resource lifecycle

This is a zero-downtime acceptance path. Each candidate is prepared in an
isolated worktree while the running application remains untouched. Admission
happens before model work, candidates run serially, and a failed candidate
cannot replace an accepted or deployed revision.

The bounded lifecycle remains the one defined by the main acceptance contract:

- one owned worker and one retained model instance per candidate;
- no separately launched llama.cpp server or auxiliary cleanup process;
- at most two candidate attempts for this fixture;
- a 300-second candidate deadline and observable acquisition heartbeats;
- an 8 GiB managed model-cache quota with a 2 GiB filesystem reserve; and
- exact leases, process groups, exchange paths, and rejected worktrees released
  by their application owner on every exit path.

A validated cached model may remain after a run; that persistent artifact is
managed cache state, not a leaked process. A cleanup failure blocks acceptance.

## Evidence basis

The evaluation rationale and links are centralized in the
[acceptance matrix official and practitioner evidence](../SELF_IMPROVEMENT_ACCEPTANCE_MATRIX.md#evaluation-practice-and-practitioner-evidence).
That section records the official lm-evaluation-harness and llama.cpp guidance,
plus the long-lived practitioner reports that motivated complete fixture,
prompt, runtime, hardware, and protocol identity. This fixture cites that
source instead of copying research that could drift independently.

## Validation and rollback

The focused contract covers strict parsing, exact fixture bytes, safe Make
delegation, the target registry, help text, and this lifecycle document.
Repository validation additionally runs the Make target contract checker,
duplicate-target checker, Python and Markdown lint, and collection check before
commit.

The rollback is an ordinary revert of the single fixture commit. Because the
wrapper has no deployment or daemon side effects, rollback does not interrupt
service or rewrite historical model outcomes. Restoring the previous fixture
commit restores its prior digest stratum; cached artifacts remain subject to
the normal lease-aware quota owner.
