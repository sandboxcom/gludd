from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


def _makefile() -> str:
    return MAKEFILE.read_text()


def _target_line(target: str) -> str:
    prefix = target + ":"
    for line in _makefile().splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"{target} target missing")


def _target_block(target: str) -> str:
    lines = _makefile().splitlines()
    prefix = target + ":"
    start = next((idx for idx, line in enumerate(lines) if line.startswith(prefix)), None)
    assert start is not None, f"{target} block missing"
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        line = lines[idx]
        if line and not line.startswith((" ", chr(9), "#")) and re.match(r"[a-zA-Z0-9_.-]+:", line):
            end = idx
            break
    return chr(10).join(lines[start:end])


def test_push_paths_fail_closed_on_dirty_tree() -> None:
    for target in [
        "git-push-sandboxcom",
        "git-push-sandboxcom-nv",
        "git-push-current-head-nv",
        "git-push-current-head-to-master-nv",
        "push-dev",
        "development-push",
        "batch-push",
        "batch-push-nv",
        "ci-push",
    ]:
        line = _target_line(target)
        assert "check-clean-tree" in line or "pre-push-check" in line, target


def test_push_paths_have_ci_busy_or_rate_guard() -> None:
    guarded_targets = {
        "git-push-sandboxcom": "_push-rate-guard",
        "git-push-sandboxcom-nv": "_push-rate-guard",
        "git-push-current-head-nv": "_push-rate-guard",
        "git-push-current-head-to-master-nv": "_push-rate-guard",
        "push-dev": "ci-busy-check",
        "development-push": "ci-busy-check",
        "ci-push": "pre-push-check",
        "ci-push-and-verify": "pre-push-check",
    }
    for target, guard in guarded_targets.items():
        assert guard in _target_line(target) or guard in _target_block(target), target


def test_batch_push_blocks_single_commit_threshold_override() -> None:
    for target in ["batch-push", "batch-push-nv"]:
        block = _target_block(target)
        assert "COMMIT_THRESHOLD=1 bypass is disabled" in block
        assert "COMMIT_THRESHOLD=1 to override" not in block


def test_ci_trigger_delegates_to_idempotent_exact_sha_signal() -> None:
    line = _target_line("ci-trigger")
    block = _target_block("ci-trigger")

    assert line == "ci-trigger: ci-trigger-committed-head"
    assert "gh workflow run" not in block
    assert "ci-trigger-failed" not in block


def test_push_workflow_runs_for_development_and_master_without_canceling_push_runs() -> None:
    workflow = BUILD_WORKFLOW.read_text()
    assert "branches:" in workflow
    assert "- development" in workflow
    assert "- master" in workflow
    assert "workflow_dispatch:" in workflow
    assert "github.sha" in workflow, "push runs must be keyed by SHA, not branch ref"
    assert "cancel-in-progress:" in workflow
    assert "github.event_name ==" in workflow
    assert "pull_request" in workflow


def test_release_paths_verify_complete_artifact_set_before_publish_or_done() -> None:
    for target in ["release-cut", "release-recut", "release-deploy"]:
        assert "verify-release-completeness" in _target_block(target), target


def test_release_deploy_does_not_swallow_ci_await_failure() -> None:
    block = _target_block("release-deploy")
    assert "ci-await BRANCH=master || true" not in block
    assert "ci-await BRANCH=master" in block


def test_development_push_verifies_remote_sha_after_push() -> None:
    block = _target_block("development-push")
    assert "verify-remote BRANCH=development" in block
    assert "git rev-parse development" in block


