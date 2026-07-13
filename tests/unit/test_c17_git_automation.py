"""TDD tests for C.17: Git automation hardening.

Three fixes:
  1. merge_branch acquires per-repo lock for the ENTIRE merge sequence
  2. Squash path uses check=True (fail-closed, not fail-open)
  3. Branch-name collision detection prevents silent overwrites
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.types import MergeResult

# ---------------------------------------------------------------------------
# C.17-1: merge_branch acquires per-repo lock for the entire sequence
# ---------------------------------------------------------------------------

class TestMergeBranchPerRepoLock:
    """merge_branch must hold git_repo_lock around its entire sequence
    (checkout -> merge -> squash-commit), not release-acquire-release
    between each _run_git call. Individual per-invocation locks leave
    the sequence non-atomic — another process can interleave between
    checkout and merge."""

    def test_merge_branch_holds_lock_across_entire_sequence(self) -> None:
        """Verify merge_branch acquires git_repo_lock EXACTLY ONCE and
        ALL _run_git calls happen INSIDE that one lock context."""
        git = GitAutomation(repo_path="/fake/repo")

        run_git_results = [
            # checkout target
            MagicMock(returncode=0, stdout="Switched to branch 'master'", stderr=""),
            # merge
            MagicMock(returncode=0, stdout="Already up to date.", stderr=""),
        ]

        def fake_run_git(*args, **kwargs):
            return run_git_results.pop(0)

        lock_entered = False
        lock_exited_early = False
        lock_call_count = 0

        def fake_lock_ctx(cwd):
            nonlocal lock_entered, lock_exited_early, lock_call_count
            class FakeLockCtx:
                def __enter__(self):
                    nonlocal lock_entered, lock_exited_early, lock_call_count
                    lock_entered = True
                    lock_call_count += 1
                    return None
                def __exit__(self, *args):
                    nonlocal lock_entered, lock_exited_early
                    # If lock_exited_early is already True, this is a SECOND exit
                    # which means the lock was released and re-acquired (bug).
                    if lock_call_count > 1 and lock_entered:
                        lock_exited_early = True
                    lock_entered = False
                    return False
            return FakeLockCtx()

        with patch.object(git, "_run_git", side_effect=fake_run_git), \
             patch("general_ludd.git_automation.repo.git_repo_lock", side_effect=fake_lock_ctx):
            result = git.merge_branch(
                repo_path="/fake/repo", source="feature", target="master", strategy="ff"
            )

        assert result.success is True
        assert lock_call_count == 1, (
            f"git_repo_lock called {lock_call_count} times; "
            "should be 1 (one lock for entire merge_branch sequence)"
        )
        assert not lock_exited_early, (
            "Lock was released before all _run_git calls completed — "
            "merge_branch is not holding the lock for the entire sequence"
        )

    def test_merge_branch_lock_held_during_squash_commit(self) -> None:
        """Squash merge does TWO git operations (merge --squash, then commit).
        Both must happen inside the SAME lock context."""
        git = GitAutomation(repo_path="/fake/repo")

        run_git_results = [
            MagicMock(returncode=0, stdout="Switched to branch 'master'", stderr=""),
            MagicMock(returncode=0, stdout="Squash merge", stderr=""),
            MagicMock(returncode=0, stdout="Squash commit", stderr=""),
        ]

        def fake_run_git(*args, **kwargs):
            return run_git_results.pop(0)

        lock_call_count = 0

        def fake_lock_ctx(cwd):
            nonlocal lock_call_count
            class FakeLockCtx:
                def __enter__(self):
                    nonlocal lock_call_count
                    lock_call_count += 1
                    return None
                def __exit__(self, *args):
                    return False
            return FakeLockCtx()

        with patch.object(git, "_run_git", side_effect=fake_run_git), \
             patch("general_ludd.git_automation.repo.git_repo_lock", side_effect=fake_lock_ctx):
            result = git.merge_branch(
                repo_path="/fake/repo", source="feature", target="master", strategy="squash"
            )

        assert result.success is True
        assert lock_call_count == 1, (
            f"Squash merge lock count: {lock_call_count} — "
            "checkout + merge --squash + commit must all happen under ONE lock"
        )

    def test_gated_merge_holds_lock_across_entire_sequence(self) -> None:
        """gated_merge must also hold the lock for checkout -> merge -> gate -> commit."""
        git = GitAutomation(repo_path="/fake/repo")

        run_git_results = [
            MagicMock(returncode=0, stdout="Switched", stderr=""),
            MagicMock(returncode=0, stdout="abc123\n", stderr=""),
            MagicMock(returncode=0, stdout="Already up to date.", stderr=""),
            MagicMock(returncode=0, stdout="abc123\n", stderr=""),
        ]

        def fake_run_git(*args, **kwargs):
            return run_git_results.pop(0)

        lock_call_count = 0

        def fake_lock_ctx(cwd):
            nonlocal lock_call_count
            class FakeLockCtx:
                def __enter__(self):
                    nonlocal lock_call_count
                    lock_call_count += 1
                    return None
                def __exit__(self, *args):
                    return False
            return FakeLockCtx()

        with patch.object(git, "_run_git", side_effect=fake_run_git), \
             patch("general_ludd.git_automation.repo.git_repo_lock", side_effect=fake_lock_ctx), \
             patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            result = git.gated_merge(
                source="feature", target="master",
                gate_cmd=["true"], strategy="ff",
            )

        assert result.success is True
        assert lock_call_count == 1, (
            f"gated_merge lock count: {lock_call_count} — "
            "entire gated_merge must hold ONE lock"
        )


# ---------------------------------------------------------------------------
# C.17-2: Squash path is fail-closed (check=True)
# ---------------------------------------------------------------------------

class TestSquashFailClosed:
    """The squash path must be structurally fail-closed — any failed git
    invocation raises CalledProcessError (via check=True) rather than
    silently returning a CompletedProcess with non-zero returncode."""

    def test_merge_branch_squash_merge_uses_check_true(self) -> None:
        """The git merge step in the squash path must use check=True
        so a failed merge always raises, never returns silently."""
        git = GitAutomation(repo_path="/fake/repo")

        run_git_calls: list[tuple] = []

        def fake_run_git(*args, **kwargs):
            run_git_calls.append((args, kwargs))
            return MagicMock(returncode=0, stdout="Ok", stderr="")

        with patch.object(git, "_run_git", side_effect=fake_run_git), \
             patch("general_ludd.git_automation.repo.git_repo_lock") as _lock:
            git.merge_branch(
                repo_path="/fake/repo", source="feat", target="master", strategy="squash"
            )

        # Find the merge call (contains "--squash")
        merge_calls = [(a, k) for a, k in run_git_calls if "--squash" in a]
        assert len(merge_calls) == 1, f"Expected 1 merge --squash call, got {len(merge_calls)}"
        _args, kwargs = merge_calls[0]
        assert kwargs.get("check") is True, (
            "merge --squash must use check=True for fail-closed behavior; "
            "check=False is fail-open"
        )

    def test_merge_branch_squash_commit_uses_check_true(self) -> None:
        """The squash commit step already uses check=True — verify it stays that way."""
        git = GitAutomation(repo_path="/fake/repo")

        run_git_calls: list[tuple] = []

        def fake_run_git(*args, **kwargs):
            run_git_calls.append((args, kwargs))
            return MagicMock(returncode=0, stdout="Ok", stderr="")

        with patch.object(git, "_run_git", side_effect=fake_run_git), \
             patch("general_ludd.git_automation.repo.git_repo_lock") as _lock:
            git.merge_branch(
                repo_path="/fake/repo", source="feat", target="master", strategy="squash"
            )

        # Find the commit call
        commit_calls = [(a, k) for a, k in run_git_calls if "commit" in a]
        assert len(commit_calls) == 1, f"Expected 1 commit call, got {len(commit_calls)}"
        _args, kwargs = commit_calls[0]
        assert kwargs.get("check") is True, (
            "squash commit must use check=True for fail-closed behavior"
        )

    def test_gated_merge_squash_merge_uses_check_true(self) -> None:
        """gated_merge squash merge step must use check=True."""
        git = GitAutomation(repo_path="/fake/repo")

        run_git_calls: list[tuple] = []

        def fake_run_git(*args, **kwargs):
            run_git_calls.append((args, kwargs))
            return MagicMock(returncode=0, stdout="Ok\n", stderr="")

        with patch.object(git, "_run_git", side_effect=fake_run_git), \
             patch("general_ludd.git_automation.repo.git_repo_lock") as _lock, \
             patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            git.gated_merge(
                source="feat", target="master",
                gate_cmd=["true"], strategy="squash",
            )

        merge_calls = [(a, k) for a, k in run_git_calls if "--squash" in a]
        assert len(merge_calls) == 1
        _args, kwargs = merge_calls[0]
        assert kwargs.get("check") is True, (
            "gated_merge squash merge step must use check=True"
        )

    def test_gated_merge_squash_commit_uses_check_true(self) -> None:
        """gated_merge squash commit step must use check=True."""
        git = GitAutomation(repo_path="/fake/repo")

        run_git_calls: list[tuple] = []

        def fake_run_git(*args, **kwargs):
            run_git_calls.append((args, kwargs))
            return MagicMock(returncode=0, stdout="Ok\n", stderr="")

        with patch.object(git, "_run_git", side_effect=fake_run_git), \
             patch("general_ludd.git_automation.repo.git_repo_lock") as _lock, \
             patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            git.gated_merge(
                source="feat", target="master",
                gate_cmd=["true"], strategy="squash",
            )

        commit_calls = [(a, k) for a, k in run_git_calls if "commit" in a]
        assert len(commit_calls) == 1
        _args, kwargs = commit_calls[0]
        assert kwargs.get("check") is True, (
            "gated_merge squash commit must use check=True"
        )

    def test_merge_branch_squash_merge_failure_returns_failure_result(self) -> None:
        """When merge --squash fails, merge_branch must return success=False."""
        git = GitAutomation(repo_path="/fake/repo")

        run_git_results = [
            MagicMock(returncode=0, stdout="Switched", stderr=""),
            subprocess.CalledProcessError(1, ["git", "merge"], output="", stderr="conflict"),
        ]

        def fake_run_git(*args, **kwargs):
            item = run_git_results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(git, "_run_git", side_effect=fake_run_git), \
             patch("general_ludd.git_automation.repo.git_repo_lock") as _lock:
            result = git.merge_branch(
                repo_path="/fake/repo", source="feat", target="master", strategy="squash"
            )

        assert isinstance(result, MergeResult)
        assert result.success is False, (
            "Failed merge --squash must return success=False, not raise unhandled"
        )


# ---------------------------------------------------------------------------
# C.17-3: Branch-name collision detection
# ---------------------------------------------------------------------------

class TestBranchNameCollision:
    """create_branch must detect when a branch name already exists and
    raise a clear error rather than silently overwriting or failing
    with a cryptic git error."""

    def test_create_branch_collision_raises(self, tmp_path) -> None:
        """Creating a branch that already exists must raise ValueError."""
        repo_path = str(tmp_path / "collision-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "collision-repo" / "README.md").write_text("# test")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")

        git.create_branch("existing-branch")
        git._run_git("checkout", "master")

        with pytest.raises(ValueError, match="already exists"):
            git.create_branch("existing-branch")

    def test_create_branch_new_name_succeeds(self, tmp_path) -> None:
        """Creating a branch with a non-existing name must succeed."""
        repo_path = str(tmp_path / "no-collision-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "no-collision-repo" / "README.md").write_text("# test")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")

        result = git.create_branch("unique-branch")
        assert result == "unique-branch"

    def test_create_branch_rejects_leading_dash(self, tmp_path) -> None:
        """Leading-dash branch names are rejected (existing behavior)."""
        repo_path = str(tmp_path / "dash-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "dash-repo" / "README.md").write_text("# test")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")

        with pytest.raises(ValueError, match="begins with '-'"):
            git.create_branch("--evil")

    def test_create_branch_collision_detected_before_git_cmd(self, tmp_path) -> None:
        """Collision detection must happen BEFORE calling git, so the error
        message is clear and no git process is spawned for a known failure."""
        repo_path = str(tmp_path / "precheck-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "precheck-repo" / "README.md").write_text("# test")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")

        git.create_branch("first-branch")
        git._run_git("checkout", "master")

        git_checkout_called = False

        call_index = [0]

        def fake_run_git(*args, **kwargs):
            nonlocal git_checkout_called
            # First call: branch listing — must return "first-branch" in output
            if call_index[0] == 0:
                call_index[0] += 1
                return MagicMock(returncode=0, stdout="first-branch\nmaster\n", stderr="")
            # Any subsequent call would be the checkout — should never be reached
            git_checkout_called = True
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(git, "_run_git", side_effect=fake_run_git), pytest.raises(ValueError, match="already exists"):
            git.create_branch("first-branch")

        assert not git_checkout_called, (
            "Branch collision detected AFTER calling git checkout — "
            "detection must happen BEFORE spawning git checkout process"
        )

    def test_re_generate_branch_name_no_collision(self) -> None:
        """generate_branch_name already includes a UUID — collisions are
        astronomically unlikely, but the method must still accept the
        standard todo_id+slug input format."""
        git = GitAutomation()
        name = git.generate_branch_name(todo_id="000001", slug="fix-auth")
        assert name.startswith("agent/TODO-000001/fix-auth-")

    def test_merge_branch_rejects_leading_dash_source(self, tmp_path) -> None:
        """merge_branch must reject leading-dash source (existing behavior)."""
        repo_path = str(tmp_path / "merge-dash-repo")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "merge-dash-repo" / "README.md").write_text("# test")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")

        with pytest.raises(ValueError, match="begins with '-'"):
            git.merge_branch(repo_path=repo_path, source="--evil", target="master")

    def test_merge_branch_rejects_leading_dash_target(self, tmp_path) -> None:
        """merge_branch must reject leading-dash target (existing behavior)."""
        repo_path = str(tmp_path / "merge-dash-repo2")
        os.makedirs(repo_path)
        git = GitAutomation(repo_path=repo_path)
        git.init_repo(path=repo_path)
        (tmp_path / "merge-dash-repo2" / "README.md").write_text("# test")
        git._run_git("add", "-A")
        git._run_git("commit", "-m", "init")

        with pytest.raises(ValueError, match="begins with '-'"):
            git.merge_branch(repo_path=repo_path, source="master", target="--evil")
