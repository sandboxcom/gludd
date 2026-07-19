"""Unit tests for VM sandbox lifecycle manager — P3 (daemon dispatch + observability).

Covers: VMSandboxManager boot/dispatch/verify/release/list/observe cycle,
VMInstance state machine, VMMetrics aggregation, event emission, image
build precondition, fail-open on backend unavailable, and concurrent
instance isolation.
"""

from __future__ import annotations

from unittest import mock

import pytest

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import (
    Finding,
    SandboxHandle,
    SandboxTarget,
)


@pytest.fixture(autouse=True)
def _isolate_cache_dir(tmp_path, monkeypatch):
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


def _make_handle(applied: bool = True, backend: str = "firecracker") -> SandboxHandle:
    return SandboxHandle(
        backend=backend,
        token="gludd-test",
        applied=applied,
        extra={"stub": True} if applied else {"reason": "absent"},
    )


def test_lifecycle_module_exports_required_names():
    from general_ludd.security.sandboxes.vm import lifecycle

    for name in (
        "VMSandboxManager",
        "VMInstance",
        "VMMetrics",
        "VMLifecycleState",
    ):
        assert hasattr(lifecycle, name), f"lifecycle missing {name}"


def test_vm_lifecycle_state_values():
    from general_ludd.security.sandboxes.vm.lifecycle import VMLifecycleState

    assert VMLifecycleState.PENDING.value == "pending"
    assert VMLifecycleState.BOOTING.value == "booting"
    assert VMLifecycleState.RUNNING.value == "running"
    assert VMLifecycleState.EXECUTING.value == "executing"
    assert VMLifecycleState.STOPPED.value == "stopped"
    assert VMLifecycleState.FAILED.value == "failed"


def test_vm_metrics_defaults():
    from general_ludd.security.sandboxes.vm.lifecycle import VMMetrics

    m = VMMetrics()
    assert m.boot_ms == 0.0
    assert m.dispatch_count == 0
    assert m.peak_rss_kb == 0
    assert m.last_verify_findings == 0
    assert m.total_dispatch_ms == 0.0


def test_vm_instance_creation_defaults_state_pending(sample_spec):
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMInstance,
        VMLifecycleState,
    )

    inst = VMInstance(
        instance_id="vm-001",
        backend_name="firecracker",
        spec=sample_spec,
        handle=_make_handle(),
    )
    assert inst.state is VMLifecycleState.PENDING
    assert inst.started_at > 0
    assert inst.stopped_at == 0.0
    assert inst.metrics.boot_ms == 0.0


def test_manager_boot_transitions_to_running(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMLifecycleState,
        VMSandboxManager,
    )

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)
    assert inst.state is VMLifecycleState.RUNNING
    assert inst.metrics.boot_ms > 0
    assert inst.instance_id in mgr.instances


def test_manager_boot_records_failed_when_unavailable(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMLifecycleState,
        VMSandboxManager,
    )

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=False,
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)
    assert inst.state is VMLifecycleState.FAILED
    assert inst.handle.applied is False


