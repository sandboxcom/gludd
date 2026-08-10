"""Tests for cloud lifecycle validator — dataclasses, enums, and logic."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict

import pytest

from general_ludd.cloud.lifecycle_validator import (
    _PROVIDER_CRED_VARS,
    LIFECYCLE_TARGETS,
    SUPPORTED_PROVIDERS,
    IdleResourceDetector,
    LifecyclePhase,
    LifecycleResult,
    ResourceLifecycleTester,
    _lifecycle_for_target,
    run_lifecycle_smoke,
)

# ---------------------------------------------------------------------------
# LifecyclePhase
# ---------------------------------------------------------------------------


class TestLifecyclePhase:
    def test_all_phases_present(self):
        expected = {"idle", "provisioning", "running", "destroying", "destroyed", "orphaned"}
        assert {p.value for p in LifecyclePhase} == expected

    def test_phase_values_are_strings(self):
        for p in LifecyclePhase:
            assert isinstance(p.value, str)

    def test_idle_is_first(self):
        assert LifecyclePhase.IDLE.value == "idle"


# ---------------------------------------------------------------------------
# LifecycleResult
# ---------------------------------------------------------------------------


class TestLifecycleResult:
    def test_default_construction(self):
        result = LifecycleResult(provider="azure", gpu_type="t4")
        assert result.provider == "azure"
        assert result.gpu_type == "t4"
        assert result.phases == []
        assert result.provision_time_ms == 0.0
        assert result.destroy_time_ms == 0.0
        assert result.endpoint_reachable is False
        assert result.destroyed_verified is False
        assert result.orphans_detected == []
        assert result.cost_incurred_usd == 0.0
        assert result.errors == []
        assert result.start_time > 0
        assert result.end_time == 0.0

    def test_full_construction(self):
        t0 = time.time()
        result = LifecycleResult(
            provider="aws",
            gpu_type="a100_80",
            phases=[
                LifecyclePhase.PROVISIONING,
                LifecyclePhase.RUNNING,
                LifecyclePhase.DESTROYING,
                LifecyclePhase.DESTROYED,
            ],
            provision_time_ms=45.2,
            destroy_time_ms=12.8,
            endpoint_reachable=True,
            destroyed_verified=True,
            orphans_detected=[],
            cost_incurred_usd=1.23,
            errors=[],
            start_time=t0,
            end_time=t0 + 60.0,
        )
        assert result.provision_time_ms == 45.2
        assert result.destroy_time_ms == 12.8
        assert result.endpoint_reachable is True
        assert result.destroyed_verified is True

    def test_passed_all_green(self):
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
            orphans_detected=[],
            errors=[],
        )
        assert result.passed is True

    def test_passed_not_destroyed_verified(self):
        result = LifecycleResult(
            provider="azure",
            gpu_type="t4",
            phases=[
                LifecyclePhase.PROVISIONING,
                LifecyclePhase.RUNNING,
                LifecyclePhase.DESTROYING,
                LifecyclePhase.DESTROYED,
            ],
            destroyed_verified=False,
            orphans_detected=[],
            errors=[],
        )
        assert result.passed is False

    def test_passed_has_orphans(self):
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
            orphans_detected=["orphan-1"],
            errors=[],
        )
        assert result.passed is False

    def test_passed_destroyed_not_in_phases(self):
        result = LifecycleResult(
            provider="azure",
            gpu_type="t4",
            phases=[LifecyclePhase.PROVISIONING, LifecyclePhase.RUNNING, LifecyclePhase.DESTROYING],
            destroyed_verified=True,
            orphans_detected=[],
            errors=[],
        )
        assert result.passed is False

    def test_passed_has_errors(self):
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
            orphans_detected=[],
            errors=["something broke"],
        )
        assert result.passed is False

    def test_passed_idle_after_first_is_bad(self):
        result = LifecycleResult(
            provider="azure",
            gpu_type="t4",
            phases=[LifecyclePhase.PROVISIONING, LifecyclePhase.IDLE, LifecyclePhase.RUNNING, LifecyclePhase.DESTROYED],
            destroyed_verified=True,
            orphans_detected=[],
            errors=[],
        )
        assert result.passed is False

    def test_passed_idle_first_is_fine(self):
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
            destroyed_verified=True,
            orphans_detected=[],
            errors=[],
        )
        assert result.passed is True

    def test_total_wall_ms_zero_when_not_ended(self):
        t0 = time.time()
        result = LifecycleResult(provider="azure", gpu_type="t4", start_time=t0)
        assert result.total_wall_ms == 0.0

    def test_total_wall_ms_computes_correctly(self):
        t0 = 1000.0
        result = LifecycleResult(
            provider="azure",
            gpu_type="t4",
            start_time=t0,
            end_time=t0 + 3.5,
        )
        assert result.total_wall_ms == 3500.0

    def test_run_full_lifecycle_without_credentials(self):
        tester = ResourceLifecycleTester(provider="azure")
        result = tester.run_full_lifecycle()
        assert result.provider == "azure"
        assert not result.passed
        assert any("No provider credentials" in e for e in result.errors)

    def test_run_full_lifecycle_success_path(self):
        tester = ResourceLifecycleTester(provider="azure")
        tester._has_credentials = lambda: True  # override to simulate creds present
        tester._do_provision = lambda: True
        tester._do_destroy = lambda: True
        tester._poll_endpoint = lambda url, timeout_s: True
        tester._provider_list_resources = lambda: []
        tester._check_cost = lambda: 0.5

        result = tester.run_full_lifecycle()
        assert result.provider == "azure"
        assert result.endpoint_reachable is True
        assert result.destroyed_verified is True
        assert LifecyclePhase.DESTROYED in result.phases
        assert LifecyclePhase.PROVISIONING in result.phases
        assert LifecyclePhase.RUNNING in result.phases
        assert result.cost_incurred_usd == 0.5
        assert result.passed is True
        assert result.end_time > 0

    def test_run_full_lifecycle_provision_fails(self):
        tester = ResourceLifecycleTester(provider="aws")
        tester._has_credentials = lambda: True
        tester._do_provision = lambda: False

        result = tester.run_full_lifecycle()
        assert not result.passed
        assert LifecyclePhase.PROVISIONING in result.phases
        assert "provision failed" in result.errors

    def test_run_full_lifecycle_destroy_fails(self):
        tester = ResourceLifecycleTester(provider="aws")
        tester._has_credentials = lambda: True
        tester._do_provision = lambda: True
        tester._do_destroy = lambda: False
        tester._poll_endpoint = lambda url, timeout_s: True

        result = tester.run_full_lifecycle()
        assert not result.passed
        assert "destroy failed" in result.errors
        assert LifecyclePhase.PROVISIONING in result.phases
        assert LifecyclePhase.DESTROYING in result.phases

    def test_run_full_lifecycle_orphans_prevent_pass(self):
        tester = ResourceLifecycleTester(provider="aws")
        tester._has_credentials = lambda: True
        tester._do_provision = lambda: True
        tester._do_destroy = lambda: True
        tester._poll_endpoint = lambda url, timeout_s: True
        tester._provider_list_resources = lambda: ["orphan-vm-1"]

        result = tester.run_full_lifecycle()
        assert result.orphans_detected == ["orphan-vm-1"]
        assert not result.passed


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_supported_providers(self):
        assert frozenset({"azure", "aws", "gcp", "runpod"}) == SUPPORTED_PROVIDERS
        assert isinstance(SUPPORTED_PROVIDERS, frozenset)

    def test_provider_cred_vars_coverage(self):
        for provider in SUPPORTED_PROVIDERS:
            assert provider in _PROVIDER_CRED_VARS, f"missing cred vars for {provider}"
            assert len(_PROVIDER_CRED_VARS[provider]) >= 1

    def test_lifecycle_targets_structure(self):
        for _name, target in LIFECYCLE_TARGETS.items():
            assert "provider" in target
            assert target["provider"] in SUPPORTED_PROVIDERS
            assert "gpu_type" in target
            assert "reason" in target
            assert "timeout_minutes" in target
            assert 0 < target["timeout_minutes"] <= 60


# ---------------------------------------------------------------------------
# ResourceLifecycleTester
# ---------------------------------------------------------------------------


class TestResourceLifecycleTester:
    def test_supported_providers_list(self):
        assert sorted(SUPPORTED_PROVIDERS) == ResourceLifecycleTester.SUPPORTED_PROVIDERS

    def test_constructor_defaults(self):
        tester = ResourceLifecycleTester(provider="azure")
        assert tester.provider == "azure"
        assert tester.gpu_type == "a100_80"
        assert tester.model_name == "Qwen/Qwen2.5-Coder-7B-Instruct"
        assert tester._provision_timeout_s == 900
        assert tester._destroy_timeout_s == 300
        assert tester._max_cost_usd == 10.0

    def test_constructor_custom(self):
        tester = ResourceLifecycleTester(
            provider="aws",
            gpu_type="t4",
            model_name="test-model",
            provision_timeout_s=60,
            destroy_timeout_s=30,
            max_cost_usd=1.0,
        )
        assert tester.provider == "aws"
        assert tester.gpu_type == "t4"
        assert tester.model_name == "test-model"
        assert tester._provision_timeout_s == 60
        assert tester._destroy_timeout_s == 30
        assert tester._max_cost_usd == 1.0

    def test_constructor_rejects_unsupported_provider(self):
        with pytest.raises(ValueError, match="Unsupported provider"):
            ResourceLifecycleTester(provider="nvidia")

    def test_all_supported_providers_accepted(self):
        for provider in SUPPORTED_PROVIDERS:
            tester = ResourceLifecycleTester(provider=provider)
            assert tester.provider == provider

    def test_has_credentials_cached(self):
        tester = ResourceLifecycleTester(provider="azure")
        assert tester._cred_check is None
        result = tester._has_credentials()
        assert result is not None
        assert tester._cred_check is not None

    def test_provision_skips_without_credentials(self):
        tester = ResourceLifecycleTester(provider="azure")
        ok = tester.provision()
        assert ok is False
        assert any("No provider credentials" in e for e in tester._result.errors)

    def test_verify_running_skips_without_credentials(self):
        tester = ResourceLifecycleTester(provider="azure")
        ok = tester.verify_running()
        assert ok is False

    def test_destroy_skips_without_credentials(self):
        tester = ResourceLifecycleTester(provider="azure")
        ok = tester.destroy()
        assert ok is False

    def test_provision_records_phases_on_success(self):
        tester = ResourceLifecycleTester(provider="azure")
        tester._has_credentials = lambda: True
        tester._do_provision = lambda: True
        ok = tester.provision()
        assert ok is True
        assert LifecyclePhase.PROVISIONING in tester._result.phases
        assert LifecyclePhase.RUNNING in tester._result.phases
        assert tester._result.provision_time_ms >= 0

    def test_provision_records_phase_on_failure(self):
        tester = ResourceLifecycleTester(provider="azure")
        tester._has_credentials = lambda: True
        tester._do_provision = lambda: False
        ok = tester.provision()
        assert ok is False
        assert LifecyclePhase.PROVISIONING in tester._result.phases
        assert "provision failed" in tester._result.errors

    def test_destroy_records_phases_on_success(self):
        tester = ResourceLifecycleTester(provider="azure")
        tester._has_credentials = lambda: True
        tester._do_destroy = lambda: True
        ok = tester.destroy()
        assert ok is True
        assert LifecyclePhase.DESTROYING in tester._result.phases
        assert LifecyclePhase.DESTROYED in tester._result.phases

    def test_destroy_records_phase_on_failure(self):
        tester = ResourceLifecycleTester(provider="azure")
        tester._has_credentials = lambda: True
        tester._do_destroy = lambda: False
        ok = tester.destroy()
        assert ok is False
        assert "destroy failed" in tester._result.errors

    def test_verify_running_sets_endpoint_reachable(self):
        tester = ResourceLifecycleTester(provider="azure")
        tester._has_credentials = lambda: True
        tester._poll_endpoint = lambda url, timeout_s: True
        ok = tester.verify_running()
        assert ok is True
        assert tester._result.endpoint_reachable is True

    def test_verify_running_sets_unreachable(self):
        tester = ResourceLifecycleTester(provider="azure")
        tester._has_credentials = lambda: True
        tester._poll_endpoint = lambda url, timeout_s: False
        ok = tester.verify_running()
        assert ok is False
        assert tester._result.endpoint_reachable is False

    def test_verify_destroyed_with_no_orphans(self):
        tester = ResourceLifecycleTester(provider="azure")
        tester._provider_list_resources = lambda: []
        ok = tester.verify_destroyed()
        assert ok is True
        assert tester._result.destroyed_verified is True
        assert tester._result.orphans_detected == []

    def test_verify_destroyed_with_orphans(self):
        tester = ResourceLifecycleTester(provider="azure")
        tester._provider_list_resources = lambda: ["vm-1", "nsg-1"]
        ok = tester.verify_destroyed()
        assert ok is False
        assert tester._result.destroyed_verified is False
        assert tester._result.orphans_detected == ["vm-1", "nsg-1"]

    def test_credential_check_behavior(self):
        tester = ResourceLifecycleTester(provider="runpod")
        assert tester._cred_check is None
        result = tester._has_credentials()
        assert isinstance(result, bool)
        assert tester._cred_check is result

    def test_default_endpoint_url(self):
        tester = ResourceLifecycleTester(provider="azure")
        assert tester._default_endpoint_url().startswith("http://")


# ---------------------------------------------------------------------------
# IdleResourceDetector
# ---------------------------------------------------------------------------


class TestIdleResourceDetector:
    def test_default_construction(self):
        detector = IdleResourceDetector()
        assert detector is not None

    def test_detect_idle_returns_empty_list(self):
        detector = IdleResourceDetector()
        result = detector.detect_idle("azure")
        assert result == []

    def test_auto_stop_idle_returns_zero(self):
        detector = IdleResourceDetector()
        result = detector.auto_stop_idle("azure")
        assert result == 0

    def test_cost_of_idle_returns_zero(self):
        detector = IdleResourceDetector()
        result = detector.cost_of_idle("aws")
        assert result == 0.0

    def test_auto_stop_idle_with_custom_max_idle(self):
        detector = IdleResourceDetector()
        result = detector.auto_stop_idle("gcp", max_idle_minutes=15)
        assert result == 0


# ---------------------------------------------------------------------------
# run_lifecycle_smoke
# ---------------------------------------------------------------------------


class TestRunLifecycleSmoke:
    def test_returns_lifecycle_result(self):
        result = run_lifecycle_smoke("azure")
        assert isinstance(result, LifecycleResult)
        assert result.provider == "azure"
        assert result.gpu_type == "t4"

    def test_all_providers_produce_result(self):
        for provider in SUPPORTED_PROVIDERS:
            result = run_lifecycle_smoke(provider)
            assert result.provider == provider
            assert isinstance(result, LifecycleResult)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unsupported provider"):
            run_lifecycle_smoke("nvidia")


# ---------------------------------------------------------------------------
# _lifecycle_for_target
# ---------------------------------------------------------------------------


class TestLifecycleForTarget:
    def test_unknown_target_returns_error(self):
        result = asyncio.run(_lifecycle_for_target("nonexistent_target"))
        assert isinstance(result, LifecycleResult)
        assert len(result.errors) == 1
        assert "Unknown lifecycle target" in result.errors[0]

    def test_known_target_returns_result(self):
        result = asyncio.run(_lifecycle_for_target("azure_t4_smoke"))
        assert isinstance(result, LifecycleResult)
        assert result.provider == "azure"
        assert result.gpu_type == "t4"

    @pytest.mark.asyncio
    async def test_unknown_target_async(self):
        result = await _lifecycle_for_target("nonexistent_target")
        assert len(result.errors) == 1
        assert "Unknown lifecycle target" in result.errors[0]

    @pytest.mark.asyncio
    async def test_valid_target_async(self):
        result = await _lifecycle_for_target("azure_t4_smoke")
        assert isinstance(result, LifecycleResult)
        assert result.provider == "azure"

    def test_all_registered_targets_succeed(self):
        for target_name in LIFECYCLE_TARGETS:
            result = asyncio.run(_lifecycle_for_target(target_name))
            assert result.provider == LIFECYCLE_TARGETS[target_name]["provider"]


# ---------------------------------------------------------------------------
# LifecycleResult — serialization
# ---------------------------------------------------------------------------


class TestLifecycleResultSerialization:
    def test_asdict_produces_dict(self):
        result = LifecycleResult(provider="azure", gpu_type="t4")
        d = asdict(result)
        assert d["provider"] == "azure"
        assert d["gpu_type"] == "t4"
        assert "phases" in d
        assert "errors" in d
        assert "destroyed_verified" in d
