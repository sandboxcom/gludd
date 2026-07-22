# Workflow Failure Class Tests

This matrix defines workflow-level failure classes as testable invariants. The goal is to catch categories of bad state transitions, not single incidents.

| Class | Invariant | Executable coverage |
|---|---|---|
| Dirty local state | Push, release, and CI dispatch paths fail closed when the worktree has uncommitted or untracked changes. | `tests/unit/test_check_clean_tree_guard.py`, `tests/unit/test_ci_remote_head_guard.py`, `tests/unit/test_workflow_failure_class_guards.py` |
| Remote CI stale head | `ci-trigger` only dispatches the current branch after local HEAD exactly matches `sandboxcom/<branch>`. | `scripts/ci_remote_head_guard.py`, `tests/unit/test_ci_remote_head_guard.py` |
| CI push thrash | Batch push cannot use `COMMIT_THRESHOLD=1`; push helpers require clean-tree plus CI busy/rate guards. | `tests/unit/test_workflow_failure_class_guards.py`, `tests/unit/test_batch_push_enforce.py` |
| CI run cancellation | Push runs are keyed by commit SHA; only pull request runs may cancel previous runs. | `.github/workflows/build.yml`, `tests/unit/test_workflow_failure_class_guards.py` |
| Missing pipeline after push | The build workflow must trigger on `development` and `master` push, and manual dispatch remains available for exact-head retries. | `.github/workflows/build.yml`, `tests/unit/test_workflow_failure_class_guards.py` |
| Incomplete release | Release paths do not declare completion until release artifacts pass completeness verification. | `scripts/verify_release_completeness.py`, `tests/unit/test_workflow_failure_class_guards.py` |
| Unexpected SIGTERM | Generic stale-task watchdogs must not kill CI shard supervisors or their pytest children; unrelated stale pytest remains killable. | `tests/unit/test_agent_watchdog.py`, `tests/unit/test_task_watchdog.py`, `tests/unit/test_no_unexpected_sigterm.py` |
| Disk pressure | Disk gates fail when `/tmp/gludd-*` usage or root disk use crosses configured thresholds. | `scripts/check_disk_usage.py`, `tests/unit/test_check_disk_usage.py` |
| Load pressure | Local test worker count is capped by memory, CPU, and 5-minute load headroom; CI bypasses local load throttling but keeps RAM and CPU caps. | `scripts/adaptive_test.py`, `tests/unit/test_adaptive_test.py` |
| Stale target docs | Public Makefile targets must appear in `make help`, preventing hidden or undocumented workflow paths. | `make check-make-help`, `tests/unit/test_makefile_syntax.py` |
| Prompt-safe file edits | The reusable make edit and copy targets must be documented, require a file path, and reject external paths. | `tests/unit/test_make_edit_targets_guardrails.py` |
| Stale deadline reuse | Task watchdog stale deadline IDs are single-use and expired IDs are pruned before any process scan, so old state cannot kill unrelated future work. | `scripts/task_watchdog.py`, `tests/unit/test_task_watchdog.py` |
| Dash-prefixed pytest arguments | Shard make targets pass pytest arguments with `--pytest-args=...` so values beginning with `--` are parsed as values, not launcher options. | `tests/unit/test_workflow_runtime_guardrails.py` |
| Live-provider secret gating | DeepSeek live game builds use repository secrets, skip when the key is absent, and remain non-blocking in CI. | `.github/workflows/build.yml`, `tests/e2e/test_game_building_deepseek.py`, `tests/unit/test_workflow_runtime_guardrails.py` |
| Long-job observability | Long-running task and shard paths must have timeout output, heartbeat output, and a status target. | `scripts/run_ci_shard_summary.py`, `scripts/task_runner.py`, `tests/unit/test_workflow_runtime_guardrails.py` |

When a new workflow bug is observed, add or extend a row here and add a focused failing test before changing behavior.

## External Notes

- GitHub Community discussions show the concurrency cancellation behavior has been a long-lived source of confusion for Actions users: [#5435](https://github.com/orgs/community/discussions/5435), [#12835](https://github.com/orgs/community/discussions/12835), [#41518](https://github.com/orgs/community/discussions/41518). The current workflow tests pin push runs to commit SHA and cancel only PR runs.
- GitHub Actions documentation and the 2026-05-07 changelog document the newer `queue: max` behavior for concurrency groups. This repo still tests the stricter invariant that release-relevant push runs are not canceled by later pushes.
- Python argparse has long-standing reports where option values beginning with `-` are parsed as options unless passed with the equals form. The shard Makefile tests pin `--pytest-args=...` for that reason: [Stack Overflow](https://stackoverflow.com/questions/52636309/python-argparse-leading-dash-in-argument), [Python issue 9334](https://bugs.python.org/issue9334), [Python issue 26196](https://bugs.python.org/issue26196).
