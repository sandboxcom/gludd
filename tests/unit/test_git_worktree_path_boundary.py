"""Fail-closed path boundaries for Git worktree creation."""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

from general_ludd.git_automation.repo import GitAutomation, _resolve_worktree_candidate
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


def test_symlink_loop_is_rejected_before_git_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolvable canonical path fails closed without invoking Git."""
    repo = tmp_path / "repo"
    repo.mkdir()
    loop = tmp_path / "loop"
    loop.symlink_to(loop, target_is_directory=True)
    automation = GitAutomation(str(repo))

    def unexpected_git_mutation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Git mutation must not run for an invalid path")

    monkeypatch.setattr(automation, "_run_git", unexpected_git_mutation)

    result = automation.create_worktree(
        str(repo),
        "feature/symlink-loop",
        str(loop / "worktree"),
    )

    assert result.success is False
    assert "canonical" in result.message


def test_uuid_temp_root_symlink_loop_is_not_authorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact compatibility name cannot authorize an unresolvable root."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    root = tmp_path / f"gludd-worktree-{uuid.uuid4().hex}"
    root.symlink_to(root, target_is_directory=True)

    assert not GitAutomation._is_gludd_temp_worktree_path(str(root / "worktree"))


def test_canonical_search_stops_at_filesystem_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing-ancestor search is bounded and fails closed at the root."""
    monkeypatch.setattr(os.path, "lexists", lambda _path: False)

    with pytest.raises(ValueError, match="unable to resolve canonical"):
        _resolve_worktree_candidate(Path("/missing/worktree"))


def test_relative_path_is_never_a_gludd_temp_authority() -> None:
    """Compatibility authorization requires an absolute canonical identity."""
    assert not GitAutomation._is_gludd_temp_worktree_path("relative/worktree")


def test_valid_confined_symlink_preserves_git_path_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical validation does not rewrite a valid path passed to Git."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target_root = tmp_path / "worktrees"
    target_root.mkdir()
    link = tmp_path / "worktrees-link"
    link.symlink_to(target_root, target_is_directory=True)
    requested = link / "feature"
    automation = GitAutomation(str(repo))
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def record_git(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(automation, "_run_git", record_git)

    result = automation.create_worktree(
        str(repo),
        "feature/confined-symlink",
        str(requested),
    )

    assert result.success is True
    assert calls == [
        (
            (
                "worktree",
                "add",
                "-b",
                "feature/confined-symlink",
                "--",
                str(requested),
                "HEAD",
            ),
            {"_cwd": str(repo)},
        )
    ]


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
