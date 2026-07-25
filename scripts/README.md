# Scripts — Gludd

200+ build, CI, quality, and operational helper scripts. All run under `make` targets; never invoked directly.

## Scripts by category

### Git operations

| Script | Description |
|---|---|
| `check_green_branch_guard.py` | Blocks pushes onto CI-green release branches (immutability guard) |
| `check_worktree_health.py` | Flags stale worktrees older than 24h with unmerged commits |
| `gated_merge.sh` | Guarded multi-branch merge with manifest |
| `ship_async.sh` | Background gate; ff-only merge on green |
| `push_rate_guard.py` | Push rate limiter: CI-pending cooldown, thrash detection |
| `check_clean_tree.py` | Exits non-zero if git working tree is dirty |

### CI / pipeline

| Script | Description |
|---|---|
| `ci_await.py` | Polls CI until terminal state (release-cut only) |
| `ci_check_cooldown.py` | CI check cooldown enforcer — blocks polling faster than 10 min |
| `ci_push_guard.py` | Blocks push while CI is pending on the target branch |
| `ci_annotations_poll.py` | Polls GitHub Actions annotations for live per-step status |
| `ci_dashboard.py` | CI run dashboard — tabular summary of recent runs |
| `ci_observability.py` | CI observability: streaming progress, heartbeat |
| `ci_run_summary.py` | Summarises a completed CI run (status, duration, failures) |
| `gha_usage.py` | GitHub Actions usage/metrics dashboard |
| `gha_cancelled_count.py` | Counts cancelled CI runs in a time window (thrash detection) |
| `require_ci_green.py` | Blocks release-cut unless CI is green on the target commit |
| `pipeline_status.py` | Pipeline status snapshot |

### Quality gates

| Script | Description |
|---|---|
| `check_tdd_compliance.py` | Blocks commits where modified source files lack test files |
| `check_dead_code.py` | Flags dead code — classes imported only in test files |
| `check_coverage_gaps.py` | Flags modules below coverage threshold |
| `check_coverage_missing.py` | Lists modules with zero test coverage |
| `check_node_v26_compat.py` | Scans plugin TS files for Node v26 `--experimental-strip-types` violations |
| `check_duplicate_targets.py` | Scans Makefile for duplicate target declarations |
| `check_tf_provider_versions.py` | Enforces canonical provider-version contract across Terraform stacks |
| `audit_coverage.py` | Per-file coverage audit with threshold check |
| `static_coverage_audit.py` | Static analysis of coverage gaps without running tests |
| `find_untested_modules.py` | Lists source modules with no corresponding test file |

### Enforcement / plugins

| Script | Description |
|---|---|
| `verify_enforcement.py` | Verifies all enforcement plugins are healthy and active |
| `verify_plugin_manifest.py` | Checks plugin manifest completeness (hooks, exports) |
| `validate_plugins.py` | Static analysis of plugin TS files (imports, hook shape, Node v26 compat) |
| `validate_plugins_runtime.mjs` | Runtime hook invocation validation — catches ReferenceError (undefined symbols) |
| `check_plugin_health.py` | Plugin health check — loads and validates each plugin |
| `check_plugin_hooks.py` | Checks plugin hook shape and registration |
| `check_plugin_imports.py` | Checks for forbidden import patterns in plugin code |
| `check_plugin_runtime.py` | Delegates to `validate_plugins_runtime.mjs` |
| `check_subagent_guards.py` | Verifies every plugin has the subagent guard (OPENCODE_SUBAGENT check) |
| `lean_enforcement_plugins.py` | Strips dead code from enforcement plugins |
| `fix_plugin_exports.py` | Auto-fixes missing plugin exports |

### Release

| Script | Description |
|---|---|
| `verify_release_artifact.py` | Checks if a release has at least one downloadable asset (not a bare tag) |
| `verify_release_completeness.py` | Full release completeness check — 12 artifact categories, version stamps, zero-size detection |
| `bump_version.py` | Bumps version across pyproject.toml, README.md, and `__init__.py` |
| `check_readme_status_current.py` | Ensures README status version matches the release version |
| `check_version_consistency.py` | Checks version consistency across all files |

### Testing

| Script | Description |
|---|---|
| `test_hook_runtime.py` | Functional hook runtime test harness — invokes actual plugin hooks |
| `run_unit_shards.py` | Runs unit tests in parallel shards (`pytest -n auto` wrapper) |
| `collect_nodeids.py` | Collects all pytest node IDs — used for test discovery |
| `junit_failures.py` | Extracts failures from JUnit XML output |
| `test_no_wait_hook.py` | Behavioral test for the no-wait hook |
| `test_no_false_completion_hook.py` | Behavioral test for the false-completion hook |
| `test_worktree_disk_guard.py` | Behavioral test for the worktree disk guard |
| `test_release_branch_guard.py` | Behavioral test for the release branch guard |

### Daemon / monitoring

| Script | Description |
|---|---|
| `agent_watchdog.py` | Background daemon watchdog — detects and unjams agent stops (10s poll) |
| `agent_liveness.py` | Counts live subagents for floor enforcement |
| `task_watchdog.py` | Kills subprocesses exceeding `GLUDD_TASK_TIMEOUT_MS` (SIGTERM → SIGKILL) |
| `smoke_daemon.py` | Quick daemon boot smoke test |
| `pipeline_status.py` | Pipeline status snapshot |

### Disk / tmp

| Script | Description |
|---|---|
| `clean_tmp.py` | Cleans `/tmp/gludd-*` state files, stale PIDs, oversized logs |
| `check_disk_usage.py` | Fails if `/tmp/gludd-*` exceeds 100MB or disk is >90% |
| `disk-guard.sh` | Disk cleanup at threshold (95%) |

### Task ledger

| Script | Description |
|---|---|
| `validate_task_ledger.py` | Validates TASKS.md structure — no duplicate IDs, consistent status |
| `check_task_ledger.py` | Checks task ledger integrity before dispatch |
| `auto_update_task_ledger.py` | Auto-updates TASKS.md from subagent results |

### Build

| Script | Description |
|---|---|
| `build_deck.py` | Builds reveal.js presentation deck (collects live stats, integrity check) |
| `build_hot_modules.js` | Compiles enforcement plugin hot-reload modules |
| `compile_plugins_for_test.mjs` | Compiles plugin TypeScript for test harness |
| `ts_to_js.js` | Transpiles TypeScript to JavaScript for Node execution |
