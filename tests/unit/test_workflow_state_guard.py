from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_state_guard import collect_state, main, workflow_errors  # noqa: E402


def _completed(
    argv: Sequence[str],
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def _base_run(
    *,
    branch: str = "development",
    head: str = "devhead",
    status: str = "",
    remote_head: str = "devhead",
    master_head: str = "masterhead",
    development_head: str = "devhead",
    master_is_ancestor: bool = True,
):
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, branch + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, head + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, status)
        if args == ["git", "ls-remote", "sandboxcom", f"refs/heads/{branch}"]:
            output = "" if not remote_head else remote_head + chr(9) + f"refs/heads/{branch}" + newline
            return _completed(args, output)
        if args == ["git", "rev-parse", "--verify", "master"]:
            return _completed(args, master_head + newline if master_head else "", returncode=0 if master_head else 1)
        if args == ["git", "rev-parse", "--verify", "development"]:
            return _completed(
                args,
                development_head + newline if development_head else "",
                returncode=0 if development_head else 1,
            )
        if args == ["git", "merge-base", "--is-ancestor", master_head, development_head]:
            return _completed(args, "", returncode=0 if master_is_ancestor else 1)
        raise AssertionError(f"unexpected argv: {args!r}")

    return run


def test_workflow_gate_blocks_dirty_master_feature_edits() -> None:
    newline = chr(10)
    run = _base_run(branch="master", head="masterhead", status=" M Makefile" + newline)

    state = collect_state(run=run)
    errors = workflow_errors(state, assert_clean=True, assert_no_feature_on_master=True)

    assert "1 dirty path(s) make local test evidence unreproducible in GHA" in errors
    feature_error = (
        "feature or guardrail edits are present on master; "
        "move work to development or a release-sync worktree"
    )
    assert feature_error in errors


