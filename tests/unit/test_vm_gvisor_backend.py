"""Unit tests for GVisorBackend — P4 real runsc subprocess backend.

Covers: module exports, _RUNSC_TERMINATE_GRACE_S constant, GVisorBackend
          protocol shape, available() detection, apply() lifecycle
          (success, no-runsc fail-open, image build failure, spawn OSError),
          verify() findings (not-applied, legacy stub, alive, dead, poll
          exception), and release() lifecycle (no-popen no-op, already-dead
          no-op, normal terminate+wait, terminate exception, wait timeout
          + kill, kill exception).
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import (
    Finding,
    SandboxHandle,
    SandboxTarget,
)
from general_ludd.security.sandboxes.vm.gvisor_backend import (
    _RUNSC_TERMINATE_GRACE_S,
    GvisorBackend,
    _spawn_runsc,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_spec():
    return PermissionSpec(agent_type="test-agent")


@pytest.fixture()
def sample_target():
    return SandboxTarget(pid=99999)


# ---------------------------------------------------------------------------
# Module-level constants and exports
# ---------------------------------------------------------------------------


def test_all_exports_gvisor_backend():
    from general_ludd.security.sandboxes.vm import gvisor_backend as mod

    assert mod.__all__ == ["GvisorBackend"]
    assert mod.GvisorBackend is GvisorBackend


def test_runsc_terminate_grace_s_constant():
    assert isinstance(_RUNSC_TERMINATE_GRACE_S, float)
    assert _RUNSC_TERMINATE_GRACE_S == 2.0


# ---------------------------------------------------------------------------
# Protocol shape
# ---------------------------------------------------------------------------


def test_gvisor_backend_name():
    assert GvisorBackend.name == "gvisor"


def test_gvisor_backend_has_required_methods():
    for attr in ("available", "apply", "verify", "release"):
        assert hasattr(GvisorBackend, attr), f"GvisorBackend missing {attr}"


# ---------------------------------------------------------------------------
# available()
# ---------------------------------------------------------------------------


def test_available_true_when_runsc_found():
    with mock.patch("shutil.which", return_value="/usr/local/bin/runsc"):
        assert GvisorBackend.available() is True


def test_available_false_when_runsc_missing():
    with mock.patch("shutil.which", return_value=None):
        assert GvisorBackend.available() is False


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------


def test_apply_success_with_runsc_available(sample_spec, sample_target):
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.return_value = None

    with (
        mock.patch.object(GvisorBackend, "available", return_value=True),
        mock.patch(
            "general_ludd.security.sandboxes.vm.gvisor_backend.build_gvisor_image",
            return_value=mock.MagicMock(path="/tmp/gludd-oci/test-bundle"),
        ),
        mock.patch("subprocess.Popen", return_value=fake_popen),
    ):
        handle = GvisorBackend.apply(sample_spec, sample_target)

    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "gvisor"
    assert handle.token == "gludd-test-agent"
    assert handle.applied is True
    assert handle.extra.get("pid") == 13579
    assert "sandbox_id" in handle.extra
    assert isinstance(handle.extra["sandbox_id"], str)
    assert handle.extra["sandbox_id"].startswith("gludd-sb-")
    assert "bundle_path" in handle.extra
    assert "started_at" in handle.extra
    assert isinstance(handle.extra.get("popen"), subprocess.Popen)
    assert isinstance(handle.extra["started_at"], float)


def test_apply_fails_open_when_runsc_absent(sample_spec, sample_target):
    with mock.patch.object(GvisorBackend, "available", return_value=False):
        handle = GvisorBackend.apply(sample_spec, sample_target)

    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "gvisor"
    assert handle.token == "gludd-test-agent"
    assert handle.applied is False
    assert handle.extra.get("reason") == "runsc binary absent"


def test_apply_fails_open_on_image_build_failure(sample_spec, sample_target):
    with (
        mock.patch.object(GvisorBackend, "available", return_value=True),
        mock.patch(
            "general_ludd.security.sandboxes.vm.gvisor_backend.build_gvisor_image",
            side_effect=RuntimeError("no disk space"),
        ),
    ):
        handle = GvisorBackend.apply(sample_spec, sample_target)

    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "gvisor"
    assert handle.applied is False
    reason = str(handle.extra.get("reason", ""))
    assert "image build failed" in reason


def test_apply_fails_open_on_runsc_spawn_oserror(sample_spec, sample_target):
    with (
        mock.patch.object(GvisorBackend, "available", return_value=True),
        mock.patch(
            "general_ludd.security.sandboxes.vm.gvisor_backend.build_gvisor_image",
            return_value=mock.MagicMock(path="/tmp/gludd-oci/test-bundle"),
        ),
        mock.patch("subprocess.Popen", side_effect=OSError("No such file")),
    ):
        handle = GvisorBackend.apply(sample_spec, sample_target)

    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "gvisor"
    assert handle.applied is False
    reason = str(handle.extra.get("reason", ""))
    assert "runsc spawn failed" in reason


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------


def test_verify_not_applied_reports_fail(sample_spec):
    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=False,
        extra={"reason": "no runsc binary"},
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert len(findings) == 1
    assert findings[0].severity == "fail"
    assert "not applied" in findings[0].message
    assert "no runsc binary" in findings[0].message


def test_verify_not_applied_without_reason_key(sample_spec):
    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=False,
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert findings[0].severity == "fail"
    assert "unknown" in findings[0].message


def test_verify_legacy_stub_no_popen(sample_spec):
    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"stub": True},
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)
    assert any("no live popen" in f.message for f in findings)


def test_verify_sandbox_alive(sample_spec):
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.return_value = None

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test-agent",
        applied=True,
        extra={
            "popen": fake_popen,
            "pid": 13579,
            "sandbox_id": "gludd-sb-deadbeef1234",
            "bundle_path": "/tmp/gludd-oci/bundle",
            "started_at": 1234567890.0,
        },
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert len(findings) == 1
    assert findings[0].severity == "ok"
    assert "alive" in findings[0].message
    assert "gludd-sb-deadbeef1234" in findings[0].message
    assert "pid=13579" in findings[0].message


def test_verify_sandbox_dead(sample_spec):
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.return_value = 1

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test-agent",
        applied=True,
        extra={
            "popen": fake_popen,
            "pid": 13579,
            "sandbox_id": "gludd-sb-deadbeef1234",
            "bundle_path": "/tmp/gludd-oci/bundle",
            "started_at": 1234567890.0,
        },
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert len(findings) == 1
    assert findings[0].severity == "fail"
    assert "dead" in findings[0].message
    assert "returncode=1" in findings[0].message


def test_verify_sandbox_dead_returncode_zero(sample_spec):
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.return_value = 0

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test-agent",
        applied=True,
        extra={
            "popen": fake_popen,
            "pid": 13579,
            "sandbox_id": "gludd-sb-deadbeef1234",
        },
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert findings[0].severity == "fail"
    assert "returncode=0" in findings[0].message


def test_verify_poll_raises_exception(sample_spec):
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.side_effect = ProcessLookupError("no such process")

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test-agent",
        applied=True,
        extra={
            "popen": fake_popen,
            "pid": 13579,
            "sandbox_id": "gludd-sb-deadbeef1234",
        },
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert len(findings) == 1
    assert findings[0].severity == "warn"
    assert "ProcessLookupError" in findings[0].message


def test_verify_all_findings_are_finding_instances(sample_spec):
    for handle in (
        SandboxHandle(backend="gvisor", token="t", applied=False),
        SandboxHandle(backend="gvisor", token="t", applied=True),
    ):
        findings = GvisorBackend.verify(sample_spec, handle)
        for f in findings:
            assert isinstance(f, Finding)
            assert isinstance(f.severity, str)
            assert isinstance(f.message, str)


# ---------------------------------------------------------------------------
# release()
# ---------------------------------------------------------------------------


def test_release_noop_when_no_popen():
    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"stub": True},
    )
    GvisorBackend.release(handle)


def test_release_noop_when_popen_already_dead():
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.return_value = 0

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"popen": fake_popen, "pid": 13579},
    )
    GvisorBackend.release(handle)

    fake_popen.poll.assert_called_once()
    fake_popen.terminate.assert_not_called()


def test_release_terminates_alive_popen():
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.return_value = None

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"popen": fake_popen, "pid": 13579},
    )
    GvisorBackend.release(handle)

    fake_popen.terminate.assert_called_once()
    fake_popen.wait.assert_called_once_with(timeout=2.0)


def test_release_handles_terminate_exception():
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.return_value = None
    fake_popen.terminate.side_effect = OSError("no process")

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"popen": fake_popen, "pid": 13579},
    )
    GvisorBackend.release(handle)

    fake_popen.terminate.assert_called_once()
    fake_popen.wait.assert_not_called()


def test_release_kills_on_wait_timeout():
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.return_value = None
    fake_popen.wait.side_effect = subprocess.TimeoutExpired(cmd="runsc", timeout=2.0)

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"popen": fake_popen, "pid": 13579},
    )
    GvisorBackend.release(handle)

    fake_popen.terminate.assert_called_once()
    fake_popen.wait.assert_called_once_with(timeout=2.0)
    fake_popen.kill.assert_called_once()


def test_release_handles_kill_exception():
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.return_value = None
    fake_popen.wait.side_effect = subprocess.TimeoutExpired(cmd="runsc", timeout=2.0)
    fake_popen.kill.side_effect = OSError("no such process")

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"popen": fake_popen, "pid": 13579},
    )
    GvisorBackend.release(handle)

    fake_popen.kill.assert_called_once()


def test_release_handles_poll_exception_proceeds_to_terminate():
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 13579
    fake_popen.poll.side_effect = ProcessLookupError("no process")
    fake_popen.wait.return_value = None

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"popen": fake_popen, "pid": 13579},
    )
    GvisorBackend.release(handle)

    fake_popen.poll.assert_called_once()
    fake_popen.terminate.assert_called_once()
    fake_popen.wait.assert_called_once_with(timeout=2.0)


# ---------------------------------------------------------------------------
# _spawn_runsc() internal
# ---------------------------------------------------------------------------


def test_spawn_runsc_builds_image_manifest(sample_spec, sample_target):
    fake_bundle = mock.MagicMock(path="/tmp/gludd-oci/bundle-abc")
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 99999

    with (
        mock.patch(
            "general_ludd.security.sandboxes.vm.gvisor_backend.build_gvisor_image",
            return_value=fake_bundle,
        ) as mock_build,
        mock.patch("subprocess.Popen", return_value=fake_popen),
    ):
        handle = _spawn_runsc(sample_spec, sample_target)

    assert handle.applied is True
    mock_build.assert_called_once()
    manifest = mock_build.call_args[0][0]
    assert manifest.name.startswith("gludd-sb-")
    assert "python3" in manifest.packages
    assert ("usr/bin/agent_executor", mock.ANY) in manifest.custom_files


def test_spawn_runsc_includes_correct_popen_args(sample_spec, sample_target):
    fake_bundle = mock.MagicMock(path="/tmp/gludd-oci/bundle-test")
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 42

    with (
        mock.patch(
            "general_ludd.security.sandboxes.vm.gvisor_backend.build_gvisor_image",
            return_value=fake_bundle,
        ),
        mock.patch("subprocess.Popen", return_value=fake_popen) as mock_popen,
    ):
        _spawn_runsc(sample_spec, sample_target)

    args = mock_popen.call_args[0][0]
    assert args[0] == "runsc"
    assert args[1] == "--root=/tmp/gludd-runsc"
    assert args[2] == "run"
    assert args[3].startswith("--bundle=")
    assert "/tmp/gludd-oci/bundle-test" in args[3]
    assert args[4].startswith("gludd-sb-")
    assert mock_popen.call_args[1].get("stdout") == subprocess.DEVNULL
    assert mock_popen.call_args[1].get("stderr") == subprocess.DEVNULL


def test_spawn_runsc_token_uses_spec_agent_type(sample_spec, sample_target):
    fake_bundle = mock.MagicMock(path="/tmp/gludd-oci/bundle")
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 1

    with (
        mock.patch(
            "general_ludd.security.sandboxes.vm.gvisor_backend.build_gvisor_image",
            return_value=fake_bundle,
        ),
        mock.patch("subprocess.Popen", return_value=fake_popen),
    ):
        handle = _spawn_runsc(sample_spec, sample_target)

    assert handle.token == "gludd-test-agent"


def test_spawn_runsc_sandbox_id_is_unique():
    fake_bundle = mock.MagicMock(path="/tmp/gludd-oci/bundle")
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 1

    with (
        mock.patch(
            "general_ludd.security.sandboxes.vm.gvisor_backend.build_gvisor_image",
            return_value=fake_bundle,
        ),
        mock.patch("subprocess.Popen", return_value=fake_popen),
    ):
        h1 = _spawn_runsc(
            PermissionSpec(agent_type="a"),
            SandboxTarget(pid=1),
        )
        h2 = _spawn_runsc(
            PermissionSpec(agent_type="b"),
            SandboxTarget(pid=2),
        )

    assert h1.extra["sandbox_id"] != h2.extra["sandbox_id"]


def test_spawn_runsc_handle_extra_keys(sample_spec, sample_target):
    fake_bundle = mock.MagicMock(path="/tmp/gludd-oci/bundle-xyz")
    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 77

    with (
        mock.patch(
            "general_ludd.security.sandboxes.vm.gvisor_backend.build_gvisor_image",
            return_value=fake_bundle,
        ),
        mock.patch("subprocess.Popen", return_value=fake_popen),
    ):
        handle = _spawn_runsc(sample_spec, sample_target)

    assert set(handle.extra.keys()) == {
        "popen",
        "pid",
        "sandbox_id",
        "bundle_path",
        "started_at",
    }
    assert handle.extra["pid"] == 77
    assert handle.extra["bundle_path"] == "/tmp/gludd-oci/bundle-xyz"
