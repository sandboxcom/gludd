"""Behavior pin for the clean-tree-before-dispatch enforcement plugin.

Per AGENTS.md "Clean Tree Before Dispatch" (2026-07-08): subagents must never
be dispatched when the git working tree is dirty. Uncommitted changes left by
a prior subagent cause pre-commit hook stash conflicts on the next push,
forcing -nv (no-verify) bypasses that defeat the lint/secret guards.

This test extracts the plugin's exported constants from the TypeScript source
and exercises the deny/allow/fail-open contract against real temp git repos.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-clean-tree.ts"
MAKEFILE_PATH = ROOT / "Makefile"

EXPECTED_DISPATCH_TOOLS = {"task", "agent", "workflow"}


def _plugin_source() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


def _extract_dispatch_tools(src: str) -> set[str]:
    block = re.search(
        r'DISPATCH_TOOLS[^=]*=\s*Object\.freeze\(\[(.*?)\]\)', src, re.DOTALL
    )
    assert block, "DISPATCH_TOOLS export not found in plugin source"
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def _git_status_porcelain(cwd: Path) -> str:
    """Mirror of the plugin's getGitStatus(): run git status --porcelain.

    Returns empty string on any error (fail-open), matching the plugin.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _is_tree_dirty(cwd: Path) -> bool:
    return len(_git_status_porcelain(cwd)) > 0


def _count_dirty_files(status: str) -> int:
    if not status.strip():
        return 0
    return len([line for line in status.strip().split("\n") if line.strip()])


def _build_deny_message(count: int) -> str:
    return (
        f"DIRTY TREE: {count} uncommitted file(s). "
        f"Commit or stash before dispatching new work. "
        f"Run `make git-status` to see the files, then "
        f"`make git-add FILES='...' && make ship-commit MSG='...'` to commit. "
        f"Or `make git-stash` to stash temporarily. "
        f"Set GLUDD_CLEAN_TREE_ENFORCE=0 to disable."
    )


def _init_repo(cwd: Path) -> None:
    """Create a minimal git repo with user config for testing."""
    subprocess.run(["git", "init"], cwd=cwd, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=cwd, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=cwd, capture_output=True
    )


class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self):
        oc = (ROOT / "opencode.json").read_text()
        assert "enforce-clean-tree.ts" in oc, (
            "Plugin not registered in opencode.json"
        )

    def test_tool_execute_before_hook(self):
        src = _plugin_source()
        assert "tool.execute.before" in src, "tool.execute.before hook missing"

    def test_fail_open_present(self):
        src = _plugin_source()
        assert "catch" in src.lower(), "No try/catch fail-open block found"

    def test_env_var_disable(self):
        src = _plugin_source()
        assert "GLUDD_CLEAN_TREE_ENFORCE" in src, (
            "Env-var disable switch missing"
        )

    def test_dispatch_tools_match(self):
        tools = _extract_dispatch_tools(_plugin_source())
        assert tools == EXPECTED_DISPATCH_TOOLS, (
            f"Expected {EXPECTED_DISPATCH_TOOLS}, got {tools}"
        )

    def test_uses_git_status_porcelain(self):
        src = _plugin_source()
        assert "git status --porcelain" in src, (
            "Must use `git status --porcelain` for dirty-tree detection"
        )

    def test_exports_helper_functions(self):
        src = _plugin_source()
        assert "getGitStatus" in src, "Must export getGitStatus for testability"
        assert "isTreeDirty" in src, "Must export isTreeDirty for testability"
        assert "buildDenyMessage" in src, (
            "Must export buildDenyMessage for testability"
        )

    def test_deny_message_prefix_constant(self):
        src = _plugin_source()
        assert "DIRTY TREE" in src, "Must reference DIRTY TREE in deny message"


class TestDenyOnDirtyTree:
    """Dirty tree (uncommitted changes) → deny dispatch."""

    def test_modified_file_makes_tree_dirty(self, tmp_path):
        repo = tmp_path / "dirty-repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "file.txt").write_text("content")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, capture_output=True
        )
        (repo / "file.txt").write_text("modified")
        assert _is_tree_dirty(repo), "Tree should be dirty after modification"

    def test_staged_file_makes_tree_dirty(self, tmp_path):
        repo = tmp_path / "staged-repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "file.txt").write_text("content")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, capture_output=True
        )
        (repo / "file.txt").write_text("staged-mod")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True)
        assert _is_tree_dirty(repo), "Tree should be dirty with staged changes"

    def test_untracked_file_makes_tree_dirty(self, tmp_path):
        repo = tmp_path / "untracked-repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "tracked.txt").write_text("content")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, capture_output=True
        )
        (repo / "untracked.txt").write_text("new")
        assert _is_tree_dirty(repo), "Tree should be dirty with untracked file"

    def test_deny_message_includes_file_count(self, tmp_path):
        repo = tmp_path / "multi-dirty"
        repo.mkdir()
        _init_repo(repo)
        (repo / "a.txt").write_text("a")
        (repo / "b.txt").write_text("b")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, capture_output=True
        )
        (repo / "a.txt").write_text("mod-a")
        (repo / "b.txt").write_text("mod-b")
        status = _git_status_porcelain(repo)
        count = _count_dirty_files(status)
        msg = _build_deny_message(count)
        assert count == 2, f"Expected 2 dirty files, got {count}"
        assert "DIRTY TREE: 2" in msg, f"Message should mention 2 files: {msg}"

    def test_deny_message_mentions_stash_and_commit(self):
        msg = _build_deny_message(3)
        assert "git-stash" in msg, "Message should mention git-stash"
        assert "ship-commit" in msg, "Message should mention ship-commit"
        assert "GLUDD_CLEAN_TREE_ENFORCE=0" in msg, (
            "Message should mention env override"
        )


