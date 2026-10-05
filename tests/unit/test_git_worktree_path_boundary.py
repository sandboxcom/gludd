"""Fail-closed path boundaries for Git worktree creation."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.security.state import STATE_DIR_ENV, project_state


def test_legacy_gludd_temp_root_is_canonically_bounded() -> None:
    """A supported Gludd temp root remains usable without widening temp."""
    root = Path(tempfile.gettempdir()) / f"gludd-worktree-{uuid.uuid4().hex}"

    assert GitAutomation._is_gludd_temp_worktree_path(str(root / "worktree"))


def test_raw_traversal_is_rejected_before_path_normalization(tmp_path: Path) -> None:
    """A lexical parent component cannot normalize into an allowed root."""
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(ValueError, match="traversal"):
        GitAutomation._reject_escaping_path(
            str(repo),
            str(repo / "worktrees" / ".." / "outside"),
        )


def test_symlink_escape_from_repo_parent_is_rejected(tmp_path: Path) -> None:
    """Canonical containment rejects a planted directory symlink."""
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path.parent / f"outside-{uuid.uuid4().hex}"
    outside.mkdir()
    link = tmp_path / "linked-worktrees"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes the repo parent"):
        GitAutomation._reject_escaping_path(str(repo), str(link / "worktree"))


def test_project_state_namespace_rejects_sibling_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the namespace derived from the requesting project is authorized."""
    state_base = tmp_path / "state"
    monkeypatch.setenv(STATE_DIR_ENV, str(state_base))
    repos = tmp_path / "repos"
    project_a = repos / "project-a"
    project_b = repos / "project-b"
    project_a.mkdir(parents=True)
    project_b.mkdir()
    worktrees_a = project_state(project_root=project_a).directory("worktrees")
    worktrees_b = project_state(project_root=project_b).directory("worktrees")

    GitAutomation._reject_escaping_path(
        str(project_a),
        str(worktrees_a / "authorized"),
    )
    with pytest.raises(ValueError, match="escapes the repo parent"):
        GitAutomation._reject_escaping_path(
            str(project_a),
            str(worktrees_b / "foreign"),
        )
