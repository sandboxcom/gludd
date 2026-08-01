"""Backend lifecycle coverage for secure sandbox runtime state."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from general_ludd.security.sandboxes import PermissionSpec, SandboxTarget
from general_ludd.security.sandboxes.state import SandboxState, safe_state_component


@pytest.fixture
def state_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SandboxState:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".gludd").mkdir()
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(project))
    return SandboxState.discover(project_root=project)


@pytest.fixture
def spec() -> PermissionSpec:
    return PermissionSpec(agent_type="worker")


def test_selinux_success_releases_private_build_state(
    state_env: SandboxState,
    spec: PermissionSpec,
) -> None:
    from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend

    completed = mock.Mock(stdout=b"gludd_worker\ngludd_worker_t", returncode=0)
    with mock.patch("subprocess.run", return_value=completed):
        handle = SELinuxBackend.apply(spec, SandboxTarget())
        state_path = Path(str(handle.extra["state_path"]))
        assert handle.applied is True
        assert state_path.is_dir()
        assert all(path.stat().st_mode & 0o077 == 0 for path in state_path.iterdir())
        findings = SELinuxBackend.verify(spec, handle)
        assert any(finding.severity == "ok" for finding in findings)
        SELinuxBackend.release(handle)

    assert not state_path.exists()


def test_selinux_partial_failure_cleans_state_immediately(
    state_env: SandboxState,
    spec: PermissionSpec,
) -> None:
    from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend

    expected = state_env.path("selinux", "gludd_worker")
    with mock.patch("subprocess.run", side_effect=OSError("compiler failed")):
        handle = SELinuxBackend.apply(spec, SandboxTarget())

    assert handle.applied is False
    assert not expected.exists()


def test_seatbelt_success_releases_private_profile(
    state_env: SandboxState,
    spec: PermissionSpec,
) -> None:
    from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend

    with (
        mock.patch(
            "general_ludd.security.sandboxes.macos_seatbelt._is_deprecated_host",
            return_value=False,
        ),
        mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)),
    ):
        handle = SeatbeltBackend.apply(spec, SandboxTarget())
        profile = Path(str(handle.extra["path"]))
        assert handle.applied is True
        assert profile.stat().st_mode & 0o777 == 0o600
        findings = SeatbeltBackend.verify(spec, handle)
        assert [finding.severity for finding in findings] == ["ok"]
        SeatbeltBackend.release(handle)

    assert not profile.exists()


def test_seatbelt_compile_failure_cleans_profile_immediately(
    state_env: SandboxState,
    spec: PermissionSpec,
) -> None:
    from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend

    with (
        mock.patch(
            "general_ludd.security.sandboxes.macos_seatbelt._is_deprecated_host",
            return_value=False,
        ),
        mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)),
    ):
        handle = SeatbeltBackend.apply(spec, SandboxTarget())

    assert handle.applied is False
    assert not Path(str(handle.extra["path"])).exists()


def test_seatbelt_verify_reports_missing_profile(spec: PermissionSpec) -> None:
    from general_ludd.security.sandboxes import SandboxHandle
    from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend

    handle = SandboxHandle(
        backend="seatbelt",
        token="worker",
        applied=True,
        extra={"path": "/definitely/missing/profile.sb"},
    )
    findings = SeatbeltBackend.verify(spec, handle)

    assert [finding.severity for finding in findings] == ["fail"]


def test_jail_success_releases_private_fallback_root(
    state_env: SandboxState,
    spec: PermissionSpec,
) -> None:
    from general_ludd.security.sandboxes.freebsd_jail import JailBackend

    jail_path = state_env.directory("jail", safe_state_component(spec.agent_type))
    completed = mock.Mock(stdout=b"gludd-worker", returncode=0)
    with (
        mock.patch.object(Path, "open", side_effect=PermissionError),
        mock.patch.object(Path, "mkdir", return_value=None),
        mock.patch.object(Path, "write_text", return_value=1),
        mock.patch("subprocess.run", return_value=completed),
    ):
        handle = JailBackend.apply(spec, SandboxTarget())
        assert handle.applied is True
        findings = JailBackend.verify(spec, handle)
        assert [finding.severity for finding in findings] == ["ok"]
        JailBackend.release(handle)

    assert not jail_path.exists()


def test_jail_partial_failure_cleans_state_immediately(
    state_env: SandboxState,
    spec: PermissionSpec,
) -> None:
    from general_ludd.security.sandboxes.freebsd_jail import JailBackend

    jail_path = state_env.directory("jail", safe_state_component(spec.agent_type))
    with (
        mock.patch.object(Path, "open", side_effect=PermissionError),
        mock.patch.object(Path, "mkdir", return_value=None),
        mock.patch.object(Path, "write_text", return_value=1),
        mock.patch("subprocess.run", side_effect=OSError("jail failed")),
    ):
        handle = JailBackend.apply(spec, SandboxTarget())

    assert handle.applied is False
    assert not jail_path.exists()


def test_jail_verify_reports_missing_jail(spec: PermissionSpec) -> None:
    from general_ludd.security.sandboxes import SandboxHandle
    from general_ludd.security.sandboxes.freebsd_jail import JailBackend

    completed = mock.Mock(stdout=b"", returncode=0)
    handle = SandboxHandle(backend="jail", token="missing", applied=True)
    with mock.patch("subprocess.run", return_value=completed):
        findings = JailBackend.verify(spec, handle)

    assert [finding.severity for finding in findings] == ["fail"]


def test_jail_verify_surfaces_command_failure(spec: PermissionSpec) -> None:
    from general_ludd.security.sandboxes import SandboxHandle
    from general_ludd.security.sandboxes.freebsd_jail import JailBackend

    handle = SandboxHandle(backend="jail", token="worker", applied=True)
    with mock.patch("subprocess.run", side_effect=subprocess.SubprocessError("jls")):
        findings = JailBackend.verify(spec, handle)

    assert [finding.severity for finding in findings] == ["fail"]


def test_explicit_jail_root_does_not_allocate_managed_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    spec: PermissionSpec,
) -> None:
    from general_ludd.security.sandboxes.freebsd_jail import JailBackend

    project = tmp_path / "project"
    project.mkdir()
    (project / ".gludd").mkdir()
    base = tmp_path / "state"
    jail_root = tmp_path / "explicit-jail"
    jail_root.mkdir()
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(project))
    original_mkdir = Path.mkdir
    original_write_text = Path.write_text

    def selective_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        if str(path).startswith("/var/db/gludd"):
            return
        original_mkdir(path, *args, **kwargs)

    def selective_write(path: Path, data: str, *args: object, **kwargs: object) -> int:
        if str(path).startswith("/var/db/gludd"):
            return len(data)
        return original_write_text(path, data, *args, **kwargs)

    with (
        mock.patch.object(Path, "open", side_effect=PermissionError),
        mock.patch.object(Path, "mkdir", autospec=True, side_effect=selective_mkdir),
        mock.patch.object(
            Path,
            "write_text",
            autospec=True,
            side_effect=selective_write,
        ),
        mock.patch(
            "subprocess.run",
            return_value=mock.Mock(stdout=b"gludd-worker", returncode=0),
        ),
    ):
        handle = JailBackend.apply(
            spec,
            SandboxTarget(directory=str(jail_root)),
        )

    assert handle.applied is True
    assert not base.exists()
    assert jail_root.exists()
