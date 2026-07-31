"""Unit tests for ``general_ludd.cloud.lifecycle_validator`` — resource
lifecycle validation, idle detection, and smoke tests.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from general_ludd.cloud.lifecycle_validator import (
    LIFECYCLE_TARGETS,
    SUPPORTED_PROVIDERS,
    IdleResourceDetector,
    LifecyclePhase,
    LifecycleResult,
    ResourceLifecycleTester,
    run_lifecycle_smoke,
)


class TestLifecycleResult:
    def test_passed_all_good(self):
        result = LifecycleResult(
            provider="azure",
            gpu_type="t4",
            phases=[
                LifecyclePhase.IDLE,
                LifecyclePhase.PROVISIONING,
                LifecyclePhase.RUNNING,
                LifecyclePhase.DESTROYING,
                LifecyclePhase.DESTROYED,
            ],
            endpoint_reachable=True,
            destroyed_verified=True,
        )
        assert result.passed is True

    def test_passed_fails_when_not_destroyed_verified(self):
        result = LifecycleResult(
            provider="azure",
            gpu_type="t4",
            phases=[LifecyclePhase.PROVISIONING, LifecyclePhase.RUNNING, LifecyclePhase.DESTROYING],
            destroyed_verified=False,
        )
        assert result.passed is False

    def test_passed_fails_with_orphans(self):
        result = LifecycleResult(
            provider="azure",
            gpu_type="t4",
            phases=[
                LifecyclePhase.PROVISIONING,
                LifecyclePhase.RUNNING,
                LifecyclePhase.DESTROYING,
                LifecyclePhase.DESTROYED,
            ],
            destroyed_verified=True,
            orphans_detected=["/subscriptions/.../resourceGroups/rg-gludd/providers/..."],
        )
        assert result.passed is False

    def test_passed_fails_with_errors(self):
        result = LifecycleResult(
            provider="azure",
            gpu_type="t4",
            phases=[
                LifecyclePhase.IDLE,
                LifecyclePhase.PROVISIONING,
                LifecyclePhase.RUNNING,
                LifecyclePhase.DESTROYING,
                LifecyclePhase.DESTROYED,
            ],
            endpoint_reachable=True,
            destroyed_verified=True,
            errors=["provision timeout"],
        )
        assert result.passed is False

    def test_total_wall_ms_computes_correctly(self):
        start = 1000.0
        end = 1005.5
        result = LifecycleResult(provider="aws", gpu_type="t4", start_time=start)
        result.end_time = end
        assert result.total_wall_ms == 5500.0

    def test_total_wall_ms_zero_when_not_ended(self):
        result = LifecycleResult(provider="aws", gpu_type="t4")
        assert result.total_wall_ms == 0.0

    def test_default_fields(self):
        result = LifecycleResult(provider="gcp", gpu_type="t4")
        assert result.provider == "gcp"
        assert result.gpu_type == "t4"
        assert result.phases == []
        assert result.provision_time_ms == 0.0
        assert result.destroy_time_ms == 0.0
        assert result.endpoint_reachable is False
        assert result.destroyed_verified is False
        assert result.orphans_detected == []
        assert result.cost_incurred_usd == 0.0
        assert result.errors == []
        assert result.end_time == 0.0


class TestResourceLifecycleTester:
    def test_init_defaults(self):
        tester = ResourceLifecycleTester(provider="azure")
        assert tester.provider == "azure"
        assert tester.gpu_type == "a100_80"
        assert tester.model_name == "Qwen/Qwen2.5-Coder-7B-Instruct"
        assert tester._provision_timeout_s == 900
        assert tester._destroy_timeout_s == 300
        assert tester._max_cost_usd == 10.0

    def test_init_custom(self):
        tester = ResourceLifecycleTester(
            provider="aws",
            gpu_type="t4",
            model_name="meta-llama/Llama-3.2-1B",
            provision_timeout_s=300,
            destroy_timeout_s=120,
            max_cost_usd=5.0,
        )
        assert tester.provider == "aws"
        assert tester.gpu_type == "t4"
        assert tester._provision_timeout_s == 300
        assert tester._destroy_timeout_s == 120
        assert tester._max_cost_usd == 5.0

    def test_init_rejects_unsupported_provider(self):
        with pytest.raises(ValueError, match="Unsupported provider"):
            ResourceLifecycleTester(provider="digital_ocean")

    def test_provision_stores_result_phases(self):
        tester = ResourceLifecycleTester(provider="azure", gpu_type="t4")
        with (
            patch.object(tester, "_has_credentials", return_value=True),
            patch.object(tester, "_do_provision", return_value=True),
        ):
            result = tester.provision()
        assert result is True

    def test_destroy_stores_result_phases(self):
        tester = ResourceLifecycleTester(provider="azure", gpu_type="t4")
        with (
            patch.object(tester, "_has_credentials", return_value=True),
            patch.object(tester, "_do_destroy", return_value=True),
        ):
            result = tester.destroy()
        assert result is True

    def test_run_full_lifecycle_produces_result(self):
        tester = ResourceLifecycleTester(provider="aws", gpu_type="t4")
        with (
            patch.object(tester, "_has_credentials", return_value=True),
            patch.object(tester, "_do_provision", return_value=True),
            patch.object(tester, "_do_destroy", return_value=True),
            patch.object(tester, "_poll_endpoint", return_value=True),
            patch.object(tester, "_check_cost", return_value=0.23),
            patch.object(tester, "_provider_list_resources", return_value=[]),
        ):
            result = tester.run_full_lifecycle()
        assert isinstance(result, LifecycleResult)
        assert result.provider == "aws"
        assert result.endpoint_reachable is True
        assert result.destroyed_verified is True
        assert LifecyclePhase.PROVISIONING in result.phases
        assert LifecyclePhase.RUNNING in result.phases
        assert LifecyclePhase.DESTROYING in result.phases
        assert LifecyclePhase.DESTROYED in result.phases

    def test_run_full_lifecycle_handles_provision_failure(self):
        tester = ResourceLifecycleTester(provider="azure", gpu_type="t4")
        with (
            patch.object(tester, "_has_credentials", return_value=True),
            patch.object(tester, "_do_provision", return_value=False),
            patch.object(tester, "_check_cost", return_value=0.0),
        ):
            result = tester.run_full_lifecycle()
        assert result.passed is False
        assert LifecyclePhase.RUNNING not in result.phases

    def test_run_full_lifecycle_detects_orphans(self):
        tester = ResourceLifecycleTester(provider="azure", gpu_type="t4")
        orphans = ["/subscriptions/sub/resourceGroups/rg-gludd/vm/gpu-vm"]
        with (
            patch.object(tester, "_has_credentials", return_value=True),
            patch.object(tester, "_do_provision", return_value=True),
            patch.object(tester, "_do_destroy", return_value=True),
            patch.object(tester, "_poll_endpoint", return_value=True),
            patch.object(tester, "_check_cost", return_value=0.45),
            patch.object(tester, "_provider_list_resources", return_value=orphans),
        ):
            result = tester.run_full_lifecycle()
        assert result.passed is False
        assert len(result.orphans_detected) == 1
        assert orphans[0] in result.orphans_detected

    def test__check_cost_returns_float(self):
        tester = ResourceLifecycleTester(provider="aws", gpu_type="t4")
        cost = tester._check_cost()
        assert isinstance(cost, float)

    def test__poll_endpoint_timeout(self):
        tester = ResourceLifecycleTester(provider="gcp", gpu_type="t4")
        with patch("time.time", side_effect=[1000.0, 1000.0, 99999.0]):
            result = tester._poll_endpoint("http://10.0.0.1:8080", timeout_s=1)
        assert result is False

    def test__provider_list_resources_returns_list(self):
        tester = ResourceLifecycleTester(provider="azure", gpu_type="t4")
        resources = tester._provider_list_resources()
        assert isinstance(resources, list)

    def test_verify_running_makes_endpoint_call(self):
        tester = ResourceLifecycleTester(provider="azure", gpu_type="t4")
        with (
            patch.object(tester, "_has_credentials", return_value=True),
            patch.object(tester, "_poll_endpoint", return_value=True),
        ):
            assert tester.verify_running() is True

    def test_verify_destroyed_no_orphans(self):
        tester = ResourceLifecycleTester(provider="azure", gpu_type="t4")
        with patch.object(tester, "_provider_list_resources", return_value=[]):
            assert tester.verify_destroyed() is True

    def test_verify_destroyed_finds_orphans(self):
        tester = ResourceLifecycleTester(provider="azure", gpu_type="t4")
        orphans = ["/subscriptions/.../resourceGroups/rg-gludd/disk/osdisk"]
        with patch.object(tester, "_provider_list_resources", return_value=orphans):
            assert tester.verify_destroyed() is False

    def test_all_supported_providers_createable(self):
        for provider in ResourceLifecycleTester.SUPPORTED_PROVIDERS:
            tester = ResourceLifecycleTester(provider=provider)
            assert tester.provider == provider

    def test_enum_providers_match_validator(self):
        tester_providers = set(ResourceLifecycleTester.SUPPORTED_PROVIDERS)
        assert tester_providers == SUPPORTED_PROVIDERS


class TestIdleResourceDetector:
    def test_detect_idle_returns_list(self):
        detector = IdleResourceDetector()
        result = detector.detect_idle("azure")
        assert isinstance(result, list)

    def test_detect_idle_returns_list_for_all_providers(self):
        detector = IdleResourceDetector()
        for provider in ["azure", "aws", "gcp", "runpod"]:
            result = detector.detect_idle(provider)
            assert isinstance(result, list)

    def test_auto_stop_idle_returns_int(self):
        detector = IdleResourceDetector()
        count = detector.auto_stop_idle("azure")
        assert isinstance(count, int)

    def test_cost_of_idle_returns_float(self):
        detector = IdleResourceDetector()
        cost = detector.cost_of_idle("azure")
        assert isinstance(cost, float)

    def test_cost_of_idle_non_negative(self):
        detector = IdleResourceDetector()
        for provider in ["azure", "aws", "gcp", "runpod"]:
            cost = detector.cost_of_idle(provider)
            assert cost >= 0.0


class TestLifecycleSmoke:
    def test_run_lifecycle_smoke_skips_without_creds(self):
        result = run_lifecycle_smoke("azure")
        assert isinstance(result, LifecycleResult)
        assert isinstance(result.passed, bool)

    def test_run_lifecycle_smoke_all_providers(self):
        for provider in ["azure", "aws", "gcp", "runpod"]:
            result = run_lifecycle_smoke(provider)
            assert isinstance(result, LifecycleResult)

    def test_smoke_returns_quickly(self):
        start = time.time()
        run_lifecycle_smoke("azure")
        elapsed = time.time() - start
        assert elapsed < 1.0, "smoke test should return immediately when no creds"


class TestLifecycleTargets:
    def test_all_targets_have_required_keys(self):
        required = {"provider", "gpu_type", "reason", "timeout_minutes"}
        for name, target in LIFECYCLE_TARGETS.items():
            assert required.issubset(target.keys()), f"{name} missing keys: {required - target.keys()}"

    def test_all_target_providers_supported(self):
        for target in LIFECYCLE_TARGETS.values():
            assert target["provider"] in SUPPORTED_PROVIDERS

    def test_timeout_minutes_positive(self):
        for name, target in LIFECYCLE_TARGETS.items():
            assert target["timeout_minutes"] > 0, f"{name} has non-positive timeout"

    def test_gpu_type_non_empty(self):
        for name, target in LIFECYCLE_TARGETS.items():
            assert target["gpu_type"], f"{name} has empty gpu_type"


class TestLifecyclePhaseEnum:
    def test_all_phases_defined(self):
        assert LifecyclePhase.IDLE.value == "idle"
        assert LifecyclePhase.PROVISIONING.value == "provisioning"
        assert LifecyclePhase.RUNNING.value == "running"
        assert LifecyclePhase.DESTROYING.value == "destroying"
        assert LifecyclePhase.DESTROYED.value == "destroyed"
        assert LifecyclePhase.ORPHANED.value == "orphaned"

    def test_phase_values_unique(self):
        values = [p.value for p in LifecyclePhase]
        assert len(values) == len(set(values))
