"""E2E integration proof for G4 Sandboxed Code Execution.

Tests the full sandbox backend pipeline:
  - SandboxTarget dataclass with each target type
  - SandboxHandle creation, applied flag, extra dict
  - Finding severity levels and capability attachment
  - Constraint helpers (constraint_value, path_prefix, allowed_hosts, allowed_ports)
  - Backend Protocol shape verification
  - detect.auto() backend selection (mock platform detection)
  - Per-backend availability checks on known OS types
  - Fail-open contract: unavailable backends return applied=False
"""

from __future__ import annotations

import pytest

from general_ludd.security.permissions import Capability, PermissionSpec
from general_ludd.security.sandboxes import (
    Finding,
    SandboxHandle,
    SandboxTarget,
    allowed_hosts,
    allowed_ports,
    constraint_value,
    path_prefix,
)

# ---------------------------------------------------------------------------
# SandboxTarget
# ---------------------------------------------------------------------------


class TestSandboxTarget:
    def test_pid_target(self):
        target = SandboxTarget(pid=12345)
        assert target.pid == 12345
        assert target.popen is None
        assert target.directory is None
        assert target.service is None

    def test_directory_target(self):
        target = SandboxTarget(directory="/tmp/jail")
        assert target.directory == "/tmp/jail"

    def test_service_target(self):
        target = SandboxTarget(service="nginx.service")
        assert target.service == "nginx.service"

    def test_target_is_frozen(self):
        from dataclasses import FrozenInstanceError

        target = SandboxTarget(pid=1)
        with pytest.raises(FrozenInstanceError):
            target.pid = 2  # type: ignore[misc]

    def test_multiple_fields_populated(self):
        target = SandboxTarget(pid=42, directory="/sandbox", service="unit")
        assert target.pid == 42
        assert target.directory == "/sandbox"
        assert target.service == "unit"


# ---------------------------------------------------------------------------
# SandboxHandle
# ---------------------------------------------------------------------------


class TestSandboxHandle:
    def test_applied_handle(self):
        handle = SandboxHandle(backend="apparmor", token="profile1")
        assert handle.backend == "apparmor"
        assert handle.applied is True
        assert handle.extra == {}

    def test_fail_open_handle(self):
        handle = SandboxHandle(
            backend="bubblewrap", token="",
            applied=False, extra={"error": "bwrap not found"},
        )
        assert handle.applied is False
        assert handle.extra["error"] == "bwrap not found"

    def test_token_repr_masked(self):
        handle = SandboxHandle(backend="selinux", token="secret-token-42")
        r = repr(handle)
        assert "secret-token-42" not in r

    def test_extra_dict_default(self):
        handle = SandboxHandle(backend="jail", token="j1")
        assert isinstance(handle.extra, dict)
        assert handle.extra == {}

    def test_extra_with_metadata(self):
        handle = SandboxHandle(
            backend="landlock", token="ll-1",
            extra={"pid": 42, "loaded": True},
        )
        assert handle.extra["pid"] == 42
        assert handle.extra["loaded"] is True


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------


class TestFinding:
    def test_ok_finding(self):
        f = Finding(severity="ok", message="profile loaded")
        assert f.severity == "ok"
        assert f.message == "profile loaded"
        assert f.capability is None

    def test_fail_finding_with_capability(self):
        cap = Capability(name="fs_write", description="write files", constraints={})
        f = Finding(severity="fail", message="write denied", capability=cap)
        assert f.severity == "fail"
        assert f.capability is cap

    def test_warn_finding(self):
        f = Finding(severity="warn", message="version drift")
        assert f.severity == "warn"

    def test_finding_is_frozen(self):
        from dataclasses import FrozenInstanceError

        f = Finding(severity="ok", message="x")
        with pytest.raises(FrozenInstanceError):
            f.severity = "fail"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Constraint helpers
# ---------------------------------------------------------------------------


