"""P4 TDD tests — real AgentExecutor subprocess execution + real runsc invocation.

P4 advances the VM sandbox from stubs to functional backends:
- ``AgentExecutor.receive_and_execute(target, command=...)`` runs a real
  ``subprocess.run`` against the supplied ``AgentCommand`` and returns a
  ``ProcessResult``-shaped dict (exit code, stdout/stderr bytes, wall time).
- ``GvisorBackend.apply`` builds an OCI bundle and spawns ``runsc run`` via
  ``subprocess.Popen``. The handle's ``extra`` carries the popen + pid + bundle
  path. ``verify`` polls the popen liveness; ``release`` terminates it.
- ``VMSandboxManager.dispatch`` plumbs an optional ``AgentCommand`` through
  to the executor.

Tests are written FIRST and FAIL until the implementation lands.
"""

from __future__ import annotations

import subprocess
import time
from unittest import mock

import pytest

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import (
    SandboxHandle,
    SandboxTarget,
)


@pytest.fixture(autouse=True)
def _isolate_image_cache(tmp_path, monkeypatch):
    cache = tmp_path / "sandbox-cache"
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "general_ludd.security.sandboxes.vm.image_builder.CACHE_DIR", cache
    )
    return cache


@pytest.fixture()
def sample_spec():
    return PermissionSpec(agent_type="test-agent")


@pytest.fixture()
def sample_target():
    return SandboxTarget(pid=99999)


# ---------------------------------------------------------------------------
# AgentCommand dataclass
# ---------------------------------------------------------------------------


def test_agent_command_dataclass_exists():
    from general_ludd.security.sandboxes.vm.agent_executor import AgentCommand

    cmd = AgentCommand(command=["/bin/echo", "hello"])
    assert cmd.command == ["/bin/echo", "hello"]
    assert cmd.cwd is None
    assert cmd.env is None
    assert cmd.timeout_s == 30.0


def test_agent_command_accepts_cwd_env_timeout():
    from general_ludd.security.sandboxes.vm.agent_executor import AgentCommand

    cmd = AgentCommand(
        command=["/bin/sh", "-c", "echo $HOME"],
        cwd="/tmp",
        env={"HOME": "/tmp/fake"},
        timeout_s=5.0,
    )
    assert cmd.cwd == "/tmp"
    assert cmd.env == {"HOME": "/tmp/fake"}
    assert cmd.timeout_s == 5.0


# ---------------------------------------------------------------------------
# AgentExecutor.receive_and_execute — real subprocess invocation
# ---------------------------------------------------------------------------


def test_agent_executor_runs_real_subprocess_when_command_given(sample_target):
    from general_ludd.security.sandboxes.vm.agent_executor import (
        AgentCommand,
        AgentExecutor,
    )

    cmd = AgentCommand(command=["/bin/echo", "hello-p4"])
    result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
    assert result["exit_code"] == 0
    assert b"hello-p4" in result["stdout"]
    assert result["stderr"] == b""
    assert result["wall_time_s"] >= 0.0
    assert result.get("stub") is not True


def test_agent_executor_captures_nonzero_exit_code(sample_target):
    from general_ludd.security.sandboxes.vm.agent_executor import (
        AgentCommand,
        AgentExecutor,
    )

    cmd = AgentCommand(
        command=["/bin/sh", "-c", "echo out; echo err 1>&2; exit 7"],
    )
    result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
    assert result["exit_code"] == 7
    assert b"out" in result["stdout"]
    assert b"err" in result["stderr"]
    assert result.get("stub") is not True


def test_agent_executor_respects_timeout(sample_target):
    from general_ludd.security.sandboxes.vm.agent_executor import (
        AgentCommand,
        AgentExecutor,
    )

    cmd = AgentCommand(
        command=["/bin/sh", "-c", "sleep 30"],
        timeout_s=0.5,
    )
    start = time.monotonic()
    result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, "timeout not enforced"
    assert result["exit_code"] != 0
    assert result.get("timed_out") is True


def test_agent_executor_passes_cwd(sample_target, tmp_path):
    from general_ludd.security.sandboxes.vm.agent_executor import (
        AgentCommand,
        AgentExecutor,
    )

    workdir = tmp_path / "agent-cwd"
    workdir.mkdir()
    cmd = AgentCommand(
        command=["/bin/sh", "-c", "pwd"],
        cwd=str(workdir),
    )
    result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
    assert result["exit_code"] == 0
    assert str(workdir).encode() in result["stdout"]


