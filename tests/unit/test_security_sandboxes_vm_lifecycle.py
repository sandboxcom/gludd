"""Tests for security/sandboxes/vm/lifecycle.py — VMSandboxManager."""

from __future__ import annotations

import pytest

import general_ludd.security.sandboxes.vm.lifecycle as lifecycle_mod
from general_ludd.security.permissions import default_spec
from general_ludd.security.sandboxes import SandboxHandle, SandboxTarget
from general_ludd.security.sandboxes.vm.lifecycle import (
    VMLifecycleState,
    VMMetrics,
    VMSandboxManager,
    _resolve_backend,
)


class _FakeBackend:
    """Minimal backend double satisfying the lifecycle contract."""

    is_available = True
    apply_succeeds = True

    @classmethod
    def available(cls) -> bool:
        return cls.is_available

    @classmethod
    def apply(cls, spec: object, target: object) -> SandboxHandle:
        return SandboxHandle(
            backend="fake",
            token="gludd-test",
            applied=cls.apply_succeeds,
        )

    @classmethod
    def verify(cls, spec: object, handle: object) -> list[object]:
        return []

    @classmethod
    def release(cls, handle: object) -> None:
        return None


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> VMSandboxManager:
    _FakeBackend.is_available = True
    _FakeBackend.apply_succeeds = True
    monkeypatch.setattr(
        lifecycle_mod, "_resolve_backend", lambda name: _FakeBackend
    )
    return VMSandboxManager()


class TestResolveBackend:
    def test_unknown_backend_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown backend"):
            _resolve_backend("bogus")


class TestBoot:
    def test_boot_registers_running_instance(
        self, manager: VMSandboxManager
    ) -> None:
        instance = manager.boot(
            "fake", default_spec("build"), SandboxTarget()
        )

        assert instance.state is VMLifecycleState.RUNNING
        assert instance.instance_id in manager.instances
        assert instance.metrics.boot_ms >= 0.0

    def test_boot_unavailable_backend_records_failed(
        self, manager: VMSandboxManager
    ) -> None:
        _FakeBackend.is_available = False

        instance = manager.boot(
            "fake", default_spec("build"), SandboxTarget()
        )

        assert instance.state is VMLifecycleState.FAILED
        assert instance.instance_id in manager.instances
        assert any(e["event"] == "boot_failed" for e in manager.events)

    def test_boot_apply_failure_records_failed(
        self, manager: VMSandboxManager
    ) -> None:
        _FakeBackend.apply_succeeds = False

        instance = manager.boot(
            "fake", default_spec("build"), SandboxTarget()
        )

        assert instance.state is VMLifecycleState.FAILED

    def test_booted_event_emitted_on_success(
        self, manager: VMSandboxManager
    ) -> None:
        manager.boot("fake", default_spec("build"), SandboxTarget())

        assert any(e["event"] == "booted" for e in manager.events)


class TestDispatchAndRelease:
    def test_dispatch_unknown_instance_raises_key_error(
        self, manager: VMSandboxManager
    ) -> None:
        with pytest.raises(KeyError):
            manager.dispatch("vm-missing", SandboxTarget())

    def test_dispatch_on_failed_instance_raises_runtime_error(
        self, manager: VMSandboxManager
    ) -> None:
        _FakeBackend.is_available = False
        instance = manager.boot(
            "fake", default_spec("build"), SandboxTarget()
        )

        with pytest.raises(RuntimeError, match="not running"):
            manager.dispatch(instance.instance_id, SandboxTarget())

    def test_release_transitions_to_stopped_and_is_idempotent(
        self, manager: VMSandboxManager
    ) -> None:
        instance = manager.boot(
            "fake", default_spec("build"), SandboxTarget()
        )

        first = manager.release(instance.instance_id)
        second = manager.release(instance.instance_id)

        assert first["state"] == VMLifecycleState.STOPPED.value
        assert second["state"] == VMLifecycleState.STOPPED.value
        assert instance.stopped_at > 0.0


class TestObserve:
    def test_observe_empty_manager(self, manager: VMSandboxManager) -> None:
        stats = manager.observe()

        assert stats["total_instances"] == 0
        assert stats["running_instances"] == 0
        assert stats["avg_boot_ms"] == 0.0

    def test_observe_aggregates_state_breakdown(
        self, manager: VMSandboxManager
    ) -> None:
        manager.boot("fake", default_spec("build"), SandboxTarget())
        _FakeBackend.is_available = False
        manager.boot("fake", default_spec("build"), SandboxTarget())

        stats = manager.observe()

        assert stats["total_instances"] == 2
        assert stats["running_instances"] == 1
        assert stats["state_breakdown"]["running"] == 1
        assert stats["state_breakdown"]["failed"] == 1

    def test_list_instances_returns_all(
        self, manager: VMSandboxManager
    ) -> None:
        manager.boot("fake", default_spec("build"), SandboxTarget())

        assert len(manager.list_instances()) == 1


class TestVMMetrics:
    def test_defaults_are_zeroed(self) -> None:
        metrics = VMMetrics()

        assert metrics.boot_ms == 0.0
        assert metrics.dispatch_count == 0
        assert metrics.last_verify_findings == 0