def test_committed_head_ci_path_requires_clean_state_before_push_and_dispatch() -> None:
    push_line = _target_line("git-push-committed-head-nv")
    trigger_line = _target_line("ci-trigger-committed-head")
    push_block = _target_block("git-push-committed-head-nv")
    trigger_block = _target_block("ci-trigger-committed-head")
    combined_line = _target_line("ci-push-committed-head")

    assert "commit-ready" in push_line
    assert "workflow-gate" not in push_line
    assert trigger_line == "ci-trigger-committed-head:"
    assert "gha-ready" not in trigger_block
    assert "workflow-gate" not in trigger_block
    assert "_require-gh" in trigger_block
    assert "uncommitted files are not included" not in push_block
    assert "dirty local files are not included" not in trigger_block
    assert "Pushed clean HEAD" in push_block
    assert "scripts/ci_signal_exact_sha.py" in trigger_block
    assert "HEAD:refs/heads/" in push_block
    assert "verify-remote" in push_block
    assert "git-push-committed-head-nv" in combined_line
    assert "ci-trigger-committed-head" in combined_line


def test_workflow_state_machine_targets_back_release_and_ci_paths() -> None:
    makefile = _makefile()
    for target in ["workflow-state", "workflow-gate", "commit-ready", "gha-ready", "merge-ready"]:
        assert target + ":" in makefile

    workflow_gate = _target_block("workflow-gate")
    gha_block = _target_block("gha-ready")
    merge_block = _target_block("merge-ready")
    merge_line = _target_line("development-merge-to-master")

    assert "scripts/workflow_state_guard.py --json" in _target_block("workflow-state")
    assert "--assert-clean --assert-no-feature-on-master" in workflow_gate
    assert "--assert-no-unintegrated-worktrees" in workflow_gate
    assert "workflow-gate" in _target_line("gha-ready")
    assert "scripts/ci_remote_head_guard.py" in gha_block
    assert "$(SSH_KEY)" in gha_block
    assert "--assert-no-unintegrated-worktrees" in merge_block
    assert "merge-ready" in merge_line


def test_committed_head_ci_path_uses_worktree_safe_key_and_fails_closed() -> None:
    push_block = _target_block("git-push-committed-head-nv")
    gha_block = _target_block("gha-ready")
    trigger_block = _target_block("ci-trigger-committed-head")

    assert "$(SSH_KEY)" in push_block
    assert "$(SSH_KEY)" in gha_block
    assert "git push --no-verify -u sandboxcom HEAD:refs/heads/" in push_block
    assert "verify-remote BRANCH=" in push_block
    assert "scripts/ci_signal_exact_sha.py" in trigger_block
    assert push_block.count("|| exit 1") >= 3
    assert trigger_block.count("|| exit 1") >= 2


def test_git_remote_targets_use_worktree_safe_ssh_key_path() -> None:
    makefile = _makefile()
    assert "-i sandboxcom_github_rsa" not in makefile
    assert "SSH_KEY ?= $(HOME)/.ssh/sandboxcom_gludd_rsa" in makefile
    assert 'ssh -i $(SSH_KEY)' in makefile


def test_local_ci_replica_shards_refuse_dirty_tree_by_default() -> None:
    for target in [
        "test-ci-shard",
        "test-ci-shard-summary",
        "test-ci-shard-slice",
        "test-ci-shards-parallel",
        "test-ci-shards-parallel-bg",
    ]:
        assert "_ci-replica-clean-tree" in _target_line(target), target

    guard = _target_block("_ci-replica-clean-tree")
    assert "scripts/worktree_state_guard.py --assert-clean --claim-token" in guard
    assert "ALLOW_DIRTY_FOCUSED_REPRO" in guard
    assert "PYTEST_ARGS" in guard
    assert "CI-like shard validation requires a clean worktree" in guard
    assert "Commit completed work or create a clean worktree at the pushed HEAD" in guard


def test_parallel_shard_targets_fail_before_launch_when_shards_are_unquoted() -> None:
    for target in ["test-ci-shards-parallel", "test-ci-shards-parallel-bg"]:
        block = _target_block(target)
        assert "$(filter-out $@,$(MAKECMDGOALS))" in block, target
        assert "quote SHARDS with spaces" in block, target
        assert block.find("quote SHARDS with spaces") < block.find(
            "run_ci_shards_parallel.py"
        ) or block.find("quote SHARDS with spaces") < block.find(
            "start_ci_shards_parallel_bg.py"
        ), target