def test_manager_boot_unknown_backend_raises(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with pytest.raises(ValueError, match="Unknown backend"):
        mgr.boot("hyperv", sample_spec, sample_target)


def test_manager_dispatch_transitions_to_executing(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMLifecycleState,
        VMSandboxManager,
    )

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    observed_states: list[VMLifecycleState] = []
    original = mgr.instances[inst.instance_id]

    def _spy_execute(target, command=None):
        observed_states.append(original.state)
        return {"exit_code": 0, "stub": True}

    with mock.patch(
        "general_ludd.security.sandboxes.vm.agent_executor.AgentExecutor.receive_and_execute",
        side_effect=_spy_execute,
    ):
        result = mgr.dispatch(inst.instance_id, sample_target)

    assert result["status"] == "executed"
    assert observed_states == [VMLifecycleState.EXECUTING]
    assert inst.state is VMLifecycleState.RUNNING
    assert inst.metrics.dispatch_count == 1
    assert inst.metrics.total_dispatch_ms >= 0.0


def test_manager_dispatch_increments_count_per_call(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    for _ in range(3):
        mgr.dispatch(inst.instance_id, sample_target)
    assert inst.metrics.dispatch_count == 3


def test_manager_dispatch_unknown_instance_raises(sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with pytest.raises(KeyError, match="not found"):
        mgr.dispatch("does-not-exist", sample_target)


def test_manager_verify_records_findings(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    fake_findings = [
        Finding(severity="warn", message="stub warning", capability=None),
    ]
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.verify",
        return_value=fake_findings,
    ):
        findings = mgr.verify(inst.instance_id)
    assert findings == fake_findings
    assert inst.metrics.last_verify_findings == 1


def test_manager_release_transitions_to_stopped(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMLifecycleState,
        VMSandboxManager,
    )

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release",
    ) as fake_release:
        result = mgr.release(inst.instance_id)
    assert inst.state is VMLifecycleState.STOPPED
    assert inst.stopped_at >= inst.started_at
    fake_release.assert_called_once()
    assert result["state"] == "stopped"


def test_manager_release_unknown_instance_raises():
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with pytest.raises(KeyError, match="not found"):
        mgr.release("nope")


def test_manager_list_instances_returns_all(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        mgr.boot("firecracker", sample_spec, sample_target)
        mgr.boot("firecracker", sample_spec, sample_target)
    assert len(mgr.list_instances()) == 2


def test_manager_observe_returns_aggregate_metrics(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst1 = mgr.boot("firecracker", sample_spec, sample_target)
        inst2 = mgr.boot("firecracker", sample_spec, sample_target)
        mgr.dispatch(inst1.instance_id, sample_target)
        mgr.dispatch(inst1.instance_id, sample_target)
        mgr.dispatch(inst2.instance_id, sample_target)

    snapshot = mgr.observe()
    assert snapshot["total_instances"] == 2
    assert snapshot["running_instances"] == 2
    assert snapshot["total_dispatches"] == 3
    assert snapshot["total_verify_findings"] == 0
    assert snapshot["avg_boot_ms"] >= 0.0
    assert "events_emitted" in snapshot


def test_manager_observe_empty_when_no_instances():
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    snap = mgr.observe()
    assert snap["total_instances"] == 0
    assert snap["running_instances"] == 0
    assert snap["total_dispatches"] == 0


def test_manager_emits_event_on_each_lifecycle_transition(
    sample_spec, sample_target,
):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release",
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)
        mgr.dispatch(inst.instance_id, sample_target)
        mgr.release(inst.instance_id)

    event_types = [e["event"] for e in mgr.events]
    assert "booted" in event_types
    assert "dispatched" in event_types
    assert "released" in event_types


def test_manager_event_has_required_fields(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        mgr.boot("firecracker", sample_spec, sample_target)

    event = mgr.events[0]
    for field in ("event", "instance_id", "backend", "ts", "spec"):
        assert field in event, f"event missing {field}"


def test_manager_with_gvisor_backend(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMLifecycleState,
        VMSandboxManager,
    )

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.gvisor_backend.GvisorBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.gvisor_backend.GvisorBackend.apply",
        return_value=_make_handle(applied=True, backend="gvisor"),
    ):
        inst = mgr.boot("gvisor", sample_spec, sample_target)
    assert inst.state is VMLifecycleState.RUNNING
    assert inst.backend_name == "gvisor"


def test_manager_image_path_passed_through(sample_spec, sample_target, tmp_path):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    image = tmp_path / "rootfs.ext4"
    image.write_bytes(b"\x00" * 1024)
    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ) as fake_apply:
        inst = mgr.boot("firecracker", sample_spec, sample_target, image_path=image)
    assert inst.image_path == image
    fake_apply.assert_called_once()


def test_manager_release_is_idempotent_on_already_stopped(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMLifecycleState,
        VMSandboxManager,
    )

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release",
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)
        mgr.release(inst.instance_id)
        result = mgr.release(inst.instance_id)
    assert result["state"] == VMLifecycleState.STOPPED.value


def test_manager_observe_per_state_breakdown(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release",
    ):
        inst1 = mgr.boot("firecracker", sample_spec, sample_target)
        mgr.boot("firecracker", sample_spec, sample_target)
        mgr.release(inst1.instance_id)

    snap = mgr.observe()
    assert snap["state_breakdown"]["stopped"] == 1
    assert snap["state_breakdown"]["running"] == 1


def test_manager_dispatch_on_stopped_instance_rejects(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release",
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)
        mgr.release(inst.instance_id)

    with pytest.raises(RuntimeError, match="not running"):
        mgr.dispatch(inst.instance_id, sample_target)


# ---------------------------------------------------------------------------
# snapshot() / restore() — NF.2 VM state save/restore
# ---------------------------------------------------------------------------


def test_snapshot_writes_file_and_returns_metadata(sample_spec, sample_target, tmp_path):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    dest = tmp_path / "snap.json"
    result = mgr.snapshot(inst.instance_id, dest)
    assert dest.exists()
    assert result["path"] == str(dest)
    assert result["size_bytes"] > 0
    assert len(result["sha256"]) == 64
    assert result["instance_id"] == inst.instance_id
    assert result["format_version"] >= 1


def test_snapshot_unknown_instance_raises(tmp_path):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with pytest.raises(KeyError, match="not found"):
        mgr.snapshot("nope", tmp_path / "snap.json")


def test_snapshot_rejects_non_running_instance(sample_spec, sample_target, tmp_path):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.release",
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)
        mgr.release(inst.instance_id)

    with pytest.raises(RuntimeError, match="not running"):
        mgr.snapshot(inst.instance_id, tmp_path / "snap.json")