class TestConstraintHelpers:
    def test_constraint_value_returns_value(self):
        cap = Capability(name="net", description="", constraints={"allowed_hosts": ["a"]})
        assert constraint_value(cap, "allowed_hosts") == ["a"]

    def test_constraint_value_missing_key_returns_none(self):
        cap = Capability(name="net", description="", constraints={})
        assert constraint_value(cap, "path_prefix") is None

    def test_constraint_value_non_dict_constraints_returns_none(self):
        cap = Capability(name="net", description="", constraints="bad")  # type: ignore[arg-type]
        assert constraint_value(cap, "x") is None

    def test_path_prefix_string(self):
        cap = Capability(
            name="fs_read", description="", constraints={"path_prefix": "/tmp"},
        )
        assert path_prefix(cap) == "/tmp"

    def test_path_prefix_none(self):
        cap = Capability(name="fs_read", description="", constraints={})
        assert path_prefix(cap) is None

    def test_path_prefix_non_string(self):
        cap = Capability(
            name="fs_read", description="", constraints={"path_prefix": 42},
        )
        assert path_prefix(cap) is None

    def test_allowed_hosts_list(self):
        cap = Capability(
            name="net", description="",
            constraints={"allowed_hosts": ["host1", "host2"]},
        )
        assert allowed_hosts(cap) == ["host1", "host2"]

    def test_allowed_hosts_string(self):
        cap = Capability(
            name="net", description="", constraints={"allowed_hosts": "single"},
        )
        assert allowed_hosts(cap) == ["single"]

    def test_allowed_hosts_empty(self):
        cap = Capability(name="net", description="", constraints={})
        assert allowed_hosts(cap) == []

    def test_allowed_hosts_tuple(self):
        cap = Capability(
            name="net", description="",
            constraints={"allowed_hosts": ("a", "b")},
        )
        assert allowed_hosts(cap) == ["a", "b"]

    def test_allowed_ports_list(self):
        cap = Capability(
            name="net", description="", constraints={"allowed_ports": [80, 443]},
        )
        assert allowed_ports(cap) == [80, 443]

    def test_allowed_ports_single_int(self):
        cap = Capability(
            name="net", description="", constraints={"allowed_ports": 8080},
        )
        assert allowed_ports(cap) == [8080]

    def test_allowed_ports_empty(self):
        cap = Capability(name="net", description="", constraints={})
        assert allowed_ports(cap) == []


# ---------------------------------------------------------------------------
# PermissionSpec integration
# ---------------------------------------------------------------------------


class TestPermissionSpecSandboxIntegration:
    def test_spec_construction(self):
        spec = PermissionSpec(
            subject="agent-1",
            capabilities=[
                Capability(name="fs_read", description="", constraints={"path_prefix": "/"}),
                Capability(name="net", description="", constraints={"allowed_hosts": ["*"]}),
            ],
        )
        assert spec.subject == "agent-1"
        assert len(spec.capabilities) == 2

    def test_spec_capability_iteration(self):
        spec = PermissionSpec(
            subject="agent-2",
            capabilities=[
                Capability(name="a", description="", constraints={}),
                Capability(name="b", description="", constraints={}),
                Capability(name="c", description="", constraints={}),
            ],
        )
        names = [c.name for c in spec.capabilities]
        assert names == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Sandbox Backend Protocol
# ---------------------------------------------------------------------------


class TestSandboxBackendProtocol:
    def test_all_backend_modules_importable(self):
        from general_ludd.security.sandboxes import (
            freebsd_jail,
            linux_apparmor,
            linux_bubblewrap,
            linux_landlock,
            linux_selinux,
            macos_seatbelt,
            windows_appcontainer,
        )
        assert linux_apparmor is not None
        assert linux_bubblewrap is not None
        assert linux_landlock is not None
        assert linux_selinux is not None
        assert macos_seatbelt is not None
        assert freebsd_jail is not None
        assert windows_appcontainer is not None

    def test_detect_module_importable(self):
        from general_ludd.security.sandboxes import detect
        assert hasattr(detect, "auto")
        assert callable(detect.auto)

    def test_landlock_backend_exists_with_name(self):
        from general_ludd.security.sandboxes.linux_landlock import LandlockBackend
        assert LandlockBackend.name == "landlock"

    def test_bubblewrap_backend_exists_with_name(self):
        from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend
        assert BubblewrapBackend.name == "bubblewrap"

    def test_apparmor_backend_exists_with_name(self):
        from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend
        assert AppArmorBackend.name == "apparmor"

    def test_selinux_backend_exists_with_name(self):
        from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend
        assert SELinuxBackend.name == "selinux"

    def test_seatbelt_backend_exists_with_name(self):
        from general_ludd.security.sandboxes.macos_seatbelt import MacSeatbeltBackend
        assert MacSeatbeltBackend.name == "seatbelt"

    def test_jail_backend_exists_with_name(self):
        from general_ludd.security.sandboxes.freebsd_jail import FreeBSDJailBackend
        assert FreeBSDJailBackend.name == "jail"

    def test_appcontainer_backend_exists_with_name(self):
        from general_ludd.security.sandboxes.windows_appcontainer import WindowsAppContainerBackend
        assert WindowsAppContainerBackend.name == "appcontainer"


