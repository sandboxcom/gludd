"""Unit tests for VM sandbox backends — Firecracker and gVisor P1 stubs.

Phase P1: import + available() + fail-open + Protocol compliance checks.
Real boot/kill tests land in P2 when the Firecracker REST API is wired.
"""

from __future__ import annotations

from unittest import mock

import pytest

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import (
    SandboxHandle,
    SandboxTarget,
)


def test_vm_package_exports_all_names():
    import general_ludd.security.sandboxes.vm as vm_mod

    for name in (
        "AgentExecutor",
        "BuiltImage",
        "FirecrackerBackend",
        "GvisorBackend",
        "ImageManifest",
        "build_firecracker_image",
        "build_gvisor_image",
        "build_rootfs",
        "cleanup_cache",
        "get_image_path",
        "image_exists",
        "list_cached_images",
        "verify_image",
    ):
        assert hasattr(vm_mod, name), f"vm.__init__ missing re-export for {name}"


def test_firecracker_backend_protocol_shape():
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    assert FirecrackerBackend.name == "firecracker"
    for attr in ("available", "apply", "verify", "release"):
        assert hasattr(FirecrackerBackend, attr), (
            f"FirecrackerBackend missing {attr}"
        )


def test_firecracker_available_checks_kvm_and_binary():
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    with mock.patch("os.path.exists", return_value=True), \
         mock.patch("os.access", return_value=True), \
         mock.patch("shutil.which", return_value="/usr/bin/firecracker"):
        assert FirecrackerBackend.available() is True

    with mock.patch("os.path.exists", return_value=False), \
         mock.patch("shutil.which", return_value="/usr/bin/firecracker"):
        assert FirecrackerBackend.available() is False

    with mock.patch("os.path.exists", return_value=True), \
         mock.patch("os.access", return_value=True), \
         mock.patch("shutil.which", return_value=None):
        assert FirecrackerBackend.available() is False