def test_merge_ready_blocks_topology_that_would_require_cherry_pick(capsys) -> None:
    run = _base_run(master_is_ancestor=False)

    rc = main(["--assert-merge-ready"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "WORKFLOW-BLOCKED" in captured.out
    assert "repair topology before release merge, do not cherry-pick" in captured.out


def test_merge_ready_passes_when_development_contains_master(capsys) -> None:
    run = _base_run(master_is_ancestor=True)

    rc = main(["--assert-merge-ready"], run=run)

    captured = capsys.readouterr()
    assert rc == 0
    assert "WORKFLOW-READY" in captured.out
    assert "master_in_development=True" in captured.out


def test_gha_head_must_match_local_head_for_ci_evidence(capsys) -> None:
    run = _base_run(head="newhead", remote_head="newhead")

    rc = main(["--assert-gha-matches-local", "--gha-head-sha", "oldhead"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "latest GHA head oldhead does not match local HEAD newhead" in captured.out


def test_remote_head_must_match_local_head_for_dispatch_evidence(capsys) -> None:
    run = _base_run(head="newhead", remote_head="oldhead")

    rc = main(["--assert-remote-head"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "remote sandboxcom/refs/heads/development is oldhead, not local HEAD newhead" in captured.out


def test_workflow_state_runfn_alias_is_python39_safe() -> None:
    source = (ROOT / "scripts" / "workflow_state_guard.py").read_text(encoding="utf-8")

    assert "RunFn = Callable[[Sequence[str], Optional[str]]" in source
    assert "RunFn = Callable[[Sequence[str], str | None]" not in source


def test_unintegrated_sibling_worktree_blocks_release_ci_gate(capsys) -> None:
    newline = chr(10)
    tab = chr(9)
    worktree_output = (
        "worktree /repo" + newline
        + "HEAD devhead" + newline
        + "branch refs/heads/development" + newline
        + newline
        + "worktree /repo-fix" + newline
        + "HEAD fixhead" + newline
        + "branch refs/heads/fix/full-run" + newline
        + newline
    )

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "development" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"] and cwd == "/repo-fix":
            return _completed(args, " M scripts/fix.py" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/development"]:
            return _completed(args, "devhead" + tab + "refs/heads/development" + newline)
        if args == ["git", "rev-parse", "--verify", "master"]:
            return _completed(args, "masterhead" + newline)
        if args == ["git", "rev-parse", "--verify", "development"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "merge-base", "--is-ancestor", "masterhead", "devhead"]:
            return _completed(args, "", returncode=0)
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return _completed(args, "/repo" + newline)
        if args == ["git", "worktree", "list", "--porcelain"]:
            return _completed(args, worktree_output)
        if args == ["git", "merge-base", "--is-ancestor", "fixhead", "devhead"]:
            return _completed(args, "", returncode=1)
        raise AssertionError(f"unexpected argv={args!r} cwd={cwd!r}")

    rc = main(["--assert-no-unintegrated-worktrees"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "WORKFLOW-BLOCKED" in captured.out
    assert "sibling worktree(s) contain unintegrated changes" in captured.out
    assert "UNINTEGRATED: path=/repo-fix" in captured.out
    assert "dirty,head_not_merged" in captured.out


def test_prunable_worktree_is_reported_without_running_git_in_missing_path(capsys) -> None:
    newline = chr(10)
    output = (
        "worktree /repo" + newline
        + "HEAD devhead" + newline
        + "branch refs/heads/development" + newline
        + newline
        + "worktree /repo-gone" + newline
        + "HEAD oldhead" + newline
        + "branch refs/heads/fix/old" + newline
        + "prunable gitdir file points to non-existent location" + newline
        + newline
    )

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "development" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            assert cwd != "/repo-gone"
            return _completed(args)
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/development"]:
            return _completed(args)
        if args == ["git", "rev-parse", "--verify", "master"]:
            return _completed(args, "masterhead" + newline)
        if args == ["git", "rev-parse", "--verify", "development"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "merge-base", "--is-ancestor", "masterhead", "devhead"]:
            return _completed(args)
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return _completed(args, "/repo" + newline)
        if args == ["git", "worktree", "list", "--porcelain"]:
            return _completed(args, output)
        raise AssertionError(f"unexpected argv={args!r} cwd={cwd!r}")

    rc = main(["--assert-no-unintegrated-worktrees"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "UNINTEGRATED: path=/repo-gone" in captured.out
    assert "prunable_registration" in captured.out


def test_clean_trunk_sibling_does_not_block_feature_branch_ci_push(capsys) -> None:
    newline = chr(10)
    tab = chr(9)
    worktree_output = (
        "worktree /repo-fix" + newline
        + "HEAD fixhead" + newline
        + "branch refs/heads/fix/full-run" + newline
        + newline
        + "worktree /repo" + newline
        + "HEAD masterhead" + newline
        + "branch refs/heads/master" + newline
        + newline
    )

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "fix/full-run" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "fixhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"] and cwd == "/repo":
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/fix/full-run"]:
            return _completed(args, "fixhead" + tab + "refs/heads/fix/full-run" + newline)
        if args == ["git", "rev-parse", "--verify", "master"]:
            return _completed(args, "masterhead" + newline)
        if args == ["git", "rev-parse", "--verify", "development"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "merge-base", "--is-ancestor", "masterhead", "devhead"]:
            return _completed(args, "", returncode=0)
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return _completed(args, "/repo-fix" + newline)
        if args == ["git", "worktree", "list", "--porcelain"]:
            return _completed(args, worktree_output)
        if args == ["git", "merge-base", "--is-ancestor", "masterhead", "fixhead"]:
            return _completed(args, "", returncode=1)
        raise AssertionError(f"unexpected argv={args!r} cwd={cwd!r}")

    rc = main(["--assert-no-unintegrated-worktrees"], run=run)

    captured = capsys.readouterr()
    assert rc == 0
    assert "WORKFLOW-READY" in captured.out
    assert "unintegrated_worktrees=0" in captured.out


def test_preserved_branch_with_unique_patches_blocks_ci_gate(capsys) -> None:
    newline = chr(10)
    tab = chr(9)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "fix/full-run" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "fixhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/fix/full-run"]:
            return _completed(args, "fixhead" + tab + "refs/heads/fix/full-run" + newline)
        if args == ["git", "rev-parse", "--verify", "master"]:
            return _completed(args, "masterhead" + newline)
        if args == ["git", "rev-parse", "--verify", "development"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "merge-base", "--is-ancestor", "masterhead", "devhead"]:
            return _completed(args, "", returncode=0)
        if args == ["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"]:
            return _completed(args, "main-dirty-preserve-20260722 preservehead" + newline)
        if args == [
            "git",
            "rev-list",
            "--cherry-pick",
            "--right-only",
            "--no-merges",
            "fixhead...main-dirty-preserve-20260722",
        ]:
            return _completed(args, "preservecommit" + newline)
        raise AssertionError(f"unexpected argv={args!r} cwd={cwd!r}")

    rc = main(["--assert-no-unintegrated-branches"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "preserved branch(es) contain unreconciled patches" in captured.out
    assert "UNINTEGRATED-BRANCH: branch=main-dirty-preserve-20260722" in captured.out


def test_preserved_branch_with_cherry_equivalent_patches_passes_ci_gate(capsys) -> None:
    newline = chr(10)
    tab = chr(9)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "fix/full-run" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "fixhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/fix/full-run"]:
            return _completed(args, "fixhead" + tab + "refs/heads/fix/full-run" + newline)
        if args == ["git", "rev-parse", "--verify", "master"]:
            return _completed(args, "masterhead" + newline)
        if args == ["git", "rev-parse", "--verify", "development"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "merge-base", "--is-ancestor", "masterhead", "devhead"]:
            return _completed(args, "", returncode=0)
        if args == ["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"]:
            return _completed(args, "main-dirty-preserve-20260722 preservehead" + newline)
        if args == [
            "git",
            "rev-list",
            "--cherry-pick",
            "--right-only",
            "--no-merges",
            "fixhead...main-dirty-preserve-20260722",
        ]:
            return _completed(args, "")
        raise AssertionError(f"unexpected argv={args!r} cwd={cwd!r}")

    rc = main(["--assert-no-unintegrated-branches"], run=run)

    captured = capsys.readouterr()
    assert rc == 0
    assert "WORKFLOW-READY" in captured.out
    assert "unintegrated_branches=0" in captured.out


def test_preserved_branch_ignores_commits_reachable_from_protected_trunks(capsys) -> None:
    newline = chr(10)
    tab = chr(9)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "fix/full-run" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "fixhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/fix/full-run"]:
            return _completed(args, "fixhead" + tab + "refs/heads/fix/full-run" + newline)
        if args == ["git", "rev-parse", "--verify", "master"]:
            return _completed(args, "masterhead" + newline)
        if args == ["git", "rev-parse", "--verify", "development"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "merge-base", "--is-ancestor", "masterhead", "devhead"]:
            return _completed(args, "", returncode=0)
        if args == ["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"]:
            return _completed(
                args,
                "master masterhead" + newline
                + "development devhead" + newline
                + "main-dirty-preserve-20260722 preservehead" + newline,
            )
        if args == [
            "git",
            "rev-list",
            "--cherry-pick",
            "--right-only",
            "--no-merges",
            "fixhead...main-dirty-preserve-20260722",
            "^master",
            "^development",
        ]:
            return _completed(args, "preservecommit" + newline)
        raise AssertionError(f"unexpected argv={args!r} cwd={cwd!r}")

    rc = main(["--assert-no-unintegrated-branches"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "unique_commits=1" in captured.out
    assert "main-dirty-preserve-20260722" in captured.out


def test_reconciled_preserved_branch_head_passes_ci_gate(capsys) -> None:
    newline = chr(10)
    tab = chr(9)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "fix/full-run" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "fixhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/fix/full-run"]:
            return _completed(args, "fixhead" + tab + "refs/heads/fix/full-run" + newline)
        if args == ["git", "rev-parse", "--verify", "master"]:
            return _completed(args, "masterhead" + newline)
        if args == ["git", "rev-parse", "--verify", "development"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "merge-base", "--is-ancestor", "masterhead", "devhead"]:
            return _completed(args, "", returncode=0)
        if args == ["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"]:
            return _completed(args, "main-dirty-preserve-20260722 preservehead" + newline)
        raise AssertionError(f"unexpected argv={args!r} cwd={cwd!r}")


    rc = main(
        [
            "--assert-no-unintegrated-branches",
            "--reconciled-preserve-head",
            "preservehead",
            "--reconciled-preserve-head-file",
            "",
        ],
        run=run,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "WORKFLOW-READY" in captured.out
    assert "unintegrated_branches=0" in captured.out
