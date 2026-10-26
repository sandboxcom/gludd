"""Tests for the worktree-per-subagent dispatch protocol.

Verifies the Makefile targets `agent-worktree`, `agent-merge`, `agent-cleanup`,
and `agent-worktree-list` exist and that the create/teardown lifecycle actually
manipulates an isolated git worktree on disk. Mirrors the structural-scan style
of test_gate_background_targets.py and test_makefile_targets.py.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

from tests.unit.test_agent_worktree_creation_fail_closed import WorktreeCreation, _creation_fixture
from tests.unit.test_single_native_commit_collection import _block
from tests.unit.test_worktree_prune_preservation import (
    test_cleanup_alias_applies_the_real_registered_native_retirement as _assert_native_retirement,
)

pytest_plugins = ("tests.unit.test_worktree_retirement",)

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _content() -> str:
    assert MAKEFILE.exists(), "Makefile must exist"
    return compose_makefile(MAKEFILE)


def _recipe(target: str, *, source: str | None = None) -> str:
    """Inspect the complete literal rule and its actual shared creation body."""
    content = _content() if source is None else source
    rule = _block(content, target)
    if "$(call _agent-worktree-create," in rule:
        macro = re.search(r"^define _agent-worktree-create\n.*?^endef\n",
                          content, re.MULTILINE | re.DOTALL)
        assert macro is not None, "Missing invoked worktree creation macro"
        rule += "\n" + macro.group()
    return rule


class TestAgentWorktreeTargetsExist:
    """The four worktree-per-subagent targets must be present in the Makefile."""

    def test_agent_worktree_target_exists(self) -> None:
        assert "agent-worktree:" in _content(), (
            "Makefile missing 'agent-worktree:' target"
        )

    def test_agent_merge_target_exists(self) -> None:
        assert "agent-merge:" in _content(), (
            "Makefile missing 'agent-merge:' target"
        )

    def test_agent_cleanup_target_exists(self) -> None:
        assert "agent-cleanup:" in _content(), (
            "Makefile missing 'agent-cleanup:' target"
        )

    def test_agent_worktree_list_target_exists(self) -> None:
        assert "agent-worktree-list:" in _content(), (
            "Makefile missing 'agent-worktree-list:' target"
        )


class TestAgentWorktreeRecipeShape:
    """Recipe bodies enforce the documented contract."""

    def test_agent_worktree_uses_git_worktree_add(self) -> None:
        recipe = _recipe("agent-worktree")
        assert "git worktree add" in recipe, (
            "agent-worktree must invoke `git worktree add` to create the checkout"
        )

    def test_agent_worktree_writes_worktree_path_marker(self) -> None:
        recipe = _recipe("agent-worktree")
        assert "WORKTREE_PATH=" in recipe, (
            "agent-worktree must print WORKTREE_PATH=<path> for the caller"
        )

    def test_agent_worktree_uses_quarantine_dir(self) -> None:
        recipe = _recipe("agent-worktree")
        assert "/tmp/gludd-worktrees/" in recipe, (
            "agent-worktree must place worktrees under /tmp/gludd-worktrees/ "
            "(not inside the shared master checkout)"
        )

    def test_agent_worktree_requires_branch_arg(self) -> None:
        recipe = _recipe("agent-worktree")
        assert "$(BRANCH)" in recipe, (
            "agent-worktree must consume a BRANCH=<name> argument"
        )

    def test_agent_merge_refuses_before_git_and_names_canonical_owner(self) -> None:
        recipe = _recipe("agent-merge")
        assert "outcome=retired" in recipe and "native_status=2" in recipe
        assert "exit 2" in recipe and "release-promote" in recipe
        assert "git merge" not in recipe and "git checkout" not in recipe
        canonical = _recipe("development-fast-forward")
        assert "merge --ff-only" in canonical
        assert "EXPECTED_BEFORE_SHA" in canonical and "EXPECTED_AFTER_SHA" in canonical

    def test_agent_cleanup_delegates_to_registered_native_retirement(self) -> None:
        recipe = _recipe("agent-cleanup")
        assert "scripts.prune_worktrees_safe --retire-merged" in recipe
        assert "--branch" in recipe and "--worktree" in recipe
        assert "ACTIVE_WORKSTREAM_REGISTRY" in recipe
        assert "--retire-validate-only" in recipe
        assert "AGENT_CLEANUP_VALIDATE_ONLY" in recipe

    def test_agent_worktree_list_is_readonly(self) -> None:
        recipe = _recipe("agent-worktree-list")
        assert "git worktree list" in recipe, (
            "agent-worktree-list must invoke `git worktree list`"
        )


@pytest.mark.parametrize("creation_graph_target", [
    "agent-worktree", "agent-worktree-base", "agent-worktree-dev",
])
def test_creation_recipe_graph_keeps_padded_native_command(creation_graph_target: str) -> None:
    source = _content().replace(
        f"{creation_graph_target}:\n", f"{creation_graph_target}:\n\t# " + "padding " * 1000 + "\n",
        1,
    )
    assert "git worktree add" in _recipe(creation_graph_target, source=source)


@pytest.mark.parametrize("creation_graph_target", [
    "agent-worktree", "agent-worktree-base", "agent-worktree-dev",
])
def test_creation_recipe_graph_never_borrows_neighbor_commands(creation_graph_target: str) -> None:
    source = _content().replace("git worktree add", "git worktree list")
    source += "\ncreation-neighbor:\n\tgit worktree add unrelated unrelated\n"
    assert "git worktree add" not in _recipe(creation_graph_target, source=source)


def test_creation_recipe_graph_requires_its_invoked_macro() -> None:
    source = re.sub(r"^define _agent-worktree-create\n.*?^endef\n", "",
                    _content(), flags=re.MULTILINE | re.DOTALL)
    with pytest.raises(AssertionError, match="Missing invoked"):
        _recipe("agent-worktree", source=source)


@pytest.mark.skipif(
    not MAKEFILE.exists(),
    reason="Makefile not present — cannot run end-to-end make targets",
)
class TestAgentWorktreeLifecycle:
    """End-to-end: actually create a worktree, then clean it up."""

    @pytest.fixture
    def lifecycle_repo(self, tmp_path: Path) -> WorktreeCreation:
        """Confine native source/tools/registry; run real owner registration."""
        return _creation_fixture(tmp_path)

    def test_agent_worktree_creates_isolated_checkout(
        self,
        lifecycle_repo: WorktreeCreation,
    ) -> None:
        repo, branch = lifecycle_repo.root, lifecycle_repo.branch
        result = lifecycle_repo.invoke("agent-worktree")
        assert result.returncode == 0, (
            f"agent-worktree failed (rc={result.returncode}):\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        # The target must print WORKTREE_PATH=<path>.
        assert "WORKTREE_PATH=" in result.stdout, (
            f"agent-worktree output missing WORKTREE_PATH= marker: {result.stdout!r}"
        )
        wt_path = next(
            line.split("=", 1)[1].strip()
            for line in result.stdout.splitlines()
            if line.startswith("WORKTREE_PATH=")
        )
        wt = Path(wt_path)
        # The worktree exists on disk.
        assert wt.exists() and wt.is_dir(), (
            f"worktree path {wt} was not created on disk"
        )
        # It is a valid git checkout with its own HEAD.
        head = subprocess.run(
            [lifecycle_repo.real_git, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(wt),
            env=lifecycle_repo.environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert head.returncode == 0 and head.stdout.strip() == branch, (
            f"worktree HEAD is not on branch {branch}: "
            f"rc={head.returncode} out={head.stdout!r}"
        )
        # The worktree is linked to the repo (shows up in `git worktree list`).
        listing = subprocess.run(
            [lifecycle_repo.real_git, "worktree", "list"],
            cwd=repo,
            env=lifecycle_repo.environment,
            capture_output=True,
            text=True,
            check=True,
        )
        assert str(wt) in listing.stdout, (
            f"worktree {wt} not registered in `git worktree list`:\n{listing.stdout}"
        )

    @pytest.mark.parametrize("environment_overrides", (False, True))
    def test_agent_cleanup_removes_worktree(
        self,
        checkout: tuple[Path, Path, Path],
        environment_overrides: bool,
    ) -> None:
        """Apply real retirement while retaining refs and original failure proof."""
        _assert_native_retirement(checkout, environment_overrides=environment_overrides)
