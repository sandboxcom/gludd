"""Tests for git staging and mutation operations (stash, reset, add, rm, mv).

Each test creates a temporary git repo so mutations are verified against real
git behaviour without touching the workspace checkout.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from general_ludd.git_automation.repo import GitAutomation


@pytest.fixture
def temp_repo():
    """Create a temporary directory, init a git repo, and return its path."""
    tmp = tempfile.mkdtemp(prefix="gludd-test-mutations-")
    ga = GitAutomation(tmp)
    ga.init_repo()
    yield ga, Path(tmp)
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_file(repo_root: Path, name: str, content: str) -> Path:
    p = repo_root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _tracked_files(ga: GitAutomation) -> set[str]:
    out = ga._run_git("ls-files").stdout.strip()
    if not out:
        return set()
    return {line.strip() for line in out.splitlines()}


def _status(ga: GitAutomation) -> str:
    return ga._run_git("status", "--short").stdout


def _head_sha(ga: GitAutomation) -> str:
    return ga._run_git("rev-parse", "HEAD").stdout.strip()


# ── GitStash ─────────────────────────────────────────────────────────────────

class TestGitStash:
    def test_stash_saves_changes(self, temp_repo):
        ga, root = temp_repo
        f = _write_file(root, "a.txt", "hello")
        ga._run_git("add", "--", "a.txt")
        # dirty working tree after a commit so the file has modifications
        f.write_text("modified")

        ts = ga.stash(message="test-stash")
        assert ts is True
        assert f.read_text() == "hello"  # restored to committed version

    def test_stash_pop_restores_changes(self, temp_repo):
        ga, root = temp_repo
        f = _write_file(root, "b.txt", "v1")
        ga._run_git("add", "--", "b.txt")
        ga._run_git("commit", "-m", "base")
        f.write_text("v2")

        ga.stash(message="pop-test")
        assert f.read_text() == "v1"
        popped = ga.stash_pop()
        assert popped is True
        assert f.read_text() == "v2"

    def test_stash_handles_clean_tree(self, temp_repo):
        ga, root = temp_repo
        _write_file(root, "c.txt", "clean")
        ga._run_git("add", "--", "c.txt")
        ga._run_git("commit", "-m", "base")

        # clean tree → stash returns False but does not raise
        result = ga.stash(message="nothing")
        assert result is False


class TestGitStashPopEmpty:
    def test_stash_pop_empty_list(self, temp_repo):
        ga, _root = temp_repo
        result = ga.stash_pop()
        assert result is False


# ── GitReset ─────────────────────────────────────────────────────────────────

class TestGitReset:
    def test_reset_mixed_unstages(self, temp_repo):
        ga, root = temp_repo
        f = _write_file(root, "d.txt", "data")
        ga._run_git("add", "--", "d.txt")
        ga._run_git("commit", "-m", "base")
        f.write_text("modified")
        ga._run_git("add", "--", "d.txt")
        assert "M  d.txt" in _status(ga)

        ga.reset_mixed()
        assert " M d.txt" in _status(ga)

    def test_reset_soft_keeps_changes(self, temp_repo):
        ga, root = temp_repo
        f = _write_file(root, "e.txt", "v1")
        ga._run_git("add", "--", "e.txt")
        ga._run_git("commit", "-m", "base")
        f.write_text("v2")
        ga._run_git("add", "--", "e.txt")
        ga._run_git("commit", "-m", "second")

        sha_before = _head_sha(ga)
        ga.reset_soft(ref="HEAD~1")
        assert _head_sha(ga) != sha_before
        assert "M  e.txt" in _status(ga)
        assert f.read_text() == "v2"

    def test_reset_hard_discards_changes(self, temp_repo):
        ga, root = temp_repo
        f = _write_file(root, "f.txt", "v1")
        ga._run_git("add", "--", "f.txt")
        ga._run_git("commit", "-m", "base")
        f.write_text("v2")
        ga._run_git("add", "--", "f.txt")
        ga._run_git("commit", "-m", "second")

        ga.reset_hard(ref="HEAD~1")
        assert f.read_text() == "v1"
        assert _status(ga).strip() == ""

    def test_reset_to_specific_ref(self, temp_repo):
        ga, root = temp_repo
        _write_file(root, "g.txt", "first")
        ga._run_git("add", "--", "g.txt")
        ga._run_git("commit", "-m", "base")
        base = _head_sha(ga)
        _write_file(root, "h.txt", "second")
        ga._run_git("add", "--", "h.txt")
        ga._run_git("commit", "-m", "second")
        _write_file(root, "i.txt", "third")
        ga._run_git("add", "--", "i.txt")
        ga._run_git("commit", "-m", "third")

        ga.reset_hard(ref=base)
        assert _head_sha(ga) == base


# ── GitAdd ───────────────────────────────────────────────────────────────────

class TestGitAdd:
    def test_add_specific_files(self, temp_repo):
        ga, root = temp_repo
        _write_file(root, "x.txt", "x")
        _write_file(root, "y.txt", "y")
        ga.add(["x.txt"])
        staged = _status(ga)
        assert "x.txt" in staged
        assert "y.txt" not in staged or "??" in staged

    def test_add_all_stages_everything(self, temp_repo):
        ga, root = temp_repo
        _write_file(root, "a.txt", "a")
        _write_file(root, "sub/b.txt", "b")
        ga.add_all()
        staged = _status(ga)
        assert "??" not in staged

    def test_add_respects_gitignore(self, temp_repo):
        ga, root = temp_repo
        _write_file(root, ".gitignore", "*.log\n")
        _write_file(root, "data.log", "secret")
        _write_file(root, "src.py", "print(1)")
        ga.add_all()
        staged = _status(ga)
        assert "src.py" in staged
        assert "data.log" not in staged


# ── GitRm ────────────────────────────────────────────────────────────────────

class TestGitRm:
    def test_rm_removes_from_git(self, temp_repo):
        ga, root = temp_repo
        f = _write_file(root, "del.txt", "bye")
        ga._run_git("add", "--", "del.txt")
        ga._run_git("commit", "-m", "base")
        assert "del.txt" in _tracked_files(ga)

        ga.rm(["del.txt"])
        assert "del.txt" not in _tracked_files(ga)
        assert not f.exists()

    def test_rm_cached_keeps_on_disk(self, temp_repo):
        ga, root = temp_repo
        f = _write_file(root, "keep.txt", "keep me")
        ga._run_git("add", "--", "keep.txt")
        ga._run_git("commit", "-m", "base")
        assert "keep.txt" in _tracked_files(ga)

        ga.rm_cached(["keep.txt"])
        assert "keep.txt" not in _tracked_files(ga)
        assert f.exists()
        assert f.read_text() == "keep me"


# ── GitMv ────────────────────────────────────────────────────────────────────

class TestGitMv:
    def test_mv_renames_tracked_file(self, temp_repo):
        ga, root = temp_repo
        _write_file(root, "old.txt", "rename me")
        ga._run_git("add", "--", "old.txt")
        ga._run_git("commit", "-m", "base")
        assert "old.txt" in _tracked_files(ga)

        ga.mv("old.txt", "new.txt")
        assert "old.txt" not in _tracked_files(ga)
        assert "new.txt" in _tracked_files(ga)
        assert not (root / "old.txt").exists()
        assert (root / "new.txt").read_text() == "rename me"

    def test_mv_handles_directory(self, temp_repo):
        ga, root = temp_repo
        sub = root / "src"
        sub.mkdir()
        f = sub / "lib.py"
        f.write_text("pass")
        ga._run_git("add", "--", "src/lib.py")
        ga._run_git("commit", "-m", "base")

        dest = root / "lib"
        dest.mkdir()
        # git mv src/lib.py lib/lib.py — mv a file into a directory
        ga.mv("src/lib.py", "lib/lib.py")
        assert "src/lib.py" not in _tracked_files(ga)
        assert "lib/lib.py" in _tracked_files(ga)


# ── GitLsTracked ─────────────────────────────────────────────────────────────

class TestGitLsTracked:
    def test_ls_tracked_returns_files(self, temp_repo):
        ga, root = temp_repo
        _write_file(root, "one.py", "")
        _write_file(root, "two.py", "")
        ga._run_git("add", "--", "one.py", "two.py")
        ga._run_git("commit", "-m", "base")

        files = ga.ls_tracked()
        assert set(files) == {"one.py", "two.py"}

    def test_ls_tracked_empty_repo(self, temp_repo):
        ga, _root = temp_repo
        files = ga.ls_tracked()
        assert files == []


# ── GitRestore ───────────────────────────────────────────────────────────────

class TestGitRestore:
    def test_restore_discards_working_changes(self, temp_repo):
        ga, root = temp_repo
        f = _write_file(root, "rest.txt", "committed")
        ga._run_git("add", "--", "rest.txt")
        ga._run_git("commit", "-m", "base")
        f.write_text("dirty")

        ga.restore("rest.txt")
        assert f.read_text() == "committed"

    def test_restore_handles_untracked(self, temp_repo):
        ga, root = temp_repo
        _write_file(root, "new.txt", "fresh")
        # restoring an untracked file is a no-op / error; we should handle it
        result = ga.restore("new.txt")
        assert result is False
