"""Security tests for the shared project-namespaced state allocator."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


def test_trusted_owned_file_nonexistent_returns_false(tmp_path: Path) -> None:
    from general_ludd.security.state import trusted_owned_file

    assert trusted_owned_file(tmp_path / "nonexistent.json") is False


def test_trusted_owned_file_directory_returns_false(tmp_path: Path) -> None:
    from general_ludd.security.state import trusted_owned_file

    dirpath = tmp_path / "adir"
    dirpath.mkdir()
    assert trusted_owned_file(dirpath) is False


def test_trusted_owned_file_empty_file_passes(tmp_path: Path) -> None:
    from general_ludd.security.state import trusted_owned_file

    empty = tmp_path / "empty.json"
    empty.write_text("")
    empty.chmod(0o644)
    assert trusted_owned_file(empty) is True
    assert stat.S_IMODE(empty.stat().st_mode) == 0o600


def test_project_state_default_prefix_when_no_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import DEFAULT_STATE_PREFIX, project_state

    monkeypatch.delenv("GLUDD_STATE_DIR", raising=False)
    state = project_state(project_root=_project(tmp_path, "project"))
    assert state.base_dir.name.startswith(DEFAULT_STATE_PREFIX)
    assert stat.S_IMODE(state.base_dir.stat().st_mode) == 0o700


def test_project_state_create_false_returns_existing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import project_state

    configured = tmp_path / "runtime"
    monkeypatch.setenv("GLUDD_STATE_DIR", str(configured))
    project_root = _project(tmp_path, "proj-create")
    state_a = project_state(project_root=project_root)
    state_b = project_state(project_root=project_root, create=False)
    assert state_a.project_dir == state_b.project_dir


def test_project_state_none_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import project_state

    configured = tmp_path / "state-none-root"
    configured.mkdir()
    monkeypatch.setenv("GLUDD_STATE_DIR", str(configured))
    state = project_state(project_root=None)
    assert state.base_dir == configured
    assert state.project_dir is not None


def test_secure_directory_rejects_dot_dot(tmp_path: Path) -> None:
    from general_ludd.security.state import SecureStateError, secure_directory

    with pytest.raises(SecureStateError, match="\\.\\."):
        secure_directory(tmp_path / "base" / ".." / "escaped")


def test_secure_directory_canonical_platform_temp(
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import secure_directory

    platform_tmp = Path(os.sep) / "tmp"
    target = platform_tmp / ".gludd-test-secure-dir"
    try:
        result = secure_directory(target)
        assert result == target
        assert target.exists()
        assert stat.S_IMODE(target.stat().st_mode) == 0o700
    finally:
        import shutil

        shutil.rmtree(target, ignore_errors=True)


def test_secure_write_text_canonical_platform_temp() -> None:
    """Write through macOS's trusted ``/tmp`` alias without weakening checks."""
    from general_ludd.security.state import secure_write_text

    with tempfile.TemporaryDirectory(dir=Path(os.sep) / "tmp") as directory:
        target = Path(directory) / "event.json"

        written = secure_write_text(target, "{}")

        assert written == target
        assert target.read_text(encoding="utf-8") == "{}"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_secure_directory_creates_intermediate_dirs(tmp_path: Path) -> None:
    from general_ludd.security.state import secure_directory

    nested = tmp_path / "a" / "b" / "c"
    result = secure_directory(nested)
    assert result == nested
    assert nested.exists()
    assert nested.is_dir()


def test_secure_write_text_rejects_relative_path() -> None:
    from general_ludd.security.state import SecureStateError, secure_write_text

    with pytest.raises(SecureStateError, match="absolute"):
        secure_write_text(Path("relative.txt"), "data")


def test_secure_write_text_unicode_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import project_state, secure_write_text

    monkeypatch.setenv("GLUDD_STATE_DIR", str(tmp_path / "state"))
    state = project_state(project_root=_project(tmp_path, "unicode-proj"))
    target = state.path("records", "unicode.json")
    content = '{"key": "\u00e9\u00fc\u00f1"}'
    secure_write_text(target, content)
    assert target.read_text() == content


def test_secure_write_text_overwrite_existing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import project_state, secure_write_text

    monkeypatch.setenv("GLUDD_STATE_DIR", str(tmp_path / "state"))
    state = project_state(project_root=_project(tmp_path, "overwrite-proj"))
    target = state.path("records", "data.json")
    secure_write_text(target, "first")
    secure_write_text(target, "second")
    assert target.read_text() == "second"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_secure_write_text_rejects_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import (
        SecureStateError,
        project_state,
    )

    monkeypatch.setenv("GLUDD_STATE_DIR", str(tmp_path / "state"))
    state = project_state(project_root=_project(tmp_path, "dir-proj"))
    target = state.path("records", "adir")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    with pytest.raises(SecureStateError, match="unable to open"):
        from general_ludd.security.state import secure_write_text

        secure_write_text(target, "should-fail")


def test_secure_write_text_closes_descriptor_for_non_regular_file(
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import SecureStateError, secure_write_text

    target = tmp_path / "not-regular.json"
    with (
        patch(
            "general_ludd.security.state.os.fstat",
            return_value=SimpleNamespace(st_mode=stat.S_IFDIR, st_uid=0),
        ),
        patch(
            "general_ludd.security.state.os.close",
            wraps=os.close,
        ) as close_mock,
        pytest.raises(SecureStateError, match="not a regular file"),
    ):
        secure_write_text(target, "data")

    close_mock.assert_called_once()


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX owner checks only")
def test_secure_write_text_closes_descriptor_for_foreign_owner(
    tmp_path: Path,
) -> None:
    from general_ludd.security.state import SecureStateError, secure_write_text

    target = tmp_path / "foreign-owner.json"
    foreign_uid = os.getuid() + 1
    with (
        patch(
            "general_ludd.security.state.os.fstat",
            return_value=SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=foreign_uid,
            ),
        ),
        patch(
            "general_ludd.security.state.os.close",
            wraps=os.close,
        ) as close_mock,
        pytest.raises(SecureStateError, match="not owned by caller"),
    ):
        secure_write_text(target, "data")

    close_mock.assert_called_once()