def test_list_valued_make_targets_fail_at_parse_time_on_stray_goals() -> None:
    makefile = _makefile()
    assert "_MULTIWORD_VALUE_GOALS" in makefile
    assert "_EXTRA_MAKE_GOALS" in makefile
    assert "Quote multi-word variable values" in makefile

    for target in [
        "git-add",
        "git-restore",
        "git-commit",
        "lint-files",
        "search",
        "test-files",
        "test-ci-shards-parallel-bg",
    ]:
        assert target in makefile


def test_committed_head_ci_path_checks_active_runs_before_push_and_dispatch() -> None:
    push_line = _target_line("git-push-committed-head-nv")
    trigger_line = _target_line("ci-trigger-committed-head")
    push_block = _target_block("git-push-committed-head-nv")
    trigger_block = _target_block("ci-trigger-committed-head")

    assert "commit-ready" in push_line
    assert "workflow-gate" not in push_line
    assert trigger_line == "ci-trigger-committed-head:"
    assert "gha-ready" not in trigger_block
    assert "_require-gh" in trigger_block
    assert "ci-busy-check" in push_line or "ci-busy-check" in push_block
    assert "scripts/ci_signal_exact_sha.py" in trigger_block


def test_ci_busy_check_defaults_to_current_branch_not_master() -> None:
    block = _target_block("ci-busy-check")
    dollar = chr(36)
    legacy_default = dollar + "(or " + dollar + "(BRANCH),master)"
    branch_arg = "scripts/ci_push_guard.py \"" + dollar + dollar + "BRANCH\""

    assert legacy_default not in block
    assert "git branch --show-current" in block
    assert branch_arg in block


def test_worktree_state_guard_targets_are_path_qualified_release_gates() -> None:
    makefile = _makefile()
    required_scripts = [
        "worktree_state_guard.py",
        "run_ci_shard_summary.py",
        "run_ci_shards_parallel.py",
        "start_ci_shards_parallel_bg.py",
        "ci_shards_parallel_status.py",
    ]
    for script_name in required_scripts:
        script = ROOT / "scripts" / script_name
        assert script.exists(), f"{script_name} must ship with Makefile guard targets"

    script_text = (ROOT / "scripts" / "worktree_state_guard.py").read_text()
    assert "def current_state" in script_text
    assert "def main_worktree_state" in script_text
    for target in [
        "worktree-state",
        "all-worktree-state",
        "main-worktree-state",
        "worktree-guard",
        "main-worktree-guard",
        "release-worktree-guard",
        "status-claim-guard",
    ]:
        assert target + ":" in makefile

    guard = _target_block("_ci-replica-clean-tree")
    assert "scripts/worktree_state_guard.py --assert-clean --claim-token" in guard
    assert "WORKTREE-CLEAN" in guard or "worktree_state_guard.py" in guard

    main_guard = _target_block("main-worktree-guard")
    assert "--main-path /Users/shawnwilson/gludd --assert-main-clean --main-claim-token" in main_guard

    for target in ["release-worktree-guard", "status-claim-guard"]:
        line = _target_line(target)
        assert "worktree-guard" in line
        assert "main-worktree-guard" in line

    for target in ["test-ci-shard", "test-ci-shard-summary", "test-ci-shard-slice"]:
        assert "_ci-replica-clean-tree" in _target_line(target), target


