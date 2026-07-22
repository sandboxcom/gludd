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


def test_ci_trigger_requires_exact_remote_head_guard() -> None:
    block = _target_block("ci-trigger")
    assert "ci-trigger: ci-remote-head-guard _require-gh" in block
    assert "--ref master" not in block
    assert "git branch --show-current" in block


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
    assert "gha-ready" in trigger_line
    assert "uncommitted files are not included" not in push_block
    assert "dirty local files are not included" not in trigger_block
    assert "Pushed clean HEAD" in push_block
    assert "Triggered Build and Release for clean HEAD" in trigger_block
    assert "HEAD:refs/heads/" in push_block
    assert "verify-remote" in push_block
    assert "gh workflow run \"Build and Release\"" in trigger_block
    assert "git-push-committed-head-nv" in combined_line
    assert "ci-trigger-committed-head" in combined_line


def test_workflow_state_machine_targets_back_release_and_ci_paths() -> None:
    makefile = _makefile()
    for target in ["workflow-state", "workflow-gate", "commit-ready", "gha-ready", "merge-ready"]:
        assert target + ":" in makefile

    gha_block = _target_block("gha-ready")
    merge_line = _target_line("development-merge-to-master")

    assert "scripts/workflow_state_guard.py --json" in _target_block("workflow-state")
    assert "--assert-clean --assert-no-feature-on-master" in _target_block("workflow-gate")
    assert "scripts/ci_remote_head_guard.py" in gha_block
    assert "/Users/shawnwilson/gludd/sandboxcom_github_rsa" in gha_block
    assert "merge-ready" in merge_line


def test_committed_head_ci_path_uses_worktree_safe_key_and_fails_closed() -> None:
    push_block = _target_block("git-push-committed-head-nv")
    gha_block = _target_block("gha-ready")
    trigger_block = _target_block("ci-trigger-committed-head")

    assert "/Users/shawnwilson/gludd/sandboxcom_github_rsa" in push_block
    assert "/Users/shawnwilson/gludd/sandboxcom_github_rsa" in gha_block
    assert "git push --no-verify -u sandboxcom HEAD:refs/heads/" in push_block
    assert "verify-remote BRANCH=" in push_block
    assert "gh workflow run \"Build and Release\"" in trigger_block
    assert push_block.count("|| exit 1") >= 3
    assert trigger_block.count("|| exit 1") >= 1


def test_git_remote_targets_use_worktree_safe_ssh_key_path() -> None:
    makefile = _makefile()
    assert "-i sandboxcom_github_rsa" not in makefile
    assert "/Users/shawnwilson/gludd/sandboxcom_github_rsa" in makefile


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


def test_committed_head_ci_path_checks_active_runs_before_push_and_dispatch() -> None:
    push_line = _target_line("git-push-committed-head-nv")
    trigger_line = _target_line("ci-trigger-committed-head")
    push_block = _target_block("git-push-committed-head-nv")
    trigger_block = _target_block("ci-trigger-committed-head")

    assert "ci-busy-check" in push_line or "ci-busy-check" in push_block
    assert "ci-busy-check" in trigger_line or "ci-busy-check" in trigger_block


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
    assert "GLUDD_XDIST=\"$(GLUDD_XDIST)\" $(SYSTEM_PYTHON) -c" not in makefile
    assert "GLUDD_XDIST=\"$(GLUDD_XDIST)\" python3 -c" not in makefile
    assert "VERSION := $(shell $(UV) run python" not in makefile
    assert "VERSION = $(shell $(UV) run python" in makefile
    for target in guard_targets:
        block = _target_block(target)

        assert "$(PYTHON)" not in block
        assert "python3 scripts/" not in block
        assert "UV=echo" in block
        assert "$(SYSTEM_PYTHON) scripts/" in block