def test_agent_executor_passes_env(sample_target):
    from general_ludd.security.sandboxes.vm.agent_executor import (
        AgentCommand,
        AgentExecutor,
    )

    cmd = AgentCommand(
        command=["/bin/sh", "-c", "echo $GLUDD_TEST_VAR"],
        env={"GLUDD_TEST_VAR": "p4-value"},
    )
    result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
    assert result["exit_code"] == 0
    assert b"p4-value" in result["stdout"]


def test_agent_executor_stub_path_still_works(sample_target):
    """Backward-compat: when no command is supplied, the stub shape is returned."""
    from general_ludd.security.sandboxes.vm.agent_executor import AgentExecutor

    result = AgentExecutor.receive_and_execute(sample_target)
    assert result["exit_code"] == 0
    assert result.get("stub") is True


def test_agent_executor_command_only_in_result_when_command_given(sample_target):
    """The stub marker MUST disappear when a real command is supplied."""
    from general_ludd.security.sandboxes.vm.agent_executor import (
        AgentCommand,
        AgentExecutor,
    )

    cmd = AgentCommand(command=["/bin/true"])
    result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
    assert "stub" not in result


# ---------------------------------------------------------------------------
# GvisorBackend.apply — real runsc invocation
# ---------------------------------------------------------------------------


_REAL_POPEN = subprocess.Popen


def _fake_popen(pid: int = 4242, returncode: int | None = None):
    """Build a mock Popen with a configurable poll result."""
    p = mock.MagicMock(spec=_REAL_POPEN)
    p.pid = pid
    p.poll.return_value = returncode
    p.returncode = returncode
    return p


def _fresh_fake_popen(pid: int, returncode: int | None = None) -> mock.MagicMock:
    """Produce a fresh mock Popen — uses module-level spec capture so it
    survives nested ``mock.patch('subprocess.Popen', ...)`` calls."""
    p = mock.MagicMock(spec=_REAL_POPEN)
    p.pid = pid
    p.poll.return_value = returncode
    p.returncode = returncode
    return p


def test_gvisor_apply_invokes_runsc_run(sample_spec, sample_target, tmp_path):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    fake_popen = _fake_popen(pid=12345)
    with mock.patch.object(GvisorBackend, "available", return_value=True), \
         mock.patch("subprocess.Popen", return_value=fake_popen) as popen_patch, \
         mock.patch("tempfile.mkdtemp", return_value=str(tmp_path / "oci-bundle")):
        handle = GvisorBackend.apply(sample_spec, sample_target)

    assert handle.applied is True
    assert handle.backend == "gvisor"
    assert "pid" in handle.extra
    assert handle.extra["pid"] == 12345
    assert "popen" in handle.extra
    assert "bundle_path" in handle.extra
    assert "sandbox_id" in handle.extra

    popen_patch.assert_called_once()
    invoked_argv = popen_patch.call_args[0][0]
    assert invoked_argv[0] == "runsc"
    assert "run" in invoked_argv, f"expected 'run' subcommand in argv: {invoked_argv}"


def test_gvisor_apply_uses_unique_sandbox_id_per_call(sample_spec, sample_target, tmp_path):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    captured_pids: list[int] = []
    pid_counter = [1000]

    def fake_popen_factory(*args, **kwargs):
        pid = pid_counter[0]
        pid_counter[0] += 1
        captured_pids.append(pid)
        return _fresh_fake_popen(pid=pid, returncode=None)

    with mock.patch.object(GvisorBackend, "available", return_value=True), \
         mock.patch("subprocess.Popen", side_effect=fake_popen_factory), \
         mock.patch("tempfile.mkdtemp", side_effect=lambda: str(tmp_path / f"b-{len(captured_pids)}")):
        h1 = GvisorBackend.apply(sample_spec, sample_target)
        h2 = GvisorBackend.apply(sample_spec, sample_target)

    assert h1.extra["sandbox_id"] != h2.extra["sandbox_id"]
    assert h1.extra["pid"] != h2.extra["pid"]


