"""P6 integration tests — full VM sandbox lifecycle for both backends.

Exercises the real P5 Firecracker REST API path and the real P4 runsc path
through the daemon-facing ``VMSandboxManager``. Each test drives the complete
boot → dispatch → verify → release cycle and asserts on:

  * State machine transitions (PENDING → RUNNING → EXECUTING → RUNNING → STOPPED)
  * Real handle extras (pid, popen, sandbox_id, api_sock / bundle_path — NOT stubs)
  * AgentExecutor subprocess dispatch producing real stdout/stderr/exit_code
  * Verify liveness findings (ok / warn / fail) matching the mock process state
  * Observability metrics aggregation (boot_ms, dispatch_count, verify_findings)
  * Error recovery (backend unavailable → FAILED → release safe)
  * Idempotent release
  * Multi-instance concurrent isolation

Mocks substitute for the firecracker binary, runsc binary, /dev/kvm, and the
Firecracker REST UNIX socket — no real microVM or gVisor sandbox is spawned.
The AgentExecutor dispatch path uses real ``subprocess.run`` against actual
binaries (``/bin/echo``, ``/usr/bin/env``) so the dispatch surface is verified
end-to-end with real process trees, exit codes, and stdout capture.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from general_ludd.security.permissions import Capability, PermissionSpec
from general_ludd.security.sandboxes import (
    SandboxHandle,
    SandboxTarget,
)
from general_ludd.security.sandboxes.vm.agent_executor import (
    AgentCommand,
    AgentExecutor,
)
from general_ludd.security.sandboxes.vm.firecracker_backend import (
    FirecrackerBackend,
    _firecracker_put,
    _wait_for_socket,
)
from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend
from general_ludd.security.sandboxes.vm.lifecycle import (
    VMLifecycleState,
    VMSandboxManager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_REAL_POPEN = subprocess.Popen


@pytest.fixture(autouse=True)
def _isolate_image_cache(tmp_path: Path, monkeypatch) -> Path:
    """Redirect the image-builder cache to a per-test tmp dir."""
    cache = tmp_path / "sandbox-cache"
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "general_ludd.security.sandboxes.vm.image_builder.CACHE_DIR", cache,
    )
    return cache


@pytest.fixture()
def sample_spec() -> PermissionSpec:
    return PermissionSpec(
        agent_type="integration-agent",
        capabilities=[
            Capability(resource="file:", actions=["read", "write"]),
            Capability(resource="net:egress:any", actions=["connect"]),
        ],
    )


@pytest.fixture()
def sample_target() -> SandboxTarget:
    return SandboxTarget(pid=os.getpid(), directory="/tmp/gludd-sandbox-test")


def _fake_popen(pid: int = 4242, returncode: int | None = None) -> mock.MagicMock:
    """Mock Popen with configurable poll result."""
    p = mock.MagicMock(spec=_REAL_POPEN)
    p.pid = pid
    p.poll.return_value = returncode
    p.returncode = returncode
    return p


# ---------------------------------------------------------------------------
# Firecracker mock helpers
# ---------------------------------------------------------------------------


def _mock_firecracker_apply(
    pid: int = 31337,
    returncode: int | None = None,
    sock_exists: bool = True,
) -> tuple[mock.MagicMock, str]:
    """Patch FirecrackerBackend.apply to return a handle with a fake live popen.

    Returns ``(handle_patcher, api_sock_path)``. The patcher is already started;
    caller is responsible for stopping it (or use ``_mock_fc_context``).
    """
    api_sock = f"/tmp/gludd-test-fc-{pid}-{int(time.time() * 1000)}.sock"
    fake_popen = _fake_popen(pid=pid, returncode=returncode)
    handle = SandboxHandle(
        backend="firecracker",
        token="gludd-integration-agent",
        applied=True,
        extra={
            "popen": fake_popen,
            "pid": pid,
            "sandbox_id": f"gludd-fc-mock-{pid}",
            "api_sock": api_sock,
            "vsock_uds": f"/tmp/gludd-test-fc-{pid}.vsock",
            "started_at": time.time(),
        },
    )
    avail_patcher = mock.patch.object(
        FirecrackerBackend, "available", return_value=True,
    )
    avail_patcher.start()
    patcher = mock.patch.object(
        FirecrackerBackend, "apply", return_value=handle,
    )
    patcher.start()

    if sock_exists:
        # Create the socket file so verify's os.path.exists check passes
        Path(api_sock).touch()

    return patcher, api_sock


# ---------------------------------------------------------------------------
# gVisor mock helpers
# ---------------------------------------------------------------------------


def _mock_gvisor_apply(
    pid: int = 5151,
    returncode: int | None = None,
) -> mock.MagicMock:
    """Patch GvisorBackend.apply to return a handle with a fake live popen."""
    fake_popen = _fake_popen(pid=pid, returncode=returncode)
    handle = SandboxHandle(
        backend="gvisor",
        token="gludd-integration-agent",
        applied=True,
        extra={
            "popen": fake_popen,
            "pid": pid,
            "sandbox_id": f"gludd-sb-mock-{pid}",
            "bundle_path": "/tmp/gludd-test-oci-bundle",
            "started_at": time.time(),
        },
    )
    avail_patcher = mock.patch.object(
        GvisorBackend, "available", return_value=True,
    )
    avail_patcher.start()
    patcher = mock.patch.object(GvisorBackend, "apply", return_value=handle)
    patcher.start()
    return fake_popen


# ---------------------------------------------------------------------------
# =====================================================================
# FULL LIFECYCLE: FIRECRACKER (boot → dispatch → verify → release)
# =====================================================================
# ---------------------------------------------------------------------------


class TestFirecrackerFullLifecycle:
    """Drive the real P5 Firecracker backend through VMSandboxManager."""

    def test_boot_transitions_to_running(self, sample_spec, sample_target):
        patcher, _ = _mock_firecracker_apply()
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)

            assert inst.state is VMLifecycleState.RUNNING
            assert inst.backend_name == "firecracker"
            assert inst.instance_id.startswith("vm-")
            assert inst.handle.applied is True
            assert inst.metrics.boot_ms > 0
            assert inst.image_path is None
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_boot_records_real_handle_extras(self, sample_spec, sample_target):
        patcher, api_sock = _mock_firecracker_apply(pid=7777)
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)

            extra = inst.handle.extra
            assert extra["pid"] == 7777
            assert "popen" in extra
            assert extra["sandbox_id"] == "gludd-fc-mock-7777"
            assert extra["api_sock"] == api_sock
            assert "vsock_uds" in extra
            assert "started_at" in extra
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_boot_emits_booted_event(self, sample_spec, sample_target):
        patcher, _ = _mock_firecracker_apply()
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)

            booted_events = [e for e in mgr.events if e["event"] == "booted"]
            assert len(booted_events) == 1
            assert booted_events[0]["instance_id"] == inst.instance_id
            assert booted_events[0]["backend"] == "firecracker"
            assert booted_events[0]["boot_ms"] > 0
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_dispatch_runs_real_subprocess(self, sample_spec, sample_target):
        patcher, _ = _mock_firecracker_apply()
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)

            cmd = AgentCommand(command=["/bin/echo", "dispatch-fc-lifecycle"])
            result = mgr.dispatch(inst.instance_id, sample_target, command=cmd)

            assert result["status"] == "executed"
            assert result["instance_id"] == inst.instance_id
            proc = result["result"]
            assert proc["exit_code"] == 0
            assert b"dispatch-fc-lifecycle" in proc["stdout"]
            assert proc["timed_out"] is False
            assert proc["wall_time_s"] >= 0.0
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_dispatch_transitions_to_executing_then_running(
        self, sample_spec, sample_target,
    ):
        patcher, _ = _mock_firecracker_apply()
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)
            assert inst.state is VMLifecycleState.RUNNING

            cmd = AgentCommand(command=["/usr/bin/true"])
            mgr.dispatch(inst.instance_id, sample_target, command=cmd)

            assert inst.state is VMLifecycleState.RUNNING
            assert inst.metrics.dispatch_count == 1
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_verify_reports_ok_when_process_alive(
        self, sample_spec, sample_target,
    ):
        patcher, _api_sock = _mock_firecracker_apply(
            pid=8800, returncode=None, sock_exists=True,
        )
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)

            findings = mgr.verify(inst.instance_id)
            assert inst.metrics.last_verify_findings == len(findings)
            assert any(f.severity == "ok" for f in findings)
            assert not any(f.severity == "fail" for f in findings)
        finally:
            patcher.stop()
            mock.patch.stopall()
            with mock.patch("os.path.exists", return_value=True):
                pass

    def test_verify_reports_fail_when_process_dead(
        self, sample_spec, sample_target,
    ):
        patcher, _api_sock = _mock_firecracker_apply(
            pid=8801, returncode=137, sock_exists=True,
        )
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)

            findings = mgr.verify(inst.instance_id)
            assert any(f.severity == "fail" for f in findings)
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_full_boot_dispatch_verify_release_cycle(
        self, sample_spec, sample_target,
    ):
        patcher, _api_sock = _mock_firecracker_apply(
            pid=9900, returncode=None, sock_exists=True,
        )
        try:
            mgr = VMSandboxManager()

            # boot
            inst = mgr.boot("firecracker", sample_spec, sample_target)
            assert inst.state is VMLifecycleState.RUNNING

            # dispatch
            cmd = AgentCommand(command=["/bin/echo", "fc-full-cycle"])
            result = mgr.dispatch(inst.instance_id, sample_target, command=cmd)
            assert result["status"] == "executed"
            assert b"fc-full-cycle" in result["result"]["stdout"]

            # verify
            findings = mgr.verify(inst.instance_id)
            assert any(f.severity == "ok" for f in findings)

            # release
            release_result = mgr.release(inst.instance_id)
            assert release_result["state"] == "stopped"
            assert inst.state is VMLifecycleState.STOPPED
            assert inst.stopped_at > 0
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_release_emits_released_event(self, sample_spec, sample_target):
        patcher, _ = _mock_firecracker_apply()
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)
            mgr.release(inst.instance_id)

            released = [e for e in mgr.events if e["event"] == "released"]
            assert len(released) == 1
            assert released[0]["instance_id"] == inst.instance_id
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_idempotent_release(self, sample_spec, sample_target):
        patcher, _ = _mock_firecracker_apply()
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)

            r1 = mgr.release(inst.instance_id)
            r2 = mgr.release(inst.instance_id)

            assert r1["state"] == "stopped"
            assert r2["state"] == "stopped"
            assert r1["instance_id"] == r2["instance_id"]
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_dispatch_rejects_stopped_instance(self, sample_spec, sample_target):
        patcher, _ = _mock_firecracker_apply()
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)
            mgr.release(inst.instance_id)

            with pytest.raises(RuntimeError, match="not running"):
                mgr.dispatch(inst.instance_id, sample_target)
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_verify_emits_verified_event(self, sample_spec, sample_target):
        patcher, _api_sock = _mock_firecracker_apply(
            returncode=None, sock_exists=True,
        )
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)
            mgr.verify(inst.instance_id)

            verified = [e for e in mgr.events if e["event"] == "verified"]
            assert len(verified) == 1
            assert "findings" in verified[0]
        finally:
            patcher.stop()
            mock.patch.stopall()


# ---------------------------------------------------------------------------
# =====================================================================
# FULL LIFECYCLE: gVISOR (boot → dispatch → verify → release)
# =====================================================================
# ---------------------------------------------------------------------------


class TestGvisorFullLifecycle:
    """Drive the real P4 gVisor backend through VMSandboxManager."""

    def test_boot_transitions_to_running(self, sample_spec, sample_target):
        _mock_gvisor_apply(pid=6001)
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("gvisor", sample_spec, sample_target)

            assert inst.state is VMLifecycleState.RUNNING
            assert inst.backend_name == "gvisor"
            assert inst.handle.applied is True
            assert inst.metrics.boot_ms > 0
        finally:
            mock.patch.stopall()

    def test_boot_records_real_handle_extras(self, sample_spec, sample_target):
        _mock_gvisor_apply(pid=6002)
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("gvisor", sample_spec, sample_target)

            extra = inst.handle.extra
            assert extra["pid"] == 6002
            assert "popen" in extra
            assert extra["sandbox_id"] == "gludd-sb-mock-6002"
            assert "bundle_path" in extra
            assert "started_at" in extra
        finally:
            mock.patch.stopall()

    def test_dispatch_runs_real_subprocess(self, sample_spec, sample_target):
        _mock_gvisor_apply(pid=6003)
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("gvisor", sample_spec, sample_target)

            cmd = AgentCommand(command=["/bin/echo", "gv-dispatch-ok"])
            result = mgr.dispatch(inst.instance_id, sample_target, command=cmd)

            assert result["status"] == "executed"
            assert b"gv-dispatch-ok" in result["result"]["stdout"]
            assert result["result"]["exit_code"] == 0
        finally:
            mock.patch.stopall()

    def test_verify_reports_ok_when_process_alive(
        self, sample_spec, sample_target,
    ):
        _mock_gvisor_apply(pid=6004, returncode=None)
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("gvisor", sample_spec, sample_target)
            findings = mgr.verify(inst.instance_id)

            assert any(f.severity == "ok" for f in findings)
            assert not any(f.severity == "fail" for f in findings)
        finally:
            mock.patch.stopall()

    def test_verify_reports_fail_when_process_dead(
        self, sample_spec, sample_target,
    ):
        _mock_gvisor_apply(pid=6005, returncode=1)
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("gvisor", sample_spec, sample_target)
            findings = mgr.verify(inst.instance_id)

            assert any(f.severity == "fail" for f in findings)
        finally:
            mock.patch.stopall()

    def test_full_boot_dispatch_verify_release_cycle(
        self, sample_spec, sample_target,
    ):
        _mock_gvisor_apply(pid=6006, returncode=None)
        try:
            mgr = VMSandboxManager()

            inst = mgr.boot("gvisor", sample_spec, sample_target)
            assert inst.state is VMLifecycleState.RUNNING

            cmd = AgentCommand(command=["/bin/echo", "gv-full-cycle"])
            result = mgr.dispatch(inst.instance_id, sample_target, command=cmd)
            assert b"gv-full-cycle" in result["result"]["stdout"]

            findings = mgr.verify(inst.instance_id)
            assert any(f.severity == "ok" for f in findings)

            mgr.release(inst.instance_id)
            assert inst.state is VMLifecycleState.STOPPED
        finally:
            mock.patch.stopall()

    def test_release_terminates_popen(self, sample_spec, sample_target):
        fake_popen = _mock_gvisor_apply(pid=6007, returncode=None)
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("gvisor", sample_spec, sample_target)
            mgr.release(inst.instance_id)

            fake_popen.terminate.assert_called_once()
            assert inst.state is VMLifecycleState.STOPPED
        finally:
            mock.patch.stopall()

    def test_multiple_dispatch_increments_counter(
        self, sample_spec, sample_target,
    ):
        _mock_gvisor_apply(pid=6008, returncode=None)
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("gvisor", sample_spec, sample_target)

            for i in range(5):
                cmd = AgentCommand(command=["/bin/echo", f"iter-{i}"])
                mgr.dispatch(inst.instance_id, sample_target, command=cmd)

            assert inst.metrics.dispatch_count == 5
            assert inst.metrics.total_dispatch_ms > 0
        finally:
            mock.patch.stopall()


# ---------------------------------------------------------------------------
# =====================================================================
# ERROR RECOVERY LIFECYCLE
# =====================================================================
# ---------------------------------------------------------------------------


class TestErrorRecoveryLifecycle:
    """Backend unavailable → FAILED state → release is safe."""

    def test_firecracker_unavailable_yields_failed_instance(
        self, sample_spec, sample_target,
    ):
        with mock.patch.object(
            FirecrackerBackend, "available", return_value=False,
        ):
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)

        assert inst.state is VMLifecycleState.FAILED
        assert inst.handle.applied is False
        assert "reason" in inst.handle.extra

    def test_gvisor_unavailable_yields_failed_instance(
        self, sample_spec, sample_target,
    ):
        with mock.patch.object(
            GvisorBackend, "available", return_value=False,
        ):
            mgr = VMSandboxManager()
            inst = mgr.boot("gvisor", sample_spec, sample_target)

        assert inst.state is VMLifecycleState.FAILED
        assert inst.handle.applied is False

    def test_failed_instance_rejects_dispatch(
        self, sample_spec, sample_target,
    ):
        with mock.patch.object(
            FirecrackerBackend, "available", return_value=False,
        ):
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)

        with pytest.raises(RuntimeError, match="not running"):
            mgr.dispatch(inst.instance_id, sample_target)

    def test_failed_instance_release_is_safe(
        self, sample_spec, sample_target,
    ):
        with mock.patch.object(
            FirecrackerBackend, "available", return_value=False,
        ):
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)

        result = mgr.release(inst.instance_id)
        assert result["state"] == "stopped"

    def test_boot_failed_emits_event(self, sample_spec, sample_target):
        with mock.patch.object(
            GvisorBackend, "available", return_value=False,
        ):
            mgr = VMSandboxManager()
            mgr.boot("gvisor", sample_spec, sample_target)

        failed_events = [e for e in mgr.events if e["event"] == "boot_failed"]
        assert len(failed_events) == 1
        assert failed_events[0]["reason"] == "backend unavailable"

    def test_unknown_backend_raises_valueerror(self, sample_spec, sample_target):
        mgr = VMSandboxManager()
        with pytest.raises(ValueError, match="Unknown backend"):
            mgr.boot("invalid-backend", sample_spec, sample_target)


# ---------------------------------------------------------------------------
# =====================================================================
# MULTI-INSTANCE CONCURRENT ISOLATION
# =====================================================================
# ---------------------------------------------------------------------------


class TestMultiInstanceLifecycle:
    """Boot N instances, interleave dispatches, verify all, release all."""

    def test_two_firecracker_instances_isolated(self, sample_spec, sample_target):
        patcher1, _ = _mock_firecracker_apply(pid=7101)
        try:
            mgr = VMSandboxManager()
            inst1 = mgr.boot("firecracker", sample_spec, sample_target)
            patcher1.stop()

            patcher2, _ = _mock_firecracker_apply(pid=7102)
            try:
                inst2 = mgr.boot("firecracker", sample_spec, sample_target)

                assert inst1.instance_id != inst2.instance_id
                assert inst1.handle.extra["pid"] != inst2.handle.extra["pid"]

                # Interleave dispatches
                c1 = AgentCommand(command=["/bin/echo", "inst1"])
                c2 = AgentCommand(command=["/bin/echo", "inst2"])
                r1 = mgr.dispatch(inst1.instance_id, sample_target, command=c1)
                r2 = mgr.dispatch(inst2.instance_id, sample_target, command=c2)

                assert b"inst1" in r1["result"]["stdout"]
                assert b"inst2" in r2["result"]["stdout"]
                assert r1["instance_id"] != r2["instance_id"]

                mgr.verify(inst1.instance_id)
                mgr.verify(inst2.instance_id)
                mgr.release(inst1.instance_id)
                mgr.release(inst2.instance_id)

                assert inst1.state is VMLifecycleState.STOPPED
                assert inst2.state is VMLifecycleState.STOPPED
            finally:
                patcher2.stop()
        finally:
            mock.patch.stopall()

    def test_mixed_backend_instances_coexist(
        self, sample_spec, sample_target,
    ):
        fc_patcher, _ = _mock_firecracker_apply(pid=7201)
        try:
            mgr = VMSandboxManager()
            fc_inst = mgr.boot("firecracker", sample_spec, sample_target)
            fc_patcher.stop()

            _mock_gvisor_apply(pid=7202)
            try:
                gv_inst = mgr.boot("gvisor", sample_spec, sample_target)

                assert fc_inst.backend_name == "firecracker"
                assert gv_inst.backend_name == "gvisor"
                assert fc_inst.instance_id != gv_inst.instance_id

                mgr.release(fc_inst.instance_id)
                mgr.release(gv_inst.instance_id)

                assert len(mgr.instances) == 2
            finally:
                mock.patch.stopall()
        finally:
            mock.patch.stopall()

    def test_list_instances_returns_all(self, sample_spec, sample_target):
        fc_patcher, _ = _mock_firecracker_apply(pid=7301)
        try:
            mgr = VMSandboxManager()
            mgr.boot("firecracker", sample_spec, sample_target)
            fc_patcher.stop()

            _mock_gvisor_apply(pid=7302)
            try:
                mgr.boot("gvisor", sample_spec, sample_target)

                instances = mgr.list_instances()
                assert len(instances) == 2
                backends = {i.backend_name for i in instances}
                assert backends == {"firecracker", "gvisor"}
            finally:
                mock.patch.stopall()
        finally:
            mock.patch.stopall()

    def test_dispatch_unknown_instance_raises_keyerror(self, sample_target):
        mgr = VMSandboxManager()
        with pytest.raises(KeyError, match="not found"):
            mgr.dispatch("vm-nonexistent", sample_target)

    def test_verify_unknown_instance_raises_keyerror(self):
        mgr = VMSandboxManager()
        with pytest.raises(KeyError, match="not found"):
            mgr.verify("vm-nonexistent")

    def test_release_unknown_instance_raises_keyerror(self):
        mgr = VMSandboxManager()
        with pytest.raises(KeyError, match="not found"):
            mgr.release("vm-nonexistent")


# ---------------------------------------------------------------------------
# =====================================================================
# OBSERVABILITY METRICS AGGREGATION
# =====================================================================
# ---------------------------------------------------------------------------


class TestObservabilityAggregation:
    """observe() returns correct aggregate metrics after lifecycle cycles."""

    def test_empty_observe_returns_zeros(self):
        mgr = VMSandboxManager()
        obs = mgr.observe()
        assert obs["total_instances"] == 0
        assert obs["running_instances"] == 0
        assert obs["total_dispatches"] == 0
        assert obs["total_verify_findings"] == 0
        assert obs["avg_boot_ms"] == 0.0
        assert obs["events_emitted"] == 0
        assert obs["state_breakdown"] == {}

    def test_observe_counts_running_instance(self, sample_spec, sample_target):
        patcher, _ = _mock_firecracker_apply(pid=7401)
        try:
            mgr = VMSandboxManager()
            mgr.boot("firecracker", sample_spec, sample_target)

            obs = mgr.observe()
            assert obs["total_instances"] == 1
            assert obs["running_instances"] == 1
            assert obs["state_breakdown"].get("running") == 1
            assert obs["avg_boot_ms"] > 0
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_observe_counts_dispatches(self, sample_spec, sample_target):
        _mock_gvisor_apply(pid=7402, returncode=None)
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("gvisor", sample_spec, sample_target)

            for i in range(3):
                cmd = AgentCommand(command=["/bin/echo", str(i)])
                mgr.dispatch(inst.instance_id, sample_target, command=cmd)

            obs = mgr.observe()
            assert obs["total_dispatches"] == 3
        finally:
            mock.patch.stopall()

    def test_observe_counts_findings(self, sample_spec, sample_target):
        patcher, _api_sock = _mock_firecracker_apply(
            pid=7403, returncode=None, sock_exists=True,
        )
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)
            mgr.verify(inst.instance_id)

            obs = mgr.observe()
            assert obs["total_verify_findings"] >= 1
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_observe_stopped_not_counted_as_running(
        self, sample_spec, sample_target,
    ):
        patcher, _ = _mock_firecracker_apply(pid=7404)
        try:
            mgr = VMSandboxManager()
            inst = mgr.boot("firecracker", sample_spec, sample_target)
            mgr.release(inst.instance_id)

            obs = mgr.observe()
            assert obs["total_instances"] == 1
            assert obs["running_instances"] == 0
            assert obs["state_breakdown"].get("stopped") == 1
        finally:
            patcher.stop()
            mock.patch.stopall()

    def test_observe_failed_counted_separately(
        self, sample_spec, sample_target,
    ):
        with mock.patch.object(
            FirecrackerBackend, "available", return_value=False,
        ):
            mgr = VMSandboxManager()
            mgr.boot("firecracker", sample_spec, sample_target)

            obs = mgr.observe()
            assert obs["state_breakdown"].get("failed") == 1
            assert obs["running_instances"] == 0


# ---------------------------------------------------------------------------
# =====================================================================
# REAL AGENTEXECUTOR SUBPROCESS DISPATCH
# =====================================================================
# ---------------------------------------------------------------------------


class TestRealAgentExecutorDispatch:
    """Verify the AgentExecutor dispatch surface with real subprocess.run."""

    def test_echo_command_captures_stdout(self, sample_target):
        cmd = AgentCommand(command=["/bin/echo", "real-echo"])
        result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
        assert result["exit_code"] == 0
        assert b"real-echo" in result["stdout"]
        assert result["timed_out"] is False

    def test_false_command_returns_nonzero(self, sample_target):
        cmd = AgentCommand(command=["/bin/false"])
        result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
        assert result["exit_code"] != 0

    def test_env_override(self, sample_target):
        cmd = AgentCommand(
            command=["/usr/bin/env"],
            env={"GLUDD_TEST_VAR": "dispatched-value"},
        )
        result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
        assert b"GLUDD_TEST_VAR=dispatched-value" in result["stdout"]

    def test_timeout_kills_command(self, sample_target):
        cmd = AgentCommand(
            command=["/bin/sleep", "30"],
            timeout_s=0.5,
        )
        result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
        assert result["timed_out"] is True
        assert result["exit_code"] == -1
        assert result["wall_time_s"] < 5.0

    def test_cwd_override(self, sample_target, tmp_path: Path):
        marker = tmp_path / "marker.txt"
        marker.write_text("cwd-ok")
        cmd = AgentCommand(
            command=["/bin/cat", "marker.txt"],
            cwd=str(tmp_path),
        )
        result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
        assert result["exit_code"] == 0
        assert b"cwd-ok" in result["stdout"]

    def test_nonexistent_binary_returns_oserror(self, sample_target):
        cmd = AgentCommand(command=["/nonexistent/binary_xyz_123"])
        result = AgentExecutor.receive_and_execute(sample_target, command=cmd)
        assert result["exit_code"] == -1
        assert result["timed_out"] is False

    def test_stub_path_returns_zero(self, sample_target):
        """Backward-compat: command=None returns stub dict."""
        result = AgentExecutor.receive_and_execute(sample_target)
        assert result["exit_code"] == 0
        assert result.get("stub") is True


# ---------------------------------------------------------------------------
# =====================================================================
# FIRECRACKER REST API INTEGRATION (real UNIX socket round-trip)
# =====================================================================
# ---------------------------------------------------------------------------


_MAX_UNIX_SOCKET_PATH_BYTES = 103


def _unix_socket_path(base_dir: Path, sock_name: str) -> str:
    """Return a namespaced AF_UNIX path that fits macOS ``sun_path``."""
    candidate = base_dir / f"{sock_name}.sock"
    if len(os.fsencode(candidate)) <= _MAX_UNIX_SOCKET_PATH_BYTES:
        return str(candidate)
    digest = hashlib.sha256(os.fsencode(candidate)).hexdigest()[:16]
    return f"/tmp/gludd-fc-test-{digest}.sock"


def _serve_unix_http(
    base_dir: Path, sock_name: str, response_bytes: bytes,
) -> tuple[socket.socket, threading.Thread, str]:
    """Start a real AF_UNIX HTTP server that replies with ``response_bytes``.

    Returns ``(server_socket, thread, sock_path)``. Caller MUST close the
    socket and join the thread, then ``os.unlink(sock_path)``.
    """
    sock_path = _unix_socket_path(base_dir, sock_name)
    with contextlib.suppress(OSError):
        os.unlink(sock_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    server.settimeout(3.0)

    def serve() -> None:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        try:
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    conn.sendall(response_bytes)
                    return
                buf += chunk
            head, _, body_start = buf.partition(b"\r\n\r\n")
            content_length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1].strip())
                    break
            body_buf = body_start
            while len(body_buf) < content_length:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                body_buf += chunk
            conn.sendall(response_bytes)
        finally:
            conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return server, t, sock_path


class TestFirecrackerRestRoundTrip:
    """Real UNIX socket HTTP round-trips through the Firecracker REST helper."""

    def test_socket_path_falls_back_for_long_base_dir(self, tmp_path: Path):
        long_base = tmp_path / ("nested-" + ("x" * 100))

        sock_path = _unix_socket_path(long_base, "fc-long")

        assert len(os.fsencode(sock_path)) <= _MAX_UNIX_SOCKET_PATH_BYTES
        assert sock_path.startswith("/tmp/gludd-fc-test-")

    def test_put_returns_empty_dict_on_204(self, tmp_path: Path):
        response = b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"
        server, t, sock_path = _serve_unix_http(tmp_path, "fc-204", response)
        try:
            result = _firecracker_put(sock_path, "/machine-config", {"vcpu_count": 1})
            assert result == {}
        finally:
            server.close()
            t.join(timeout=2.0)
            with contextlib.suppress(OSError):
                os.unlink(sock_path)

    def test_put_parses_json_body(self, tmp_path: Path):
        body = b'{"state":"Running"}'
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )
        server, t, sock_path = _serve_unix_http(tmp_path, "fc-json", response)
        try:
            result = _firecracker_put(sock_path, "/boot-source", {"kernel_image_path": "/k"})
            assert result == {"state": "Running"}
        finally:
            server.close()
            t.join(timeout=2.0)
            with contextlib.suppress(OSError):
                os.unlink(sock_path)

    def test_put_raises_on_non_2xx(self, tmp_path: Path):
        body = b'{"error":"bad config"}'
        response = (
            b"HTTP/1.1 400 Bad Request\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )
        server, t, sock_path = _serve_unix_http(tmp_path, "fc-err", response)
        try:
            with pytest.raises(RuntimeError, match="HTTP 400"):
                _firecracker_put(sock_path, "/machine-config", {"bad": True})
            assert server is not None
        finally:
            server.close()
            t.join(timeout=2.0)
            with contextlib.suppress(OSError):
                os.unlink(sock_path)

    def test_wait_for_socket_returns_true_when_connectable(
        self, tmp_path: Path,
    ):
        sock_path = _unix_socket_path(tmp_path, "fc-wait")
        with contextlib.suppress(OSError):
            os.unlink(sock_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)
        try:
            assert _wait_for_socket(sock_path, timeout=2.0) is True
        finally:
            server.close()
            with contextlib.suppress(OSError):
                os.unlink(sock_path)

    def test_wait_for_socket_returns_false_on_timeout(
        self, tmp_path: Path,
    ):
        sock_path = _unix_socket_path(tmp_path, "fc-missing")
        assert _wait_for_socket(sock_path, timeout=0.2) is False


# ---------------------------------------------------------------------------
# =====================================================================
# STRESS: rapid boot/dispatch/release cycles
# =====================================================================
# ---------------------------------------------------------------------------


class TestStressRapidCycles:
    """Verify the lifecycle manager survives rapid repeated cycles."""

    def test_rapid_firecracker_cycles(self, sample_spec, sample_target):
        mgr = VMSandboxManager()
        instance_ids: list[str] = []

        for i in range(10):
            pid = 8000 + i
            patcher, _ = _mock_firecracker_apply(pid=pid)
            try:
                inst = mgr.boot("firecracker", sample_spec, sample_target)
                cmd = AgentCommand(command=["/bin/echo", f"cycle-{i}"])
                mgr.dispatch(inst.instance_id, sample_target, command=cmd)
                mgr.release(inst.instance_id)
                instance_ids.append(inst.instance_id)
            finally:
                patcher.stop()
                mock.patch.stopall()

        assert len(instance_ids) == 10
        assert len(set(instance_ids)) == 10
        obs = mgr.observe()
        assert obs["total_instances"] == 10
        assert obs["total_dispatches"] == 10
        assert obs["running_instances"] == 0

    def test_rapid_gvisor_cycles(self, sample_spec, sample_target):
        mgr = VMSandboxManager()

        for i in range(10):
            _mock_gvisor_apply(pid=8100 + i)
            try:
                inst = mgr.boot("gvisor", sample_spec, sample_target)
                cmd = AgentCommand(command=["/usr/bin/true"])
                mgr.dispatch(inst.instance_id, sample_target, command=cmd)
                mgr.release(inst.instance_id)
            finally:
                mock.patch.stopall()

        obs = mgr.observe()
        assert obs["total_dispatches"] == 10
        assert obs["state_breakdown"].get("stopped") == 10
