"""Security tests for the shared project-namespaced state allocator."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


def _project(root: Path, name: str) -> Path:
    project = root / name
    project.mkdir()
    return project


def test_project_state_is_configurable_owner_only_and_namespaced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import project_state

    configured = tmp_path / "runtime"
    monkeypatch.setenv("GLUDD_STATE_DIR", str(configured))
    alpha = project_state(project_root=_project(tmp_path, "alpha"))
    beta = project_state(project_root=_project(tmp_path, "beta"))

    assert alpha.base_dir == configured
    assert alpha.project_dir != beta.project_dir
    assert alpha.project_dir.parent == configured
    assert stat.S_IMODE(configured.stat().st_mode) == 0o700
    assert stat.S_IMODE(alpha.project_dir.stat().st_mode) == 0o700


def test_project_state_rejects_symlinked_configured_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import SecureStateError, project_state

    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    monkeypatch.setenv("GLUDD_STATE_DIR", str(linked))

    with pytest.raises(SecureStateError, match="symlink"):
        project_state(project_root=_project(tmp_path, "project"))


def test_secure_write_is_mode_0600_and_refuses_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import (
        SecureStateError,
        project_state,
        secure_write_text,
    )

    monkeypatch.setenv("GLUDD_STATE_DIR", str(tmp_path / "state"))
    state = project_state(project_root=_project(tmp_path, "project"))
    target = state.path("records", "event.json")
    secure_write_text(target, "{}")

    assert target.read_text() == "{}"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    target.unlink()
    outside = tmp_path / "outside"
    outside.write_text("do-not-touch")
    target.symlink_to(outside)
    with pytest.raises(SecureStateError, match="symlink"):
        secure_write_text(target, "changed")
    assert outside.read_text() == "do-not-touch"


def test_temporary_directory_cleanup_is_exactly_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import project_state

    monkeypatch.setenv("GLUDD_STATE_DIR", str(tmp_path / "state"))
    state = project_state(project_root=_project(tmp_path, "project"))
    first = state.temporary_directory("jobs", prefix="first-")
    second = state.temporary_directory("jobs", prefix="second-")
    (first / "owned.txt").write_text("first")
    (second / "owned.txt").write_text("second")

    assert state.cleanup_path(first) is True
    assert not first.exists()
    assert second.exists()
    assert stat.S_IMODE(second.stat().st_mode) == 0o700


def test_secure_external_directory_rejects_relative_and_symlink_paths(
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import SecureStateError, secure_directory

    with pytest.raises(SecureStateError, match="absolute"):
        secure_directory(Path("relative-state"))

    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(SecureStateError, match="symlink"):
        secure_directory(linked)


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX owner checks only")
def test_secure_write_preserves_current_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import project_state, secure_write_text

    monkeypatch.setenv("GLUDD_STATE_DIR", str(tmp_path / "state"))
    state = project_state(project_root=_project(tmp_path, "project"))
    target = state.path("records", "owner.json")
    secure_write_text(target, "{}")

    assert target.stat().st_uid == os.getuid()


def test_trusted_owned_file_hardens_legacy_file_and_rejects_symlink(
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import trusted_owned_file

    legacy = tmp_path / "legacy.json"
    legacy.write_text("{}")
    legacy.chmod(0o644)
    assert trusted_owned_file(legacy) is True
    assert stat.S_IMODE(legacy.stat().st_mode) == 0o600

    link = tmp_path / "legacy-link.json"
    link.symlink_to(legacy)
    assert trusted_owned_file(link) is False
