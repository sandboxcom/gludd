"""Tests for cloud resource lifecycle manager."""

from __future__ import annotations

import os
import signal
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

from general_ludd.cloud.resource_lifecycle import (
    LIFECYCLE_TARGETS,
    ResourceLifecycleManager,
    TrackedResource,
    get_lifecycle,
)


class TestTrackedResource:
    def test_default_construction(self):
        r = TrackedResource(provider="azure", instance_id="inst-1", deploy_dir="/tmp/d1")
        assert r.provider == "azure"
        assert r.instance_id == "inst-1"
        assert r.deploy_dir == "/tmp/d1"
        assert isinstance(r.registered_at, float)
        assert isinstance(r.last_activity, float)
        assert r.cleaned_up is False

    def test_registered_at_is_time_of_creation(self):
        before = time.time()
        r = TrackedResource(provider="aws", instance_id="inst-2", deploy_dir="/tmp/d2")
        after = time.time()
        assert before <= r.registered_at <= after

    def test_last_activity_equals_registered_at_initially(self):
        r = TrackedResource(provider="gcp", instance_id="inst-3", deploy_dir="/tmp/d3")
        assert abs(r.last_activity - r.registered_at) < 0.01

    def test_cleaned_up_defaults_false(self):
        r = TrackedResource(provider="runpod", instance_id="inst-4", deploy_dir="/tmp/d4")
        assert r.cleaned_up is False

    def test_explicit_fields_settable(self):
        r = TrackedResource(
            provider="azure",
            instance_id="inst-5",
            deploy_dir="/tmp/d5",
            registered_at=100.0,
            last_activity=200.0,
            cleaned_up=True,
        )
        assert r.registered_at == 100.0
        assert r.last_activity == 200.0
        assert r.cleaned_up is True