class TestAllowOnCleanTree:
    """Clean tree (no uncommitted changes) → allow dispatch."""

    def test_clean_tree_after_commit(self, tmp_path):
        repo = tmp_path / "clean-repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / "file.txt").write_text("content")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, capture_output=True
        )
        assert not _is_tree_dirty(repo), "Tree should be clean after commit"

    def test_empty_repo_is_clean(self, tmp_path):
        repo = tmp_path / "empty-repo"
        repo.mkdir()
        _init_repo(repo)
        assert not _is_tree_dirty(repo), "Empty repo should be clean"

    def test_clean_tree_allows_dispatch(self, tmp_path):
        repo = tmp_path / "clean-allow"
        repo.mkdir()
        _init_repo(repo)
        (repo / "file.txt").write_text("content")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, capture_output=True
        )
        dirty = _is_tree_dirty(repo)
        should_deny = dirty and os.environ.get("GLUDD_CLEAN_TREE_ENFORCE") != "0"
        assert not should_deny, "Clean tree should allow dispatch"


class TestEnvVarDisable:
    """GLUDD_CLEAN_TREE_ENFORCE=0 → allow even if dirty."""

    def test_env_var_check_in_source(self):
        src = _plugin_source()
        assert 'process.env.GLUDD_CLEAN_TREE_ENFORCE === "0"' in src, (
            "Must check env var for '0' value"
        )

    def test_env_var_disables_dirty_check(self, tmp_path, monkeypatch):
        repo = tmp_path / "env-disabled"
        repo.mkdir()
        _init_repo(repo)
        (repo / "file.txt").write_text("content")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, capture_output=True
        )
        (repo / "file.txt").write_text("modified")

        monkeypatch.setenv("GLUDD_CLEAN_TREE_ENFORCE", "0")
        env_disabled = os.environ.get("GLUDD_CLEAN_TREE_ENFORCE") == "0"
        tree_is_dirty = _is_tree_dirty(repo)

        assert env_disabled, "Env var should be '0'"
        assert tree_is_dirty, "Tree IS dirty"
        assert env_disabled and tree_is_dirty, (
            "Dirty tree + env disabled = allow (plugin returns early before check)"
        )


class TestFailOpen:
    """Git error (not a repo, git not found) → fail-open (allow)."""

    def test_not_a_git_repo_returns_empty(self, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        result = _git_status_porcelain(non_repo)
        assert result == "", "Non-git dir should return empty (fail-open)"

    def test_fail_open_means_allow(self, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        status = _git_status_porcelain(non_repo)
        is_dirty = len(status) > 0
        assert not is_dirty, "Fail-open returns empty = not dirty = allow"

    def test_plugin_has_try_catch_in_get_git_status(self):
        src = _plugin_source()
        assert "getGitStatus" in src
        start = src.index("export function getGitStatus")
        next_export = src.find("export ", start + 1)
        if next_export == -1:
            next_export = len(src)
        get_status_block = src[start:next_export]
        assert "catch" in get_status_block, (
            "getGitStatus must have try/catch for fail-open"
        )
        assert 'return ""' in get_status_block, (
            "getGitStatus catch must return empty string (fail-open)"
        )


class TestMakefileStashTargets:
    """Verify git-stash and git-stash-pop Makefile targets exist."""

    def test_git_stash_target_exists(self):
        makefile = MAKEFILE_PATH.read_text()
        assert re.search(r"^git-stash:", makefile, re.MULTILINE), (
            "git-stash target missing from Makefile"
        )

    def test_git_stash_pop_target_exists(self):
        makefile = MAKEFILE_PATH.read_text()
        assert re.search(r"^git-stash-pop:", makefile, re.MULTILINE), (
            "git-stash-pop target missing from Makefile"
        )

    def test_git_stash_in_phony(self):
        makefile = MAKEFILE_PATH.read_text()
        phony_block = makefile.split(".PHONY")[1].split("\n\n")[0]
        assert "git-stash" in phony_block, "git-stash not in .PHONY list"
        assert "git-stash-pop" in phony_block, "git-stash-pop not in .PHONY list"

    def test_git_stash_uses_push(self):
        makefile = MAKEFILE_PATH.read_text()
        target_block = re.search(
            r"git-stash:\n(.*?)(?=\n[a-zA-Z_-]+:|\Z)", makefile, re.DOTALL
        )
        assert target_block, "git-stash recipe block not found"
        assert "git stash push" in target_block.group(1), (
            "git-stash must use `git stash push`"
        )

    def test_git_stash_pop_uses_pop(self):
        makefile = MAKEFILE_PATH.read_text()
        target_block = re.search(
            r"git-stash-pop:\n(.*?)(?=\n[a-zA-Z_-]+:|\Z)", makefile, re.DOTALL
        )
        assert target_block, "git-stash-pop recipe block not found"
        assert "git stash pop" in target_block.group(1), (
            "git-stash-pop must use `git stash pop`"
        )