def test_gvisor_apply_fails_open_on_popen_error(sample_spec, sample_target, tmp_path):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    with mock.patch.object(GvisorBackend, "available", return_value=True), \
         mock.patch("subprocess.Popen", side_effect=OSError("runsc not executable")), \
         mock.patch("tempfile.mkdtemp", return_value=str(tmp_path / "bundle")):
        handle = GvisorBackend.apply(sample_spec, sample_target)

    assert handle.applied is False
    assert "reason" in handle.extra
    assert "runsc not executable" in handle.extra["reason"]


def test_gvisor_apply_fails_open_when_runsc_absent(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    with mock.patch.object(GvisorBackend, "available", return_value=False):
        handle = GvisorBackend.apply(sample_spec, sample_target)
    assert handle.applied is False
    assert "reason" in handle.extra


# ---------------------------------------------------------------------------
# GvisorBackend.verify — process liveness check
# ---------------------------------------------------------------------------


def test_gvisor_verify_reports_ok_when_process_alive(sample_spec):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    fake_popen = _fake_popen(pid=9999, returncode=None)
    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"pid": 9999, "popen": fake_popen, "sandbox_id": "sb-1"},
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert any(f.severity == "ok" for f in findings)
    assert not any(f.severity == "fail" for f in findings)


def test_gvisor_verify_reports_fail_when_process_dead(sample_spec):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    fake_popen = _fake_popen(pid=9999, returncode=137)
    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"pid": 9999, "popen": fake_popen, "sandbox_id": "sb-1"},
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


def test_gvisor_verify_reports_fail_when_popen_missing(sample_spec):
    """Handle that lacks a popen (e.g. legacy stub) — verify surfaces the gap."""
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"stub": True},
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


def test_gvisor_verify_reports_fail_when_not_applied(sample_spec):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=False,
        extra={"reason": "no runsc"},
    )
    findings = GvisorBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


# ---------------------------------------------------------------------------
# GvisorBackend.release — process teardown
# ---------------------------------------------------------------------------


def test_gvisor_release_terminates_popen(sample_spec):
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    fake_popen = _fake_popen(pid=8888, returncode=None)
    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"pid": 8888, "popen": fake_popen, "sandbox_id": "sb-release"},
    )
    GvisorBackend.release(handle)
    fake_popen.terminate.assert_called_once()


def test_gvisor_release_kills_after_terminate_timeout():
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 7777
    fake_popen.poll.return_value = None
    fake_popen.wait.side_effect = subprocess.TimeoutExpired(cmd="runsc", timeout=2)

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"pid": 7777, "popen": fake_popen, "sandbox_id": "sb-stubborn"},
    )
    GvisorBackend.release(handle)
    fake_popen.terminate.assert_called_once()
    fake_popen.kill.assert_called_once()


def test_gvisor_release_idempotent_when_popen_already_dead():
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    fake_popen = _fake_popen(pid=6666, returncode=0)
    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"pid": 6666, "popen": fake_popen, "sandbox_id": "sb-dead"},
    )
    GvisorBackend.release(handle)
    fake_popen.terminate.assert_not_called()


def test_gvisor_release_safe_when_no_popen():
    """Legacy stub handle has no popen — release must be a no-op, not raise."""
    from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend

    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-test",
        applied=True,
        extra={"stub": True},
    )
    GvisorBackend.release(handle)


# ---------------------------------------------------------------------------
# VMSandboxManager.dispatch — command plumbing
# ---------------------------------------------------------------------------


def test_manager_dispatch_passes_command_through(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.agent_executor import AgentCommand
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMSandboxManager,
    )

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=SandboxHandle(
            backend="firecracker", token="t", applied=True, extra={},
        ),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    cmd = AgentCommand(command=["/bin/echo", "via-manager"])
    result = mgr.dispatch(inst.instance_id, sample_target, command=cmd)
    assert result["status"] == "executed"
    assert b"via-manager" in result["result"]["stdout"]
    assert inst.metrics.dispatch_count == 1


def test_manager_dispatch_without_command_uses_stub_path(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=SandboxHandle(
            backend="firecracker", token="t", applied=True, extra={},
        ),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    result = mgr.dispatch(inst.instance_id, sample_target)
    assert result["result"].get("stub") is True
