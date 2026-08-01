"""Unit tests for ResourceLifecycleManager — cross-provider resource lifecycle."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.cloud.resource_lifecycle import (
    LIFECYCLE_TARGETS,
    ResourceLifecycleManager,
    TrackedResource,
    get_lifecycle,
)


@pytest.fixture
def manager() -> ResourceLifecycleManager:
    mgr = ResourceLifecycleManager()
    return mgr


@pytest.fixture
def tmp_deploy_dir() -> str:
    return tempfile.mkdtemp(prefix="gludd-test-deploy-")


class TestRegisterAndDeregister:
    def test_register_adds_to_tracked(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        assert manager.is_tracked("vm-001")

    def test_register_different_providers(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.register("aws", "i-002", "/tmp/deploy-002")
        manager.register("gcp", "gcp-003", "/tmp/deploy-003")
        assert manager.is_tracked("vm-001")
        assert manager.is_tracked("i-002")
        assert manager.is_tracked("gcp-003")

    def test_deregister_removes_from_tracked(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.deregister("vm-001")
        assert not manager.is_tracked("vm-001")

    def test_deregister_nonexistent_is_noop(self, manager: ResourceLifecycleManager) -> None:
        manager.deregister("nonexistent")

    def test_double_register_replaces(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.register("aws", "vm-001", "/tmp/deploy-002")
        tracked = manager.all_tracked()
        assert len(tracked) == 1
        assert tracked[0].provider == "aws"
        assert tracked[0].deploy_dir == "/tmp/deploy-002"

    def test_double_deregister_is_noop(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.deregister("vm-001")
        manager.deregister("vm-001")


class TestPendingCleanup:
    def test_pending_cleanup_after_register(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        pending = manager.pending_cleanup()
        assert len(pending) == 1
        assert pending[0]["instance_id"] == "vm-001"
        assert pending[0]["provider"] == "azure"

    def test_pending_cleanup_empty_after_deregister(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.deregister("vm-001")
        assert len(manager.pending_cleanup()) == 0

    def test_pending_cleanup_excludes_cleaned_up(self, manager: ResourceLifecycleManager, tmp_deploy_dir: str) -> None:
        manager.register("azure", "vm-001", tmp_deploy_dir)
        manager.register("aws", "i-002", tmp_deploy_dir)
        manager.deregister("i-002")
        pending = manager.pending_cleanup()
        assert len(pending) == 1
        assert pending[0]["instance_id"] == "vm-001"

    def test_pending_cleanup_empty_when_nothing_registered(self, manager: ResourceLifecycleManager) -> None:
        assert len(manager.pending_cleanup()) == 0


class TestCleanupAll:
    def test_cleanup_all_calls_destroy(self, manager: ResourceLifecycleManager) -> None:
        destroyed: list[str] = []

        def _destroy(instance_id: str, _deploy_dir: str) -> None:
            destroyed.append(instance_id)

        manager.set_destroy_fn(_destroy)
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.register("aws", "i-002", "/tmp/deploy-002")

        count = manager.cleanup_all()
        assert count == 2
        assert sorted(destroyed) == ["i-002", "vm-001"]
        assert not manager.is_tracked("vm-001")
        assert not manager.is_tracked("i-002")

    def test_cleanup_all_empty_manager(self, manager: ResourceLifecycleManager) -> None:
        assert manager.cleanup_all() == 0

    def test_cleanup_all_provider_filter(self, manager: ResourceLifecycleManager) -> None:
        destroyed: list[str] = []

        def _destroy(instance_id: str, _deploy_dir: str) -> None:
            destroyed.append(instance_id)

        manager.set_destroy_fn(_destroy)
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.register("aws", "i-002", "/tmp/deploy-002")

        count = manager.cleanup_all(provider="azure")
        assert count == 1
        assert destroyed == ["vm-001"]

    def test_cleanup_all_resilient_to_destroy_failure(self, manager: ResourceLifecycleManager) -> None:
        destroyed: list[str] = []

        def _destroy(instance_id: str, _deploy_dir: str) -> None:
            if instance_id == "vm-001":
                raise RuntimeError("simulated failure")
            destroyed.append(instance_id)

        manager.set_destroy_fn(_destroy)
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.register("aws", "i-002", "/tmp/deploy-002")

        count = manager.cleanup_all()
        assert count == 1
        assert destroyed == ["i-002"]
        assert not manager.is_tracked("i-002")


class TestCleanupIdle:
    def test_idle_detection(self, manager: ResourceLifecycleManager) -> None:
        destroyed: list[str] = []

        def _destroy(instance_id: str, _deploy_dir: str) -> None:
            destroyed.append(instance_id)

        manager.set_destroy_fn(_destroy)
        manager.register("azure", "vm-001", "/tmp/deploy-001")

        with manager._lock:
            manager._resources["vm-001"].last_activity = time.time() - 3600

        count = manager.cleanup_idle("azure", idle_minutes=10)
        assert count == 1
        assert destroyed == ["vm-001"]

    def test_non_idle_skipped(self, manager: ResourceLifecycleManager) -> None:
        destroyed: list[str] = []

        def _destroy(instance_id: str, _deploy_dir: str) -> None:
            destroyed.append(instance_id)

        manager.set_destroy_fn(_destroy)
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        count = manager.cleanup_idle("azure", idle_minutes=60)
        assert count == 0
        assert destroyed == []

    def test_idle_respects_provider_filter(self, manager: ResourceLifecycleManager) -> None:
        destroyed: list[str] = []

        def _destroy(instance_id: str, _deploy_dir: str) -> None:
            destroyed.append(instance_id)

        manager.set_destroy_fn(_destroy)
        manager.register("azure", "vm-001", "/tmp/deploy-001")

        with manager._lock:
            manager._resources["vm-001"].last_activity = time.time() - 3600

        count = manager.cleanup_idle("aws", idle_minutes=10)
        assert count == 0


class TestCostEstimate:
    def test_cost_estimate_zero_when_empty(self, manager: ResourceLifecycleManager) -> None:
        assert manager.cost_estimate() == 0.0

    def test_cost_estimate_positive_with_resources(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        with manager._lock:
            manager._resources["vm-001"].registered_at = time.time() - 1800

        cost = manager.cost_estimate()
        assert cost > 0.0

    def test_cost_estimate_skips_cleaned_up(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        with manager._lock:
            manager._resources["vm-001"].registered_at = time.time() - 3600
            manager._resources["vm-001"].cleaned_up = True

        assert manager.cost_estimate() == 0.0

    def test_cost_estimate_with_provider_filter(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.register("aws", "i-002", "/tmp/deploy-002")
        with manager._lock:
            manager._resources["vm-001"].registered_at = time.time() - 1800
            manager._resources["i-002"].registered_at = time.time() - 1800

        cost = manager.cost_estimate(provider="aws")
        assert cost > 0.0


class TestOrphanReport:
    def test_orphan_report_empty_when_clean(self, manager: ResourceLifecycleManager) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "deployed.txt").write_text("ok")
            manager.register("azure", "vm-001", d)
            orphans = manager.orphan_report()
            assert len(orphans) == 0

    def test_orphan_report_with_missing_dir(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/nonexistent/dir/12345")
        orphans = manager.orphan_report()
        assert len(orphans) == 1
        assert orphans[0]["instance_id"] == "vm-001"
        assert "missing" in orphans[0]["reason"]

    def test_orphan_report_empty_dir(self, manager: ResourceLifecycleManager) -> None:
        with tempfile.TemporaryDirectory() as d:
            empty_subdir = os.path.join(d, "empty")
            os.makedirs(empty_subdir)
            manager.register("azure", "vm-001", empty_subdir)
            orphans = manager.orphan_report()
            assert len(orphans) == 1
            assert orphans[0]["reason"] == "deploy directory empty"

    def test_orphan_report_skips_cleaned_up(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/nonexistent/dir/12345")
        manager.deregister("vm-001")
        orphans = manager.orphan_report()
        assert len(orphans) == 0


class TestBackgroundThread:
    def test_background_thread_starts_and_stops(self, manager: ResourceLifecycleManager) -> None:
        manager.start_background_poll()
        assert manager._poll_thread is not None
        assert manager._poll_thread.is_alive()
        manager.stop_background_poll()
        assert manager._poll_thread is None

    def test_start_background_poll_idempotent(self, manager: ResourceLifecycleManager) -> None:
        manager.start_background_poll()
        first = manager._poll_thread
        manager.start_background_poll()
        assert manager._poll_thread is first
        manager.stop_background_poll()


class TestSignalAndAtexitHandlers:
    def test_atexit_handler_attempts_cleanup(self, manager: ResourceLifecycleManager) -> None:
        cleaned = []

        def _destroy(instance_id: str, _deploy_dir: str) -> None:
            cleaned.append(instance_id)

        manager.set_destroy_fn(_destroy)
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager._guaranteed_cleanup()
        assert "vm-001" in cleaned

    def test_signal_handler_cleanup_then_kill(self, monkeypatch: Any, manager: ResourceLifecycleManager) -> None:
        cleaned = []

        def _destroy(instance_id: str, _deploy_dir: str) -> None:
            cleaned.append(instance_id)

        manager.set_destroy_fn(_destroy)
        manager.register("azure", "vm-001", "/tmp/deploy-001")

        mock_kill = MagicMock()
        monkeypatch.setattr(os, "kill", mock_kill)
        manager._handle_signal(15, None)
        assert "vm-001" in cleaned
        mock_kill.assert_called_once()

    def test_singleton_imports_signal_and_atexit(self) -> None:
        mgr = get_lifecycle()
        assert mgr is not None
        mgr2 = get_lifecycle()
        assert mgr is mgr2


class TestLifecycleTargets:
    def test_lifecycle_targets_for_all_providers(self) -> None:
        providers = {"azure", "aws", "gcp", "runpod"}
        assert set(LIFECYCLE_TARGETS.keys()) == providers

    def test_lifecycle_targets_have_validator_script(self) -> None:
        for _provider, target in LIFECYCLE_TARGETS.items():
            assert "validator_script" in target


class TestTrackedResource:
    def test_tracked_resource_defaults(self) -> None:
        tr = TrackedResource(provider="azure", instance_id="vm-001", deploy_dir="/tmp/d")
        assert tr.provider == "azure"
        assert tr.instance_id == "vm-001"
        assert tr.deploy_dir == "/tmp/d"
        assert not tr.cleaned_up
        assert tr.registered_at > 0
