"""Unit tests for VM sandbox backends — Firecracker (P1 stub) and gVisor (P4 real runsc).

Phase P4: the gVisor backend now spawns a real ``runsc`` subprocess when
available; the Firecracker backend remains a P1 stub pending REST API wiring.
"""

from __future__ import annotations

import subprocess
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


def test_firecracker_apply_with_mocked_spawn(sample_spec, sample_target):
    """P5: when firecracker is available, apply() spawns the binary + REST boots.

    The P1 stub returned ``extra={'stub': True}``. P5 replaces that with a
    real ``subprocess.Popen`` call against ``firecracker --api-sock=<path>``
    followed by the REST configuration sequence — so the test must mock Popen
    and the REST helpers to verify the wiring without requiring firecracker
    to be installed on the host.
    """
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.return_value = None

    with mock.patch.object(FirecrackerBackend, "available", return_value=True), \
         mock.patch("subprocess.Popen", return_value=fake_popen), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
             return_value=True,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
             return_value={},
         ):
        handle = FirecrackerBackend.apply(sample_spec, sample_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "firecracker"
    assert handle.applied is True
    assert handle.extra.get("pid") == 13579
    assert "sandbox_id" in handle.extra
    assert "api_sock" in handle.extra
    assert "vsock_uds" in handle.extra
    assert handle.extra.get("stub") is not True


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


def test_firecracker_verify_reports_fail_for_legacy_stub_handle(sample_spec):
    """P5: a legacy handle with no popen is flagged fail (process tracking lost).

    The P1 stub returned a ``warn`` finding for stub handles. P5 promotes
    this to ``fail`` because a handle without popen state means the sandbox
    process is unobservable — the verify step cannot confirm liveness or
    issue a graceful CtrlAltDel on release.
    """
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    handle = SandboxHandle(
        backend="firecracker", token="gludd-test", applied=True,
        extra={"stub": True},
    )
    findings = FirecrackerBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


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


def test_gvisor_apply_with_mocked_runsc_spawn(sample_spec, sample_target):
    """P4: when runsc is available, apply() spawns a real subprocess.

    The P1 stub returned ``extra={'stub': True}``. P4 replaces that with a
    real ``subprocess.Popen`` call — so the test must mock Popen to verify
    the wiring without requiring runsc to be installed on the host.
    """
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 24680
    fake_popen.poll.return_value = None

    with mock.patch.object(GvisorBackend, "available", return_value=True), \
         mock.patch("subprocess.Popen", return_value=fake_popen):
        handle = GvisorBackend.apply(sample_spec, sample_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "gvisor"
    assert handle.applied is True
    assert handle.extra.get("pid") == 24680
    assert "sandbox_id" in handle.extra


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


def test_gvisor_verify_reports_fail_for_legacy_stub_handle(sample_spec):
    """P4: a legacy handle with no popen is flagged fail (process tracking lost).

    The P1 stub returned a ``warn`` finding for stub handles. P4 promotes
    this to ``fail`` because a handle without popen state means the sandbox
    process is unobservable — the verify step cannot confirm liveness.
    """
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    handle = SandboxHandle(
        backend="gvisor", token="gludd-test", applied=True,
        extra={"stub": True},
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


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
