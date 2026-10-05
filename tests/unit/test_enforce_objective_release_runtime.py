"""Runtime release-state proof for the objective enforcement plugin."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts.test_hook_runtime import _factory_plugin_code, _run_ts


def _git(project: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.fixture
def unpublished_release_repo(tmp_path: Path) -> Path:
    """Return a repo with one unpushed commit and an untagged stable version."""
    remote = tmp_path / "remote.git"
    project = tmp_path / "project"
    remote.mkdir()
    project.mkdir()
    _git(remote, "init", "--bare")
    _git(project, "init")
    _git(project, "config", "user.name", "Gludd Test")
    _git(project, "config", "user.email", "gludd-test@example.invalid")
    (project / "pyproject.toml").write_text('[project]\nversion = "0.1.1"\n')
    (project / "SESSION.md").write_text("## PRIMARY OBJECTIVE: PUBLISH RELEASE ARTIFACTS\n")
    _git(project, "add", "pyproject.toml", "SESSION.md")
    _git(project, "commit", "-m", "base")
    _git(project, "branch", "-M", "development")
    _git(project, "remote", "add", "origin", str(remote))
    _git(project, "push", "-u", "origin", "development")
    (project / "candidate.txt").write_text("candidate\n")
    _git(project, "add", "candidate.txt")
    _git(project, "commit", "-m", "candidate")
    return project


def _dispatch_result(project: Path, tmp_path: Path):
    code = _factory_plugin_code(
        "enforce-objective.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'task', args: {}}, {})",
    )
    return _run_ts(
        code,
        cwd=project,
        env_override={
            "GLUDD_PROJECT_ROOT": str(project),
            "GLUDD_OBJECTIVE_ENFORCE": "1",
            "GLUDD_SPEC_VELOCITY_FILE": str(tmp_path / "spec-velocity.json"),
            "GLUDD_SPEC_BEHAVIOR_FILE": str(tmp_path / "spec-behavior.json"),
            "GLUDD_OBJECTIVE_STACK_FILE": str(tmp_path / "objective-stack.json"),
        },
    )


def _assert_release_denial(result: object) -> None:
    assert isinstance(result, dict)
    assert result.get("permissionDecision") == "deny"
    assert "release 0.1.1 is pending" in str(result.get("message", ""))


def test_untagged_stable_release_blocks_dispatch(
    unpublished_release_repo: Path,
    tmp_path: Path,
) -> None:
    _assert_release_denial(_dispatch_result(unpublished_release_repo, tmp_path))


def test_lightweight_tag_does_not_complete_release(
    unpublished_release_repo: Path,
    tmp_path: Path,
) -> None:
    _git(unpublished_release_repo, "tag", "v0.1.1")
    _assert_release_denial(_dispatch_result(unpublished_release_repo, tmp_path))


def test_annotated_tag_on_candidate_completes_local_release_state(
    unpublished_release_repo: Path,
    tmp_path: Path,
) -> None:
    _git(unpublished_release_repo, "tag", "-a", "v0.1.1", "-m", "release v0.1.1")
    assert _dispatch_result(unpublished_release_repo, tmp_path) is None


def test_release_tag_must_resolve_to_current_candidate(
    unpublished_release_repo: Path,
    tmp_path: Path,
) -> None:
    _git(unpublished_release_repo, "tag", "-a", "v0.1.1", "-m", "release v0.1.1")
    (unpublished_release_repo / "after-tag.txt").write_text("drift\n")
    _git(unpublished_release_repo, "add", "after-tag.txt")
    _git(unpublished_release_repo, "commit", "-m", "post-tag drift")
    _assert_release_denial(_dispatch_result(unpublished_release_repo, tmp_path))
