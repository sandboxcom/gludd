# Gate and OpenCode Runtime Contract

## Problem

A gate status file is not evidence of completion until a terminal marker has
been written. Likewise, source-pattern checks are not runtime evidence that an
OpenCode hook loads and executes. Parallel live OpenCode tests can also collide
on TUI, daemon, port, and state resources when xdist schedules them to different
workers.

## Behavioral contract

- `gate` and `gate-refresh` acquire the checkout-local gate-run lock before any
  phase. Every terminal path releases it, and a failed test phase continues far
  enough to write `=== GATE: FAILED ===` rather than leaving an ambiguous file.
- The OpenCode E2E has its own visible phase marker and durable log/status row.
- `check-gate-fresh` requires both the semantic completion check and the signed
  status attestation. A partial, stale, red, or modified result fails closed.
- Direct TypeScript children of `.opencode/plugins/` are syntax/default-export
  checked but do not require a config entry because OpenCode auto-loads that
  directory. Configured `.opencode/plugin/` entries remain bidirectionally
  checked, and non-TypeScript companions are ignored.
- Real OpenCode subprocess/TUI tests share the `opencode-live` xdist group. The
  runtime harness imports every enforcement plugin, invokes a supported hook,
  and exercises the watchdog event lifecycle.
- Hook scratch files, hot-module artifacts, and legacy checkout-local dirty-test
  files are removed at both harness startup and teardown. Python dynamic modules
  are registered before execution so Python 3.14 dataclass resolution works.
- The workflow YAML hook parses and structurally verifies the canonical release
  graph. It is registered with pre-commit and has a tracked, validate-only Make
  installation target.
- `coverage-files` runs the selected tests sequentially, captures Python child
  processes through coverage.py, and fails unless aggregate branch coverage is
  at least 85% and every configured file meets the 75% line and branch floor.

## Practitioner and upstream evidence

The long-running
[pytest-xdist grouping issue #981](https://github.com/pytest-dev/pytest-xdist/issues/981)
records the
maintainer clarification that one xdist group deliberately stays on one worker.
That is the required behavior for live OpenCode processes that share machine
resources.

OpenCode's [official plugin documentation](https://opencode.ai/docs/plugins/)
states that `.opencode/plugins/` is automatically loaded, while configured
plugins are a separate source. The practitioner confusion in
[OpenCode issue #1849](https://github.com/anomalyco/opencode/issues/1849)
shows why this path
contract needs an executable checker rather than tribal knowledge.

GitHub Community discussion
[#13690](https://github.com/orgs/community/discussions/13690) has tracked the
operational gap between a required check and an absent/skipped verdict since
2022. Gludd therefore treats missing terminal evidence as red, never green.

The
[pytest-cov 7 subprocess guidance](https://pytest-cov.readthedocs.io/en/latest/subprocess-support.html)
requires coverage.py's `patch = subprocess` setting after pytest-cov removed its
implicit child-process support. The tracked coverage profile applies that
upstream migration without changing the project-wide application profile.

## ZDD, rollback, security, and resources

This is a control-plane-only rollout: it changes no persisted application data
and restarts no serving process. Promote it from development only after the full
gate and exact-SHA CI pass. Rollback is a commit revert; the previous application
artifact continues serving throughout.

Locks live under the checkout's ignored `.gate-logs` namespace, while runtime
scratch uses the project/test temporary namespace. The gate rejects concurrent
mutation instead of killing unrelated processes. Hook output never prints
resolved OpenCode configuration or secrets, and the YAML check remains
fail-closed on parse or release-graph errors.

## Verification

- Focused gate, OpenCode, hook, and loader regressions.
- Full `test-hook-runtime` execution with real hook calls.
- Ruff, strict mypy, docstring/Markdown/spec lint, target-contract validation,
  aggregate coverage at least 85%, and every touched source file at least 75%.
- Full project gate and exact-SHA CI before release promotion.