def test_firecracker_apply_stub(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    with mock.patch.object(
        FirecrackerBackend, "available", return_value=True,
    ):
        handle = FirecrackerBackend.apply(sample_spec, sample_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "firecracker"
    assert handle.applied is True
    assert handle.extra.get("stub") is True


def test_firecracker_apply_fails_open(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    with mock.patch.object(
        FirecrackerBackend, "available", return_value=False,
    ):
        handle = FirecrackerBackend.apply(sample_spec, sample_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.applied is False
    assert "reason" in handle.extra


def test_firecracker_verify_when_not_applied(sample_spec):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    handle = SandboxHandle(
        backend="firecracker", token="gludd-test", applied=False,
        extra={"reason": "no /dev/kvm"},
    )
    findings = FirecrackerBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


def test_firecracker_verify_stub_warning(sample_spec):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    handle = SandboxHandle(
        backend="firecracker", token="gludd-test", applied=True,
        extra={"stub": True},
    )
    findings = FirecrackerBackend.verify(sample_spec, handle)
    assert any(f.severity == "warn" for f in findings)


def test_firecracker_release_noop_on_stub():
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    handle = SandboxHandle(
        backend="firecracker", token="gludd-test", applied=True,
        extra={"stub": True},
    )
    FirecrackerBackend.release(handle)


def test_gvisor_backend_protocol_shape():
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    assert GvisorBackend.name == "gvisor"
    for attr in ("available", "apply", "verify", "release"):
        assert hasattr(GvisorBackend, attr), f"GvisorBackend missing {attr}"


def test_gvisor_available_checks_runsc():
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    with mock.patch("shutil.which", return_value="/usr/bin/runsc"):
        assert GvisorBackend.available() is True

    with mock.patch("shutil.which", return_value=None):
        assert GvisorBackend.available() is False


def test_gvisor_apply_stub(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    with mock.patch.object(GvisorBackend, "available", return_value=True):
        handle = GvisorBackend.apply(sample_spec, sample_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "gvisor"
    assert handle.applied is True
    assert handle.extra.get("stub") is True


def test_gvisor_apply_fails_open(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    with mock.patch.object(GvisorBackend, "available", return_value=False):
        handle = GvisorBackend.apply(sample_spec, sample_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.applied is False
    assert "reason" in handle.extra


def test_gvisor_verify_when_not_applied(sample_spec):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    handle = SandboxHandle(
        backend="gvisor", token="gludd-test", applied=False,
        extra={"reason": "no runsc"},
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


def test_gvisor_verify_stub_warning(sample_spec):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    handle = SandboxHandle(
        backend="gvisor", token="gludd-test", applied=True,
        extra={"stub": True},
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert any(f.severity == "warn" for f in findings)


def test_gvisor_release_noop_on_stub():
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    handle = SandboxHandle(
        backend="gvisor", token="gludd-test", applied=True,
        extra={"stub": True},
    )
    GvisorBackend.release(handle)


def test_image_builder_build_rootfs_stub(tmp_path):
    from general_ludd.security.sandboxes.vm.image_builder import build_rootfs

    result = build_rootfs(tmp_path / "rootfs.ext4")
    assert result.path == tmp_path / "rootfs.ext4"


def test_image_builder_verify_image_missing():
    from general_ludd.security.sandboxes.vm.image_builder import verify_image

    assert verify_image("/nonexistent/path/rootfs.ext4") is False


def test_image_builder_verify_image_exists(tmp_path):
    from general_ludd.security.sandboxes.vm.image_builder import verify_image

    img = tmp_path / "rootfs.ext4"
    data = bytearray(2048)
    data[1024 + 0x38:1024 + 0x3A] = b"\x53\xef"
    img.write_bytes(bytes(data))
    assert verify_image(img) is True


def test_agent_executor_receive_and_execute_stub(sample_target):
    from general_ludd.security.sandboxes.vm.agent_executor import AgentExecutor

    result = AgentExecutor.receive_and_execute(sample_target)
    assert result == {
        "exit_code": 0,
        "stdout": b"",
        "stderr": b"",
        "wall_time_s": 0.0,
        "stub": True,
    }


def test_vm_backends_in_auto_detection_chain():
    from general_ludd.security.sandboxes import detect

    with mock.patch.object(detect.sys, "platform", "linux"), \
         mock.patch("os.path.exists", return_value=True), \
         mock.patch("os.access", return_value=True), \
         mock.patch("shutil.which", return_value="/usr/bin/firecracker"):
        from general_ludd.security.sandboxes.vm.firecracker_backend import (
            FirecrackerBackend,
        )
        assert detect.auto() is FirecrackerBackend


def test_vm_backends_skipped_on_import_error():
    from general_ludd.security.sandboxes import detect

    with mock.patch.object(detect.sys, "platform", "linux"), \
         mock.patch.object(detect, "_landlock_available", return_value=False), \
         mock.patch.object(detect, "_bubblewrap_present", return_value=False), \
         mock.patch.object(detect, "_apparmor_enabled", return_value=False), \
         mock.patch.object(detect, "_selinux_enabled", return_value=False):
        assert detect.auto() is None


def test_auto_detect_prefers_firecracker_over_landlock():
    from general_ludd.security.sandboxes import detect

    with mock.patch.object(detect.sys, "platform", "linux"), \
         mock.patch("os.path.exists", return_value=True), \
         mock.patch("os.access", return_value=True), \
         mock.patch("shutil.which", return_value="/usr/bin/firecracker"), \
         mock.patch.object(detect, "_landlock_available", return_value=True):
        from general_ludd.security.sandboxes.vm.firecracker_backend import (
            FirecrackerBackend,
        )
        assert detect.auto() is FirecrackerBackend


@pytest.fixture()
def sample_spec():
    return PermissionSpec(agent_type="test-agent")


@pytest.fixture()
def sample_target():
    return SandboxTarget(pid=99999)
