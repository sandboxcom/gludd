"""Red-team: correctness/security of MASTER's git + worktree automation.

Scope (visible code):
  - src/general_ludd/git_automation/repo.py      (GitAutomation)
  - src/general_ludd/git_automation/pr_delivery.py (PRDelivery -> gh pr create)
  - src/general_ludd/git_automation/issue_ingestor.py (untrusted issue -> todo)
  - src/general_ludd/worktree/core.py            (WorktreeMonitor / dispatcher)

KEY MEASURED FACT (see REDTEAM_GIT_WORKTREE.md):
  Every subprocess call in these modules uses LIST-FORM argv (no shell=True,
  no os.system, no f-string command). => classic SHELL injection (`; rm -rf`,
  `$(...)`, backticks, `|`) is NOT exploitable. These tests PROVE that
  property holds (regression guards) and then probe the residual, real risk:
  ARGUMENT injection via a leading-dash value reaching git/gh argv with no
  `--` end-of-options separator and no leading-dash rejection.

The bug/hardening tests below previously carried strict-xfail markers
documenting the gap against the unfixed tree. The markers have been removed
so the tests now assert the *fixed* behaviour (a `--` separator and/or a
leading-dash guard, real worktree removal, and a reconciled dispatcher
signature).

Run:  make test-iso TESTFILE='tests/security/test_git_worktree_redteam.py'
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from general_ludd.git_automation.pr_delivery import PRDelivery
from general_ludd.git_automation.repo import GitAutomation


class _ArgvRecorder:
    """Captures every argv list passed to subprocess.run and returns success."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self._rc = returncode
        self._stdout = stdout
        self._stderr = stderr

    def __call__(self, args: Any, *a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        return subprocess.CompletedProcess(args, self._rc, self._stdout, self._stderr)


# ---------------------------------------------------------------------------
# REGRESSION GUARDS (expected to PASS): no shell, list-form argv only.
# These lock in the property that protects against classic shell injection.
# ---------------------------------------------------------------------------


def test_commit_message_is_never_shell_interpreted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit message with shell metacharacters reaches git as a single
    literal argv element after `-m` -- never a shell string."""
    rec = _ArgvRecorder(stdout="deadbeef\n")
    monkeypatch.setattr(subprocess, "run", rec)

    evil = "pwn; rm -rf / #$(touch /tmp/pwned)`id`|cat /etc/passwd"
    GitAutomation("/repo").commit(evil)

    commit_calls = [c for c in rec.calls if c[:2] == ["git", "commit"]]
    assert commit_calls, "expected a `git commit` invocation"
    c = commit_calls[0]
    # message is the element right after -m, passed verbatim, no shell.
    assert "-m" in c
    assert c[c.index("-m") + 1] == evil


def test_no_shell_true_in_git_automation_or_pr_delivery() -> None:
    """Belt-and-braces: the source of these modules must not enable shell."""
    import inspect

    from general_ludd.git_automation import pr_delivery, repo

    for mod in (repo, pr_delivery):
        src = inspect.getsource(mod)
        assert "shell=True" not in src, f"{mod.__name__} must not use shell=True"
        assert "os.system" not in src, f"{mod.__name__} must not use os.system"


def test_pr_title_from_issue_cannot_be_a_standalone_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The PR title is always prefixed with `[gludd] {todo_id}: `, so even a
    malicious GitHub-issue title can never START with `-` and be parsed as a
    gh flag. (This SHOULD pass -- documents that the title vector is closed.)"""
    rec = _ArgvRecorder(stdout="https://example/pr/1\n")
    monkeypatch.setattr(subprocess, "run", rec)
    # gh --version probe + push + create all go through rec; make probe succeed.

    delivery = PRDelivery(base_branch="main")
    evil_title = "--repo attacker/evil"  # would hijack the target repo if injected
    delivery.push_and_create_pr(
        repo_path="/repo",
        branch_name="gludd-todo-abc",
        todo_id="TODO-DEADBEEF",
        title=evil_title,
    )
    create_calls = [c for c in rec.calls if c[:3] == ["gh", "pr", "create"]]
    assert create_calls, "expected a `gh pr create` invocation"
    c = create_calls[0]
    title_val = c[c.index("--title") + 1]
    assert title_val.startswith("[gludd] ")
    assert not title_val.startswith("-")


# ---------------------------------------------------------------------------
# RESIDUAL-RISK PROBES (now FIXED): argument injection via a leading-dash
# branch name must be separated by `--` and/or rejected.
# ---------------------------------------------------------------------------


def test_push_rejects_or_separates_leading_dash_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _ArgvRecorder()
    monkeypatch.setattr(subprocess, "run", rec)

    # A leading-dash branch must be rejected before any git push exec.
    with pytest.raises((ValueError, RuntimeError)):
        GitAutomation("/repo").push(remote="origin", branch="--receive-pack=touch /tmp/x")

    # And a normal push must still place a `--` end-of-options marker before
    # the branch so a dash-leading value could never be parsed as an option.
    rec.calls.clear()
    GitAutomation("/repo").push(remote="origin", branch="main")
    push_calls = [c for c in rec.calls if c[:2] == ["git", "push"]]
    assert push_calls
    c = push_calls[0]
    assert "--" in c, (
        "git push must place a `--` end-of-options separator before the branch "
        "so a leading-dash branch name cannot be parsed as a git option"
    )
    assert c.index("--") < c.index("main")


def test_pr_delivery_guards_leading_dash_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _ArgvRecorder(stdout="https://example/pr/1\n")
    monkeypatch.setattr(subprocess, "run", rec)

    delivery = PRDelivery(base_branch="main")
    result = delivery.push_and_create_pr(
        repo_path="/repo",
        branch_name="--upload-pack=evil",
        todo_id="TODO-1",
        title="ok",
    )
    # A dash-leading branch must be rejected fail-closed; no git push exec.
    assert result["pr_url"] is None
    assert result["error"]
    push_calls = [c for c in rec.calls if c[:2] == ["git", "push"]]
    assert not push_calls, "a dash-leading branch must never reach git push"

    # A normal branch must still place `--` before the branch in git push.
    rec.calls.clear()
    delivery.push_and_create_pr(
        repo_path="/repo",
        branch_name="gludd-todo-abc",
        todo_id="TODO-1",
        title="ok",
    )
    push_calls = [c for c in rec.calls if c[:2] == ["git", "push"]]
    assert push_calls
    c = push_calls[0]
    assert "--" in c, "git push branch must be after a `--` end-of-options marker"
    assert c.index("--") < c.index("gludd-todo-abc")


def test_create_branch_rejects_leading_dash_name(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _ArgvRecorder()
    monkeypatch.setattr(subprocess, "run", rec)

    # DESIRED behaviour: reject a structurally-invalid (dash-leading) branch name.
    with pytest.raises((ValueError, RuntimeError)):
        GitAutomation("/repo").create_branch("--orphan")


def test_merge_branch_separates_refs_from_options(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _ArgvRecorder(stdout="Updating\n")
    monkeypatch.setattr(subprocess, "run", rec)

    # A dash-leading source/target must be rejected before exec.
    with pytest.raises((ValueError, RuntimeError)):
        GitAutomation("/repo").merge_branch("/repo", source="--no-verify", target="main")

    # A normal merge must place a `--` before the source ref.
    rec.calls.clear()
    GitAutomation("/repo").merge_branch("/repo", source="feature", target="main")
    merge_calls = [c for c in rec.calls if c[:2] == ["git", "merge"]]
    assert merge_calls
    c = merge_calls[0]
    assert "--" in c, "git merge must place `--` before the source ref"
    assert c.index("--") < c.index("feature")
    checkout_calls = [c for c in rec.calls if c[:2] == ["git", "checkout"]]
    assert checkout_calls
    co = checkout_calls[0]
    # `git checkout <branch> --`: the `--` ends option parsing after the branch
    # (the only checkout form that both switches branches and is option-safe).
    assert "--" in co and co.index("main") < co.index("--")


def test_create_worktree_validates_path(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _ArgvRecorder()
    monkeypatch.setattr(subprocess, "run", rec)

    # DESIRED: a traversal path or dash-leading path is rejected before exec.
    res = GitAutomation("/repo").create_worktree(
        "/repo", branch_name="feat", worktree_path="../../etc/cron.d/evil"
    )
    assert res.success is False, (
        "create_worktree should refuse a path that escapes the repo parent"
    )

    # A dash-leading branch name is likewise rejected.
    res2 = GitAutomation("/repo").create_worktree(
        "/repo", branch_name="--orphan", worktree_path="/repo/wt"
    )
    assert res2.success is False


# ---------------------------------------------------------------------------
# WORKTREE MONITOR correctness probes.
# ---------------------------------------------------------------------------


def test_dispatcher_evaluate_signature_is_reconciled() -> None:
    """WorktreeEventDispatcher.on_agents_md_event calls
    `self._monitor.evaluate(watch_paths=[...])`. The monitor's evaluate must
    accept that keyword so wiring a real monitor in does not raise TypeError."""
    import inspect

    from general_ludd.worktree.core import WorktreeMonitor

    sig = inspect.signature(WorktreeMonitor.evaluate)
    assert "watch_paths" in sig.parameters, (
        "evaluate() must accept watch_paths so the dispatcher call site does "
        "not raise TypeError when a real monitor is wired in"
    )


def test_dispatcher_calls_real_monitor_without_typeerror() -> None:
    """A real WorktreeMonitor wired into the dispatcher must not raise when an
    AGENTS.md event is dispatched."""
    import os
    import tempfile

    from general_ludd.worktree.core import (
        WorktreeEventDispatcher,
        WorktreeMonitor,
        WorktreeMonitorConfig,
        WorktreeScanner,
    )

    with tempfile.TemporaryDirectory() as td:
        wt_dir = os.path.join(td, "wt")
        os.makedirs(wt_dir)
        # Make it look like a git worktree (a .git *file*, not dir).
        with open(os.path.join(wt_dir, ".git"), "w") as f:
            f.write("gitdir: /somewhere\n")
        with open(os.path.join(wt_dir, "AGENTS.md"), "w") as f:
            f.write("# Title\n")

        config = WorktreeMonitorConfig(watch_paths=[td])
        scanner = WorktreeScanner(config)
        monitor = WorktreeMonitor(config, scanner=scanner)
        dispatcher = WorktreeEventDispatcher(scanner, config, monitor=monitor)

        # Must not raise TypeError.
        dispatcher.on_agents_md_event(os.path.join(wt_dir, "AGENTS.md"))


def test_monitor_prunes_abandoned_worktree_dirs() -> None:
    from general_ludd.worktree.core import WorktreeMonitor, WorktreeScanner

    src = __import__("inspect").getsource(WorktreeMonitor)
    src += __import__("inspect").getsource(WorktreeScanner)
    assert "worktree remove" in src or "prune" in src, (
        "monitor should be able to prune/remove abandoned worktree dirs, not just "
        "forget them in memory"
    )