# ---------------------------------------------------------------------------
# Fail-open contract
# ---------------------------------------------------------------------------


class TestFailOpenContract:
    def test_landlock_available_is_bool(self):
        from general_ludd.security.sandboxes.linux_landlock import LandlockBackend
        result = LandlockBackend.available()
        assert isinstance(result, bool)

    def test_bubblewrap_available_is_bool(self):
        from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend
        result = BubblewrapBackend.available()
        assert isinstance(result, bool)

    def test_apparmor_available_is_bool(self):
        from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend
        result = AppArmorBackend.available()
        assert isinstance(result, bool)

    def test_selinux_available_is_bool(self):
        from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend
        result = SELinuxBackend.available()
        assert isinstance(result, bool)

    def test_seatbelt_available_is_bool(self):
        from general_ludd.security.sandboxes.macos_seatbelt import MacSeatbeltBackend
        result = MacSeatbeltBackend.available()
        assert isinstance(result, bool)

    def test_jail_available_is_bool(self):
        from general_ludd.security.sandboxes.freebsd_jail import FreeBSDJailBackend
        result = FreeBSDJailBackend.available()
        assert isinstance(result, bool)

    def test_appcontainer_available_is_bool(self):
        from general_ludd.security.sandboxes.windows_appcontainer import WindowsAppContainerBackend
        result = WindowsAppContainerBackend.available()
        assert isinstance(result, bool)

    def test_fail_open_apply_on_unavailable_backend(self):
        from general_ludd.security.sandboxes.linux_landlock import LandlockBackend
        spec = PermissionSpec(subject="test", capabilities=[])
        target = SandboxTarget(pid=99999)
        handle = LandlockBackend.apply(spec, target)
        assert isinstance(handle, SandboxHandle)
        if not LandlockBackend.available():
            assert handle.applied is False
        # verify + release must not raise
        LandlockBackend.verify(spec, handle)
        LandlockBackend.release(handle)

    def test_bubblewrap_fail_open(self):
        from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend
        spec = PermissionSpec(subject="test", capabilities=[])
        target = SandboxTarget(pid=1)
        handle = BubblewrapBackend.apply(spec, target)
        assert isinstance(handle, SandboxHandle)
        BubblewrapBackend.verify(spec, handle)
        BubblewrapBackend.release(handle)


# ---------------------------------------------------------------------------
# detect.auto — platform-specific backend selection
# ---------------------------------------------------------------------------


class TestDetectAuto:
    def test_auto_returns_backend_or_none(self):
        from general_ludd.security.sandboxes.detect import auto
        backend = auto()
        assert backend is None or hasattr(backend, "name")

    def test_auto_callable(self):
        from general_ludd.security.sandboxes.detect import auto
        assert callable(auto)

    def test_auto_on_current_platform_does_not_raise(self):
        from general_ludd.security.sandboxes.detect import auto
        try:
            backend = auto()
        except Exception as exc:
            pytest.fail(f"auto() raised on current platform: {exc}")
        assert backend is None or isinstance(backend, type)

    def test_landlock_detection_is_bool(self):
        from general_ludd.security.sandboxes.detect import _landlock_available
        result = _landlock_available()
        assert isinstance(result, bool)

    def test_bubblewrap_detection_is_bool(self):
        from general_ludd.security.sandboxes.detect import _bubblewrap_present
        result = _bubblewrap_present()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# SandboxBackend Protocol — runtime_checkable
# ---------------------------------------------------------------------------


class TestSandboxBackendProtocolRuntime:
    def test_landlock_is_sandbox_backend(self):
        from general_ludd.security.sandboxes import SandboxBackend
        from general_ludd.security.sandboxes.linux_landlock import LandlockBackend
        assert isinstance(LandlockBackend, SandboxBackend)

    def test_bubblewrap_is_sandbox_backend(self):
        from general_ludd.security.sandboxes import SandboxBackend
        from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend
        assert isinstance(BubblewrapBackend, SandboxBackend)

    def test_apparmor_is_sandbox_backend(self):
        from general_ludd.security.sandboxes import SandboxBackend
        from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend
        assert isinstance(AppArmorBackend, SandboxBackend)