class TestResourceLifecycleManagerRegistration:
    def test_register_adds_resource(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        assert mgr.is_tracked("inst-1") is True

    def test_register_multiple_providers(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("aws", "inst-2", "/tmp/d2")
        mgr.register("gcp", "inst-3", "/tmp/d3")
        assert mgr.is_tracked("inst-1") is True
        assert mgr.is_tracked("inst-2") is True
        assert mgr.is_tracked("inst-3") is True

    def test_is_tracked_false_for_unknown(self):
        mgr = ResourceLifecycleManager()
        assert mgr.is_tracked("nonexistent") is False

    def test_deregister_removes_resource(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.deregister("inst-1")
        assert mgr.is_tracked("inst-1") is False

    def test_deregister_marks_cleaned_up(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.deregister("inst-1")
        orphans = mgr.orphan_report()
        assert len(orphans) == 0

    def test_deregister_nonexistent_does_not_raise(self):
        mgr = ResourceLifecycleManager()
        mgr.deregister("never-registered")

    def test_register_overwrite_reuses_key(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("aws", "inst-1", "/tmp/d2")
        tracked = mgr.all_tracked()
        assert len(tracked) == 1
        assert tracked[0].provider == "aws"


class TestResourceLifecycleManagerQueries:
    def test_pending_cleanup_empty_initially(self):
        mgr = ResourceLifecycleManager()
        assert mgr.pending_cleanup() == []

    def test_pending_cleanup_returns_registered(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        pending = mgr.pending_cleanup()
        assert len(pending) == 1
        assert pending[0]["provider"] == "azure"
        assert pending[0]["instance_id"] == "inst-1"
        assert pending[0]["deploy_dir"] == "/tmp/d1"
        assert "registered_at" in pending[0]

    def test_pending_cleanup_excludes_deregistered(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("aws", "inst-2", "/tmp/d2")
        mgr.deregister("inst-1")
        pending = mgr.pending_cleanup()
        assert len(pending) == 1
        assert pending[0]["instance_id"] == "inst-2"

    def test_all_tracked_empty_initially(self):
        mgr = ResourceLifecycleManager()
        assert mgr.all_tracked() == []

    def test_all_tracked_returns_all(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("aws", "inst-2", "/tmp/d2")
        tracked = mgr.all_tracked()
        assert len(tracked) == 2
        assert {r.instance_id for r in tracked} == {"inst-1", "inst-2"}

    def test_all_tracked_excludes_cleaned_up(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("aws", "inst-2", "/tmp/d2")
        mgr.deregister("inst-1")
        tracked = mgr.all_tracked()
        assert len(tracked) == 1
        assert tracked[0].instance_id == "inst-2"


class TestResourceLifecycleManagerCostEstimate:
    def test_cost_zero_for_no_resources(self):
        mgr = ResourceLifecycleManager()
        assert mgr.cost_estimate() == 0.0

    def test_cost_zero_immediately_after_register(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        cost = mgr.cost_estimate()
        assert cost == 0.0

    def test_cost_filter_by_provider(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("aws", "inst-2", "/tmp/d2")
        cost = mgr.cost_estimate(provider="aws")
        assert cost == 0.0  # just registered, no elapsed time

    def test_cost_filter_nonexistent_provider_returns_zero(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        cost = mgr.cost_estimate(provider="runpod")
        assert cost == 0.0

    def test_cost_rounds_to_4_decimal_places(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        cost = mgr.cost_estimate()
        assert cost == round(cost, 4)

    def test_cost_excludes_cleaned_up(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.deregister("inst-1")
        assert mgr.cost_estimate() == 0.0

    def test_cost_with_elapsed_time(self):
        mgr = ResourceLifecycleManager()
        past = time.time() - 7200.0  # 2 hours ago
        mgr.register("azure", "inst-1", "/tmp/d1")
        r = mgr.all_tracked()[0]
        r.registered_at = past
        cost = mgr.cost_estimate()
        assert cost > 0.0
        assert cost == round(cost, 4)


class TestResourceLifecycleManagerOrphanReport:
    def test_no_orphans_for_present_directory(self):
        mgr = ResourceLifecycleManager()
        with tempfile.TemporaryDirectory() as tmpd:
            (Path(tmpd) / "terraform.tfstate").write_text("{}")
            mgr.register("azure", "inst-1", tmpd)
            assert mgr.orphan_report() == []

    def test_orphan_for_missing_directory(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/nonexistent/path/xyz")
        orphans = mgr.orphan_report()
        assert len(orphans) == 1
        assert orphans[0]["reason"] == "deploy directory missing"
        assert orphans[0]["instance_id"] == "inst-1"

    def test_orphan_for_empty_directory(self):
        mgr = ResourceLifecycleManager()
        with tempfile.TemporaryDirectory() as tmpd:
            mgr.register("azure", "inst-1", tmpd)
            orphans = mgr.orphan_report()
            assert len(orphans) == 1
            assert orphans[0]["reason"] == "deploy directory empty"

    def test_no_orphans_for_cleaned_up(self):
        mgr = ResourceLifecycleManager()
        mgr.register("azure", "inst-1", "/nonexistent/path/xyz")
        mgr.deregister("inst-1")
        assert mgr.orphan_report() == []

    def test_no_orphans_for_directory_with_files(self):
        mgr = ResourceLifecycleManager()
        with tempfile.TemporaryDirectory() as tmpd:
            Path = __import__("pathlib").Path
            (Path(tmpd) / "test.txt").write_text("hello")
            mgr.register("azure", "inst-1", tmpd)
            assert mgr.orphan_report() == []


class TestResourceLifecycleManagerCleanup:
    def test_cleanup_all_no_resources(self):
        mgr = ResourceLifecycleManager()
        assert mgr.cleanup_all() == 0

    def test_cleanup_all_calls_destroy_fn(self):
        mgr = ResourceLifecycleManager()
        destroyed = []
        mgr.set_destroy_fn(lambda iid, dd: destroyed.append(iid))
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("aws", "inst-2", "/tmp/d2")
        count = mgr.cleanup_all()
        assert count == 2
        assert set(destroyed) == {"inst-1", "inst-2"}

    def test_cleanup_all_removes_after_destroy(self):
        mgr = ResourceLifecycleManager()
        mgr.set_destroy_fn(lambda iid, dd: None)
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.cleanup_all()
        assert mgr.is_tracked("inst-1") is False
        assert mgr.pending_cleanup() == []

    def test_cleanup_all_respects_provider_filter(self):
        mgr = ResourceLifecycleManager()
        destroyed = []
        mgr.set_destroy_fn(lambda iid, dd: destroyed.append(iid))
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("aws", "inst-2", "/tmp/d2")
        count = mgr.cleanup_all(provider="azure")
        assert count == 1
        assert destroyed == ["inst-1"]
        assert mgr.is_tracked("inst-2") is True

    def test_cleanup_all_destroy_exception_does_not_raise(self):
        mgr = ResourceLifecycleManager()

        def boom(_iid, _dd):
            raise RuntimeError("destroy failed")

        mgr.set_destroy_fn(boom)
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("aws", "inst-2", "/tmp/d2")
        count = mgr.cleanup_all()
        assert count == 0

    def test_cleanup_idle_cleans_only_idle_resources(self):
        mgr = ResourceLifecycleManager()
        destroyed = []
        mgr.set_destroy_fn(lambda iid, dd: destroyed.append(iid))
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("azure", "inst-2", "/tmp/d2")
        r = mgr.all_tracked()
        r[0].last_activity = time.time() - 3600  # 1 hour idle
        count = mgr.cleanup_idle("azure", idle_minutes=10)
        assert count == 1
        assert destroyed == ["inst-1"]

    def test_cleanup_idle_respects_provider(self):
        mgr = ResourceLifecycleManager()
        destroyed = []
        mgr.set_destroy_fn(lambda iid, dd: destroyed.append(iid))
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("aws", "inst-2", "/tmp/d2")
        r = mgr.all_tracked()
        for rr in r:
            rr.last_activity = time.time() - 3600
        count = mgr.cleanup_idle("azure", idle_minutes=10)
        assert count == 1
        assert destroyed == ["inst-1"]

    def test_cleanup_idle_no_idle_resources(self):
        mgr = ResourceLifecycleManager()
        destroyed = []
        mgr.set_destroy_fn(lambda iid, dd: destroyed.append(iid))
        mgr.register("azure", "inst-1", "/tmp/d1")
        count = mgr.cleanup_idle("azure", idle_minutes=60)
        assert count == 0
        assert destroyed == []


class TestResourceLifecycleManagerBackgroundPoll:
    def test_start_poll_creates_thread(self):
        mgr = ResourceLifecycleManager()
        mgr.start_background_poll()
        assert mgr._poll_thread is not None
        assert mgr._poll_thread.is_alive()
        mgr.stop_background_poll()

    def test_start_poll_idempotent(self):
        mgr = ResourceLifecycleManager()
        mgr.start_background_poll()
        t1 = mgr._poll_thread
        mgr.start_background_poll()
        assert mgr._poll_thread is t1
        mgr.stop_background_poll()

    def test_stop_poll_joins_thread(self):
        mgr = ResourceLifecycleManager()
        mgr.start_background_poll()
        mgr.stop_background_poll()
        assert mgr._poll_thread is None

    def test_stop_poll_no_thread_does_not_raise(self):
        mgr = ResourceLifecycleManager()
        mgr.stop_background_poll()

    def test_poll_loop_cleans_idle_per_provider(self):
        mgr = ResourceLifecycleManager()
        destroyed = []
        mgr.set_destroy_fn(lambda iid, dd: destroyed.append(iid))
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr.register("azure", "inst-2", "/tmp/d2")
        mgr.register("aws", "inst-3", "/tmp/d3")
        r_list = mgr.all_tracked()
        for r in r_list:
            r.last_activity = time.time() - 3600
        cleanup_calls = []
        original_cleanup_idle = mgr.cleanup_idle

        def tracking_cleanup(*args, **kwargs):
            cleanup_calls.append((args, kwargs))
            return original_cleanup_idle(*args, **kwargs)

        mgr.cleanup_idle = tracking_cleanup
        wait_responses = iter([False, True])
        with mock.patch.object(mgr._stop_event, "wait", side_effect=lambda *a, **kw: next(wait_responses)):
            mgr._poll_loop()
        assert len(cleanup_calls) > 0


class TestResourceLifecycleManagerGuaranteedCleanup:
    def test_guaranteed_cleanup_calls_cleanup_all(self):
        mgr = ResourceLifecycleManager()
        destroyed = []
        mgr.set_destroy_fn(lambda iid, dd: destroyed.append(iid))
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr._guaranteed_cleanup()
        assert "inst-1" in destroyed
        assert mgr.is_tracked("inst-1") is False

    def test_handle_signal_calls_cleanup(self):
        mgr = ResourceLifecycleManager()
        destroyed = []
        mgr.set_destroy_fn(lambda iid, dd: destroyed.append(iid))
        mgr.register("azure", "inst-1", "/tmp/d1")
        with mock.patch.object(os, "kill") as mock_kill:
            mgr._handle_signal(signal.SIGTERM, None)
            assert "inst-1" in destroyed
            mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)

    def test_guaranteed_cleanup_exception_handled(self):
        mgr = ResourceLifecycleManager()

        def boom(_iid, _dd):
            raise RuntimeError("fail")

        mgr.set_destroy_fn(boom)
        mgr.register("azure", "inst-1", "/tmp/d1")
        mgr._guaranteed_cleanup()


class TestResourceLifecycleManagerSignalHandlers:
    def test_install_signal_handlers_main_thread(self):
        import general_ludd.cloud.resource_lifecycle as rl

        mgr = ResourceLifecycleManager()
        from general_ludd.cloud.resource_lifecycle import _install_signal_handlers

        saved = rl._signal_handlers_installed
        try:
            rl._signal_handlers_installed = False
            with mock.patch("signal.signal") as mock_sig:
                result = _install_signal_handlers(mgr)
                assert result is True
                assert mock_sig.call_count == 2
        finally:
            rl._signal_handlers_installed = saved

    def test_install_signal_handlers_value_error(self):
        mgr = ResourceLifecycleManager()
        import general_ludd.cloud.resource_lifecycle as rl

        saved = rl._signal_handlers_installed
        try:
            rl._signal_handlers_installed = False
            with mock.patch("signal.signal", side_effect=ValueError):
                result = rl._install_signal_handlers(mgr)
                assert result is False
        finally:
            rl._signal_handlers_installed = saved

    def test_signal_handlers_idempotent(self):
        mgr = ResourceLifecycleManager()
        import general_ludd.cloud.resource_lifecycle as rl

        saved = rl._signal_handlers_installed
        try:
            rl._signal_handlers_installed = True
            with mock.patch("signal.signal") as mock_sig:
                result = rl._install_signal_handlers(mgr)
                assert result is True
                mock_sig.assert_not_called()
        finally:
            rl._signal_handlers_installed = saved

    def test_install_signal_handlers_non_main_thread(self):
        mgr = ResourceLifecycleManager()
        import general_ludd.cloud.resource_lifecycle as rl

        saved = rl._signal_handlers_installed
        try:
            rl._signal_handlers_installed = False
            with mock.patch("threading.current_thread") as mock_ct:
                mock_ct.return_value = object()
                with mock.patch("threading.main_thread", return_value=object()):
                    result = rl._install_signal_handlers(mgr)
                    assert result is False
        finally:
            rl._signal_handlers_installed = saved


class TestGetLifecycleSingleton:
    def test_returns_same_instance(self):
        get_lifecycle()
        import general_ludd.cloud.resource_lifecycle as rl

        saved = rl._lifecycle_singleton
        try:
            rl._lifecycle_singleton = None
            rl._lifecycle_singleton_lock = threading.Lock()
            a = get_lifecycle()
            b = get_lifecycle()
            assert a is b
        finally:
            rl._lifecycle_singleton = saved


class TestLIFECYCLE_TARGETS:
    def test_has_four_providers(self):
        assert len(LIFECYCLE_TARGETS) == 4

    def test_each_provider_has_validator_script_key(self):
        for provider in ("azure", "aws", "gcp", "runpod"):
            assert provider in LIFECYCLE_TARGETS
            assert "validator_script" in LIFECYCLE_TARGETS[provider]

    def test_each_validator_script_is_string(self):
        for provider in ("azure", "aws", "gcp", "runpod"):
            assert isinstance(LIFECYCLE_TARGETS[provider]["validator_script"], str)
