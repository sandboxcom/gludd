"""Security contract for namespaced host-side sandbox runtime state."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from general_ludd.security.sandboxes import PermissionSpec, SandboxTarget


def _project(path: Path, name: str) -> Path:
    project = path / name
    project.mkdir()
    (project / ".gludd").mkdir()
    return project


def test_state_root_honours_override_and_isolates_projects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import SandboxState

    base = tmp_path / "operator-state"
    alpha = _project(tmp_path, "alpha")
    beta = _project(tmp_path, "beta")
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))

    first = SandboxState.discover(project_root=alpha)
    repeated = SandboxState.discover(project_root=alpha)
    other = SandboxState.discover(project_root=beta)

    assert first.base_dir == base
    assert first.project_dir == repeated.project_dir
    assert first.namespace == repeated.namespace
    assert first.project_dir != other.project_dir
    assert first.project_dir.parent == base
    assert other.project_dir.parent == base


def test_state_directories_are_owner_only_and_owned_by_caller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import SandboxState

    base = tmp_path / "state"
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    state = SandboxState.discover(project_root=_project(tmp_path, "project"))
    backend = state.directory("gvisor", "run-01")

    for directory in (base, state.project_dir, backend.parent, backend):
        info = directory.stat()
        assert stat.S_IMODE(info.st_mode) == 0o700
        if hasattr(os, "getuid"):
            assert info.st_uid == os.getuid()


def test_configured_root_rejects_relative_paths_and_symlinks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import (
        SandboxState,
        SandboxStateError,
    )

    project = _project(tmp_path, "project")
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", "relative/state")
    with pytest.raises(SandboxStateError, match="absolute"):
        SandboxState.discover(project_root=project)

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked-state"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(link))
    with pytest.raises(SandboxStateError, match="symlink"):
        SandboxState.discover(project_root=project)


def test_read_only_discovery_rejects_insecure_existing_namespace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import (
        SandboxState,
        SandboxStateError,
    )

    base = tmp_path / "state"
    project = _project(tmp_path, "project")
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    state = SandboxState.discover(project_root=project)
    assert state.cleanup_project() is True

    outside = tmp_path / "outside"
    outside.mkdir()
    state.project_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SandboxStateError, match="symlink"):
        SandboxState.discover(project_root=project, create=False)


def test_read_only_discovery_rejects_non_private_base(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import (
        SandboxState,
        SandboxStateError,
    )

    base = tmp_path / "state"
    base.mkdir(mode=0o700)
    base.chmod(0o755)
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))

    with pytest.raises(SandboxStateError, match="mode 0700"):
        SandboxState.discover(
            project_root=_project(tmp_path, "project"),
            create=False,
        )


def test_default_root_is_uid_scoped_and_canonical(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import SandboxState

    monkeypatch.delenv("GLUDD_SANDBOX_STATE_DIR", raising=False)
    state = SandboxState.discover(project_root=_project(tmp_path, "project"))

    assert state.base_dir.is_absolute()
    assert state.base_dir.name.startswith("gludd-sandbox-state-")
    assert not state.base_dir.is_symlink()
    state.cleanup_project()


def test_noncreating_discovery_and_leaf_cleanup_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import (
        SandboxState,
        SandboxStateError,
    )

    base = tmp_path / "state"
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    state = SandboxState.discover(
        project_root=_project(tmp_path, "project"),
        create=False,
    )

    assert not base.exists()
    assert state.path() == state.project_dir
    assert state.directory() == state.project_dir
    leaf = state.path("marker")
    leaf.write_text("private")
    assert state.cleanup_path(leaf) is True
    assert state.cleanup_path(leaf) is False
    with pytest.raises(SandboxStateError, match="cleanup_project"):
        state.cleanup_path(state.project_dir)


def test_existing_file_cannot_be_used_as_state_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import (
        SandboxState,
        SandboxStateError,
    )

    base = tmp_path / "state"
    base.write_text("not a directory")
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    project = _project(tmp_path, "project")

    with pytest.raises(SandboxStateError, match="not a directory"):
        SandboxState.discover(project_root=project, create=False)
    with pytest.raises(SandboxStateError, match="not a directory"):
        SandboxState.discover(project_root=project)


def test_invalid_identifiers_and_project_roots_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import (
        SandboxState,
        SandboxStateError,
        safe_state_component,
    )

    assert safe_state_component("").startswith("item-")
    monkeypatch.setenv(
        "GLUDD_SANDBOX_STATE_DIR",
        str(tmp_path / "state" / ".." / "other"),
    )
    with pytest.raises(SandboxStateError, match="must not contain"):
        SandboxState.discover(project_root=_project(tmp_path, "project"))

    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(SandboxStateError, match="unavailable"):
        SandboxState.discover(project_root=tmp_path / "missing")

    project = _project(tmp_path, "relative")
    monkeypatch.chdir(tmp_path)
    relative = SandboxState.discover(project_root=Path(project.name))
    assert relative.project_root == project.resolve()


def test_cleanup_rejects_symlink_nested_inside_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import (
        SandboxState,
        SandboxStateError,
    )

    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(tmp_path / "state"))
    state = SandboxState.discover(project_root=_project(tmp_path, "project"))
    nested = state.directory("gvisor", "run")
    outside = tmp_path / "outside"
    outside.write_text("preserve")
    (nested / "linked").symlink_to(outside)

    with pytest.raises(SandboxStateError, match="symlink"):
        state.cleanup_backend("gvisor")
    assert outside.read_text() == "preserve"


def test_state_rejects_wrong_owner_before_changing_permissions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import general_ludd.security.sandboxes.state as state_module

    base = tmp_path / "state"
    base.mkdir(mode=0o777)
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    monkeypatch.setattr(state_module, "_current_uid", lambda: base.stat().st_uid + 1)

    with pytest.raises(state_module.SandboxStateError, match="owned"):
        state_module.SandboxState.discover(
            project_root=_project(tmp_path, "project"),
        )

    assert stat.S_IMODE(base.stat().st_mode) != 0o700


def test_canonical_containment_and_symlink_cleanup_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import (
        SandboxState,
        SandboxStateError,
    )

    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(tmp_path / "state"))
    state = SandboxState.discover(project_root=_project(tmp_path, "project"))

    for unsafe in ("..", ".", "../escape", "/absolute"):
        with pytest.raises(SandboxStateError):
            state.path("gvisor", unsafe)

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(SandboxStateError, match="outside"):
        state.cleanup_path(outside)

    backend = state.directory("gvisor")
    linked = backend / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SandboxStateError, match="symlink"):
        state.cleanup_path(linked)
    assert outside.exists()


def test_cleanup_is_scoped_deterministic_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.state import SandboxState

    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(tmp_path / "state"))
    state = SandboxState.discover(project_root=_project(tmp_path, "project"))
    gvisor = state.directory("gvisor", "run-01")
    firecracker = state.directory("firecracker", "run-02")
    (gvisor / "state.json").write_text("{}")
    (firecracker / "api.sock").write_text("")

    assert state.cleanup_backend("gvisor") is True
    assert state.cleanup_backend("gvisor") is False
    assert not gvisor.exists()
    assert firecracker.exists()
    assert state.cleanup_project() is True
    assert state.cleanup_project() is False
    assert not state.project_dir.exists()
    assert state.base_dir.exists()


def test_host_backends_resolve_state_below_project_namespace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.security.sandboxes.freebsd_jail import _jail_path
    from general_ludd.security.sandboxes.linux_selinux import _build_dir, _fc_for
    from general_ludd.security.sandboxes.macos_seatbelt import _profile_path
    from general_ludd.security.sandboxes.state import SandboxState
    from general_ludd.security.sandboxes.vm.firecracker_backend import _socket_paths
    from general_ludd.security.sandboxes.vm.gvisor_backend import _runsc_root

    base = tmp_path / "state"
    project = _project(tmp_path, "project")
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(project))
    state = SandboxState.discover(project_root=project)
    spec = PermissionSpec(agent_type="../../unsafe agent")

    paths = (
        Path(_jail_path(spec, SandboxTarget())),
        _build_dir(),
        _profile_path(spec),
        _runsc_root("gludd-sb-abc123"),
        *map(Path, _socket_paths("gludd-fc-abc123")),
    )
    for path in paths:
        assert path.resolve(strict=False).is_relative_to(state.project_dir)
        assert ".." not in path.parts

    assert str(state.project_dir) in _fc_for(spec)


def test_guest_tmp_mount_semantics_remain_ephemeral() -> None:
    from general_ludd.security.sandboxes.linux_bubblewrap import render_argv

    spec = PermissionSpec(agent_type="test-agent")
    command = render_argv(spec, SandboxTarget(), ["/bin/true"])

    start = command.index("--dir")
    assert command[start : start + 2] == ["--dir", "/tmp"]