def test_workflow_state_targets_do_not_dirty_lockfile_with_uv_run() -> None:
    guard_targets = [
        "worktree-state",
        "all-worktree-state",
        "main-worktree-state",
        "worktree-guard",
        "main-worktree-guard",
        "release-worktree-guard",
        "status-claim-guard",
        "workflow-state",
        "workflow-gate",
        "commit-ready",
        "gha-ready",
        "merge-ready",
    ]
    makefile = _makefile()
    assert "override SYSTEM_PYTHON := /usr/bin/python3" in makefile
    assert "getconf _NPROCESSORS_ONLN" in makefile
    assert "GLUDD_XDIST_WORKERS=\"$(GLUDD_XDIST_WORKERS)\" $(SYSTEM_PYTHON) -c" not in makefile
    assert "GLUDD_XDIST_WORKERS=\"$(GLUDD_XDIST_WORKERS)\" python3 -c" not in makefile
    assert "VERSION := $(shell $(UV) run python" not in makefile
    assert "VERSION = $(shell $(UV) run python" in makefile
    no_uv_goals = makefile.split("_NO_UV_SYNC_GOALS :=", 1)[1].split("ifneq", 1)[0]
    for goal in [
        *guard_targets,
        "git-where",
        "repo-status",
        "git-status",
        "git-remote-sandboxcom",
        "git-pull-sandboxcom",
        "git-fetch-sandboxcom",
        "verify-remote",
        "git-branch",
        "git-checkout",
        "git-add",
        "git-merge",
        "git-merge-nc",
        "git-merge-abort",
        "git-rebase-abort",
        "git-cherry-pick",
        "git-cherry-pick-list",
        "git-cherry-pick-continue",
        "git-cherry-pick-skip",
        "git-cherry-pick-abort",
        "ci-remotes",
        "ci-diff-since-remote",
        "ci-head-compare",
        "ci-remote-head-guard",
        "ci-trigger",
        "git-push-committed-head-nv",
        "ci-trigger-committed-head",
        "ci-push-committed-head",
        "git-push-current-head-to-master-nv",
        "search",
        "show-lines",
        "cat-file",
        "copy-file",
        "mkdir-p",
        "write-text",
        "append-text",
        "replace-lines",
        "replace-text",
        "replace-all-text",
        "write-text-b64",
        "replace-text-b64",
        "tmp-gludd-usage",
        "tmp-gludd-worktree-usage",
        "tmp-gludd-clean-ci-shards",
        "clean-worktree-venvs",
        "clean-worktree-caches",
    ]:
        assert goal in no_uv_goals
    for target in guard_targets:
        block = _target_block(target)

        assert "$(PYTHON)" not in block
        assert "python3 scripts/" not in block
        assert "UV=echo" in block
        assert "$(SYSTEM_PYTHON) scripts/" in block


def test_lightweight_status_target_never_bootstraps_project_venv() -> None:
    """A read-only resource snapshot must use the dependency-free interpreter."""
    makefile = _makefile()
    no_uv_goals = makefile.split("_NO_UV_SYNC_GOALS :=", 1)[1].split("ifneq", 1)[0]
    block = _target_block("active-work-status")

    assert "active-work-status" in no_uv_goals
    assert "$(UV) run" not in block
    assert "$(SYSTEM_PYTHON) scripts/active_work_status.py" in block


