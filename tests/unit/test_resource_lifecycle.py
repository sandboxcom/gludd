"""Unit tests for ResourceLifecycleManager — cross-provider resource lifecycle."""

from __future__ import annotations

import io
import logging
import os
import signal
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import general_ludd.cloud.resource_lifecycle as resource_lifecycle
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

    def test_exact_identity_reregister_replaces(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.register("azure", "vm-001", "/tmp/deploy-002")
        tracked = manager.all_tracked()
        assert len(tracked) == 1
        assert tracked[0].provider == "azure"
        assert tracked[0].deploy_dir == "/tmp/deploy-002"

    def test_same_instance_id_different_providers_do_not_collide(
        self,
        manager: ResourceLifecycleManager,
    ) -> None:
        manager.register("azure", "shared-id", "/tmp/azure")
        manager.register("aws", "shared-id", "/tmp/aws")

        assert len(manager.all_tracked()) == 2

    def test_double_deregister_is_noop(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.deregister("vm-001")
        manager.deregister("vm-001")

    def test_project_provider_instance_identity_prevents_cross_project_overwrite(
        self,
        manager: ResourceLifecycleManager,
    ) -> None:
        manager.register(
            "azure",
            "shared-id",
            "/tmp/project-a",
            project_id="project-a",
        )
        manager.register(
            "azure",
            "shared-id",
            "/tmp/project-b",
            project_id="project-b",
        )

        assert len(manager.all_tracked()) == 2
        assert manager.pending_cleanup(project_id="project-a")[0]["deploy_dir"] == (
            "/tmp/project-a"
        )

    def test_ambiguous_deregister_requires_project_scope(
        self,
        manager: ResourceLifecycleManager,
    ) -> None:
        manager.register("azure", "shared-id", "/tmp/a", project_id="project-a")
        manager.register("azure", "shared-id", "/tmp/b", project_id="project-b")

        with pytest.raises(ValueError, match="ambiguous"):
            manager.deregister("shared-id")

        manager.deregister(
            "shared-id",
            provider="azure",
            project_id="project-a",
        )
        assert manager.is_tracked("shared-id", project_id="project-b")

    def test_touch_requires_exact_identity_and_updates_only_that_resource(
        self,
        manager: ResourceLifecycleManager,
    ) -> None:
        manager.register("azure", "shared", "/tmp/a", project_id="project-a")
        manager.register("azure", "shared", "/tmp/b", project_id="project-b")
        before = {
            resource.project_id: resource.last_activity
            for resource in manager.all_tracked()
        }

        with pytest.raises(ValueError, match="ambiguous resource identity"):
            manager.touch("shared")
        with pytest.raises(KeyError, match="missing"):
            manager.touch("missing", provider="azure", project_id="project-a")

        manager.touch("shared", provider="azure", project_id="project-a")
        after = {
            resource.project_id: resource.last_activity
            for resource in manager.all_tracked()
        }
        assert after["project-a"] >= before["project-a"]
        assert after["project-b"] == before["project-b"]


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
    def test_cleanup_without_destroy_owner_fails_closed(
        self,
        manager: ResourceLifecycleManager,
    ) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")

        assert manager.cleanup_all() == 0
        assert manager.is_tracked("vm-001")

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

    def test_cleanup_project_only_destroys_that_projects_resources(
        self,
        manager: ResourceLifecycleManager,
    ) -> None:
        destroyed: list[str] = []
        manager.set_destroy_fn(lambda instance_id, _deploy_dir: destroyed.append(instance_id))
        manager.register("azure", "vm-a", "/tmp/a", project_id="project-a")
        manager.register("azure", "vm-b", "/tmp/b", project_id="project-b")

        assert manager.cleanup_project("project-a") == 1
        assert destroyed == ["vm-a"]
        assert not manager.is_tracked("vm-a", project_id="project-a")
        assert manager.is_tracked("vm-b", project_id="project-b")

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

        manager.all_tracked(provider="azure")[0].last_activity = time.time() - 3600

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

        manager.all_tracked(provider="azure")[0].last_activity = time.time() - 3600

        count = manager.cleanup_idle("aws", idle_minutes=10)
        assert count == 0


class TestCostEstimate:
    def test_cost_estimate_zero_when_empty(self, manager: ResourceLifecycleManager) -> None:
        assert manager.cost_estimate() == 0.0

    def test_cost_estimate_positive_with_resources(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.all_tracked(provider="azure")[0].registered_at = time.time() - 1800

        cost = manager.cost_estimate()
        assert cost > 0.0

    def test_cost_estimate_skips_cleaned_up(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        resource = manager.all_tracked(provider="azure")[0]
        resource.registered_at = time.time() - 3600
        resource.cleaned_up = True

        assert manager.cost_estimate() == 0.0

    def test_cost_estimate_with_provider_filter(self, manager: ResourceLifecycleManager) -> None:
        manager.register("azure", "vm-001", "/tmp/deploy-001")
        manager.register("aws", "i-002", "/tmp/deploy-002")
        manager.all_tracked(provider="azure")[0].registered_at = time.time() - 1800
        manager.all_tracked(provider="aws")[0].registered_at = time.time() - 1800

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

    def test_atexit_warning_survives_a_closed_capture_stream(
        self,
        monkeypatch: Any,
        manager: ResourceLifecycleManager,
    ) -> None:
        """Late cleanup must remain observable after pytest closes capture."""
        capture_stream = io.StringIO()
        capture_handler = logging.StreamHandler(capture_stream)
        isolated_logger = logging.Logger("resource-lifecycle-atexit-test")
        isolated_logger.propagate = False
        isolated_logger.addHandler(capture_handler)
        capture_stream.close()

        cleaned: list[str] = []
        manager.set_destroy_fn(lambda instance_id, _deploy_dir: cleaned.append(instance_id))
        manager.register("azure", "vm-after-capture", "/tmp/deploy-after-capture")

        raw_stderr_write = MagicMock()
        monkeypatch.setattr(resource_lifecycle, "logger", isolated_logger)
        monkeypatch.setattr(os, "write", raw_stderr_write)

        manager._guaranteed_cleanup()

        assert cleaned == ["vm-after-capture"]
        raw_stderr_write.assert_called_once()
        fd, payload = raw_stderr_write.call_args.args
        assert fd == 2
        assert b"Guaranteed cleanup triggered" in payload

    def test_late_shutdown_handler_tolerates_unavailable_stderr(
        self,
        monkeypatch: Any,
    ) -> None:
        """Cleanup logging must not mask teardown when fd 2 is unavailable."""
        raw_stderr_write = MagicMock(side_effect=OSError("stderr unavailable"))
        monkeypatch.setattr(os, "write", raw_stderr_write)
        handler = resource_lifecycle._ShutdownStderrHandler()

        handler.emit(logging.makeLogRecord({"msg": "late cleanup"}))

        raw_stderr_write.assert_called_once()

    def test_closed_stream_probe_stops_at_nonpropagating_logger(self) -> None:
        """An isolated live logger must not inspect unrelated root handlers."""
        isolated_logger = logging.Logger("resource-lifecycle-isolated-live")
        isolated_logger.propagate = False

        assert resource_lifecycle._has_closed_log_stream(isolated_logger) is False

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

    def test_signal_handler_chains_existing_runtime_handler(
        self,
        monkeypatch: Any,
        manager: ResourceLifecycleManager,
    ) -> None:
        chained_handler = MagicMock()
        manager._previous_signal_handlers[15] = chained_handler
        mock_kill = MagicMock()
        monkeypatch.setattr(os, "kill", mock_kill)

        manager._handle_signal(15, None)

        chained_handler.assert_called_once_with(15, None)
        mock_kill.assert_not_called()

    def test_signal_handler_respects_preexisting_ignore(
        self,
        monkeypatch: Any,
        manager: ResourceLifecycleManager,
    ) -> None:
        manager._previous_signal_handlers[signal.SIGTERM] = signal.SIG_IGN
        mock_kill = MagicMock()
        monkeypatch.setattr(os, "kill", mock_kill)

        manager._handle_signal(signal.SIGTERM, None)

        mock_kill.assert_not_called()

    def test_partial_signal_install_rolls_back_every_installed_handler(
        self,
        monkeypatch: Any,
        manager: ResourceLifecycleManager,
    ) -> None:
        """A failed second install must not leave SIGTERM pointing at this manager."""
        previous_term = MagicMock()
        previous_int = MagicMock()
        installed: dict[int, object] = {
            signal.SIGTERM: previous_term,
            signal.SIGINT: previous_int,
        }
        install_attempts = 0

        def fake_getsignal(signum: int) -> object:
            return installed[signum]

        def fake_signal(signum: int, handler: object) -> object:
            nonlocal install_attempts
            install_attempts += 1
            if signum == signal.SIGINT and handler == manager._handle_signal:
                raise ValueError("forced partial install")
            old = installed[signum]
            installed[signum] = handler
            return old

        monkeypatch.setattr(resource_lifecycle, "_signal_handlers_installed", False)

        with (
            patch.object(signal, "getsignal", side_effect=fake_getsignal),
            patch.object(signal, "signal", side_effect=fake_signal),
        ):
            assert resource_lifecycle._install_signal_handlers(manager) is False

        assert install_attempts == 3
        assert installed[signal.SIGTERM] is previous_term
        assert installed[signal.SIGINT] is previous_int
        assert manager._previous_signal_handlers == {}
        assert resource_lifecycle._signal_handlers_installed is False

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