def test_restore_creates_new_running_instance(sample_spec, sample_target, tmp_path):
    from general_ludd.security.sandboxes.vm.lifecycle import (
        VMLifecycleState,
        VMSandboxManager,
    )

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    dest = tmp_path / "snap.json"
    mgr.snapshot(inst.instance_id, dest)
    restored = mgr.restore(dest)

    assert restored.instance_id != inst.instance_id
    assert restored.state is VMLifecycleState.RUNNING
    assert restored.backend_name == "firecracker"
    assert restored.instance_id in mgr.instances


def test_restore_missing_file_raises(tmp_path):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with pytest.raises(FileNotFoundError):
        mgr.restore(tmp_path / "does-not-exist.json")


def test_restore_corrupt_file_raises(tmp_path):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    bad = tmp_path / "corrupt.json"
    bad.write_text("{ not valid json ")
    mgr = VMSandboxManager()
    with pytest.raises(ValueError, match="snapshot"):
        mgr.restore(bad)


def test_snapshot_restore_roundtrip_preserves_metrics_and_spec(
    sample_spec, sample_target, tmp_path,
):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.agent_executor.AgentExecutor.receive_and_execute",
        return_value={"exit_code": 0},
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)
        mgr.dispatch(inst.instance_id, sample_target)
        mgr.dispatch(inst.instance_id, sample_target)

    dest = tmp_path / "snap.json"
    mgr.snapshot(inst.instance_id, dest)
    restored = mgr.restore(dest)

    assert restored.metrics.dispatch_count == 2
    assert restored.spec.agent_type == inst.spec.agent_type
    assert restored.handle.token == inst.handle.token
    assert restored.handle.applied is inst.handle.applied


def test_snapshot_emits_event(sample_spec, sample_target, tmp_path):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    mgr.snapshot(inst.instance_id, tmp_path / "snap.json")
    event_types = [e["event"] for e in mgr.events]
    assert "snapshotted" in event_types


def test_restore_emits_event(sample_spec, sample_target, tmp_path):
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    dest = tmp_path / "snap.json"
    mgr.snapshot(inst.instance_id, dest)
    mgr.restore(dest)
    event_types = [e["event"] for e in mgr.events]
    assert "restored" in event_types


def test_snapshot_includes_vm_state_payload(sample_spec, sample_target, tmp_path):
    """vm_state (memory/registers/disk diff hook) round-trips through snapshot."""
    import json

    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    inst.vm_state = {
        "memory": b"\x00\x01\x02\x03",
        "registers": b"\xff\xee\xdd\xcc",
    }
    dest = tmp_path / "snap.json"
    mgr.snapshot(inst.instance_id, dest)
    payload = json.loads(dest.read_text())
    assert "vm_state" in payload
    assert "memory" in payload["vm_state"]
    assert "registers" in payload["vm_state"]


def test_restore_reconstructs_vm_state_bytes(sample_spec, sample_target, tmp_path):
    import json

    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    mgr = VMSandboxManager()
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.available",
        return_value=True,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerBackend.apply",
        return_value=_make_handle(applied=True),
    ):
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    inst.vm_state = {"memory": b"\xde\xad\xbe\xef", "registers": b"\x01\x02"}
    dest = tmp_path / "snap.json"
    mgr.snapshot(inst.instance_id, dest)
    restored = mgr.restore(dest)

    assert restored.vm_state["memory"] == b"\xde\xad\xbe\xef"
    assert restored.vm_state["registers"] == b"\x01\x02"
    # confirm it was actually base64-encoded in the file
    payload = json.loads(dest.read_text())
    assert payload["vm_state"]["memory"] != b"\xde\xad\xbe\xef"