def test_tmp_gludd_cleanup_targets_are_scoped_to_generated_dirs() -> None:
    makefile = _makefile()
    phony_block = makefile.split(".PHONY:", 1)[1].split("help:", 1)[0]
    usage_block = _target_block("tmp-gludd-usage")
    tmp_cleanup = _target_block("clean-tmp")
    worktree_usage = _target_block("tmp-gludd-worktree-usage")
    shard_cleanup = _target_block("tmp-gludd-clean-ci-shards")
    venv_cleanup = _target_block("clean-worktree-venvs")
    cache_cleanup = _target_block("clean-worktree-caches")
    venv_cleaner = (ROOT / "scripts" / "clean_worktree_venvs.py").read_text()

    assert phony_block.count("log-agent-result disk-guard") == 1
    assert "sort -h | tail -40" in usage_block
    assert "scripts/clean_tmp.py" in tmp_cleanup
    assert "rm -rf" not in tmp_cleanup
    assert "/tmp/gludd-worktrees/*/.pytest_cache" in worktree_usage

    assert "$(SYSTEM_PYTHON) -m scripts.clean_ci_shard_scratch" in shard_cleanup
    assert "rm -rf /tmp/gludd-ci-shard-*" not in shard_cleanup
    assert "rm -rf /tmp/gludd-unit-shard-*" not in shard_cleanup
    assert "/tmp/gludd-worktrees" not in shard_cleanup
    assert "/Users/shawnwilson/gludd" not in shard_cleanup

    assert "$(SYSTEM_PYTHON) -m scripts.clean_worktree_venvs" in venv_cleanup
    assert "rm -rf" not in venv_cleanup
    assert 'Path("/tmp/gludd-worktrees")' in venv_cleaner
    assert 'Path("/Users/shawnwilson/gludd/.claude/worktrees")' in venv_cleaner
    assert "registered_worktree_paths" in venv_cleaner
    assert "active_process_pids" in venv_cleaner
    assert "invoking-worktree" in venv_cleaner

    assert "clean-worktree-caches: clean-worktree-venvs" in cache_cleanup
    assert "/usr/bin/find /tmp/gludd-worktrees -type d" in cache_cleanup
    assert "/usr/bin/find /Users/shawnwilson/gludd/.claude/worktrees -type d" in cache_cleanup
    for cache_name in (".pytest_cache", ".mypy_cache", ".ruff_cache"):
        assert f"-name {cache_name}" in cache_cleanup
    assert "-prune -exec rm -rf {} +" in cache_cleanup
    assert "/tmp/gludd-worktrees/* " not in cache_cleanup


def test_ci_head_compare_reports_bidirectional_divergence() -> None:
    block = _target_block("ci-head-compare")

    assert "--- sandboxcom/master HEAD ---" in block
    assert "sandboxcom/master..HEAD" in block
    assert "HEAD..sandboxcom/master" in block
    assert block.count("--- commits local has that remote does NOT ---") == 1
    assert block.count("--- commits remote has that local does NOT ---") == 1


def test_secrets_scan_targets_do_not_dirty_committed_baseline() -> None:
    for target in ("scan-secrets", "secrets-scan"):
        block = _target_block(target)
        assert "$$(mktemp /tmp/gludd-secrets-baseline." in block
        assert "cp .secrets.baseline \"$$TMP\"" in block
        assert "$(UV) run detect-secrets scan --baseline \"$$TMP\"" in block
        assert "--baseline .secrets.baseline" not in block


def test_git_show_file_to_is_scoped_to_safe_restore_outputs() -> None:
    block = _target_block("git-show-file-to")
    assert "git show" in block
    assert ":$(FILE)" in block
    assert "> \"$(OUT)\"" in block
    assert ".opencode/plugin/impl/*" in block
    assert "Refusing unsafe FILE" in block
    assert "Refusing unsafe OUT" in block


def test_test_failures_is_a_bounded_read_only_cache_reporter() -> None:
    block = _target_block("test-failures")
    assert "scripts/report_pytest_failures.py" in block
    assert "$(TEST_FAILURES_CACHE)" in block
    assert "$(TEST_FAILURES_LIMIT)" in block
    assert "python -m pytest" not in block
    assert "pytest tests/" not in block
    assert "$(_XD)" not in block
    assert "tee" not in block


def test_search_target_allows_scoped_tmp_gludd_logs_only() -> None:
    block = _target_block("search")
    assert "/tmp/gludd-*)" in block
    assert "/*" + chr(124) + "*..*)" in block
    assert "Refusing path outside workspace" in block


def test_kill_stale_reaps_orphaned_workspace_gunicorn_daemon_tree() -> None:
    block = _target_block("kill-stale")
    assert r"/Users/shawnwilson/gludd/\.venv/bin/gunicorn general_ludd\.daemon:create_daemon_app" in block
    assert "KILLED stale orphan daemon tree" in block
    assert "pgrep -P \"$$pid\"" in block
    assert "active non-daemon" in block
    assert "childless gludd scratch only" not in block
