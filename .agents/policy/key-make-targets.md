## Key Make Targets

Run `make help` for the current categorized target list. This is the short
operating path; [the executable contract](../../config/make_target_contract.json)
owns target variables and safe examples. Use only Make commands, including
reads, edits, research helpers, tests and Git operations. Add and test a missing
target before using it. Follow the release rules in the root AGENTS.md.

### Establish current state before changing it

Read TASKS.md, BUGS.md, config/ratchet.yml and SESSION.md together using
`make cat-file FILE=path`. Use `make worktree-state` for the current branch,
SHA and staged/unstaged paths; `make all-worktree-state` includes other checkouts.
Use `make active-work-status`, `make ps` and `make disk` to identify existing
owners, subprocesses, worker capacity and disk use. `make ps-gludd` covers
namespaced daemons only. Model-agent status is separate from process PIDs.

Preserve existing staged work and immutable failure evidence. A live gate owns
its frozen candidate: do not edit that checkout, change its index or move its
HEAD during proof. Use an isolated development-based worktree for disjoint
repairs; record branch, base SHA, task and file ownership before editing.
Unknown ownership needs investigation through existing owners, not another gate.

### Choose the next action from evidence

| Current input | Next action |
|---|---|
| Capacity, runtime, configuration or ownership prerequisite fails | Repair the named input and run its focused acceptance. |
| Focused tests pass | Run each complete changed test owner, lint and strict typing; collect before committing. |
| A Make recipe or variable changes | Run `make check-make-target-contract` and the contract's behavioral example. |
| Source is reviewed and prerequisites pass | Freeze one candidate for combined acceptance, full gate and exact-SHA hosted CI. |
| Local or hosted proof fails | Preserve its receipt and log; change the actual failing input before retrying. |
| Gate and required exact-SHA proofs pass | Record task evidence and use the guarded promotion owner from the canonical main checkout. |

Source review, focused acceptance, collection, plan mode and scheduling admission
have distinct meanings. A plan reports `gate_evidence=false`; it never supplies
a full gate, CI result, deployment proof or permission to close a task.

### Run focused checks with explicit inputs

This is a safe contract example, not the complete acceptance scope for a change:

```bash
make test-files TESTFILES=tests/unit/test_make_target_contract.py PYTEST_ARGS='-q -W error -n 0' UV_PROJECT_ENVIRONMENT=.venv
```

Select the installed environment belonging to the active checkout or its
registered shared toolchain. An isolated worktree must not point at the canonical
main checkout's .venv: its subprocess-confinement guard rejects that reference.
Keep warnings fatal and preserve ordinary plugins and original assertions.
`make lint-files FILES='path1.py path2.py'` and
`make typecheck-scope FILES='path1.py path2.py'` provide focused checks.
`make lint`, `make collect-check` and `make test-count` remain required
by the commit/completion policy. A focused pass is not a green full gate.

### Admit and prove a release candidate

First inspect the existing admission plan:

```bash
make release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=1 WORK_ITEM_ID='' OBSERVED_ROOT=.gate-logs/observed OBSERVED_RETAIN_RUNS=20
```

When the identified prerequisites have changed or are clear, use the real owner
for the active release task. Do not start a second proof while another owner
holds the candidate or resources:

```bash
make release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=0 WORK_ITEM_ID=S98.4 OBSERVED_ROOT=.gate-logs/observed OBSERVED_RETAIN_RUNS=20
make gate GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1
```

Use the installed canonical release runtime and the complete gate. Preserve
85% aggregate and 75% per-file line and branch coverage, fatal warnings, ZDD,
cleanup and exact-SHA promotion requirements. The native gate owns .gate-status
and its authenticated evidence. Resume only through the existing authenticated
resume owner after it verifies current source, index and policy; do not relabel
historical green output as current proof.

`make gate-tail GATE_TAIL_LINES=80` shows a bounded log snapshot.
`make gate-progress GATE_PROGRESS_MAX_HISTORICAL=10` explains phase progress.
Use existing pipeline-status/ci-await owners for generated current CI evidence;
select their full variables from the contract. Do not manually edit observation
JSON or repeatedly poll status in place of repairing a known failure.

### Commit, integrate and promote through their owners

`make agent-worktree-dev BRANCH=feature-name` creates an isolated branch from
development. For a bounded source repair, use
`make ship-commit-files FILES='explicit paths' MSG='one tested change' PUSH=0`;
its native content, index-scope and fresh collection guards still apply.
`make git-commit MSG='one tested change'` additionally requires a fresh green
gate. Keep each small tested change in its own commit.
Before a push, `make verify-state` must identify the intended current branch.

From a clean development checkout, plan integration of the reviewed source:

```bash
make development-merge-forward SOURCE=HEAD MODE=content APPLY=0
```

Replace SOURCE with the reviewed branch or exact ref before applying. Inspect
overlapping files: content mode retains development's version on overlapping
content and collection alone cannot prove semantic integration. Ancestry-only
mode is exceptional and needs a documented decision that development already
contains or intentionally supersedes the source deliverable. Preserve failed
proofs; source integration does not close release tasks.

Master receives development through the release owner, after local and hosted
exact-SHA proof, task evidence and the reviewed-head integration receipt.
The following validates policy/topology only and does not publish:

```bash
make release-promote TAG=v0.1.2 MSG=release-notes RELEASE_PROMOTE_VALIDATE_ONLY=1 RELEASE_PROMOTE_PUBLISH=0 REVIEWED_HEAD_INTEGRATION_RECEIPT=
```

Real promotion runs only from the canonical main checkout with the genuine
receipt and validated candidate. Follow
[Release Proof Throughput](../../docs/features/RELEASE_PROOF_THROUGHPUT.md) for
missing-proof records and
[the release runbook](../../docs/RELEASE_RUNBOOK.md) for artifact/publication proof.

### Other supported work

Use `make search PATTERN=regex SEARCH_PATH=directory-or-file` for bounded
repository searches and `make show-lines FILE=path START=1 END=80` for slices.
Use `make write-text-b64 FILE=path TEXT_B64=encoded` or
`make replace-text-b64 FILE=path OLD_B64=encoded NEW_B64=encoded` for literal edits.
Do not place shell interpolation in Make arguments.

Setup through `make init`, dependency synchronization, packaging, Ansible,
Terraform, secrets,
resource cleanup and recovery each have dedicated owners in `make help`.
Read their contract before execution. Cleanup must retain live owners and failed
proof evidence. A target name, dry run or old threshold in prose is not evidence
that its current policy admits an action.
