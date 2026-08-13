"""Release contracts for usable Azure A100/H100 accelerator deployments."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from general_ludd.infra.azure_accelerator import (
    AzureAcceleratorPreflight,
    AzureAcceleratorUnavailable,
    effective_timeout_minutes,
    resolve_accelerator,
)
from general_ludd.infra.compute import GPUType


def _usage(
    name: str,
    current: int | None,
    limit: int | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=SimpleNamespace(value=name, localized_value=name),
        current_value=current,
        limit=limit,
    )


def _sku(
    name: str,
    location: str = "eastus",
    *,
    restrictions: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        resource_type="virtualMachines",
        locations=[location],
        restrictions=restrictions or [],
    )


class _ComputeClient:
    def __init__(
        self,
        *,
        skus: list[SimpleNamespace],
        usages: list[SimpleNamespace],
    ) -> None:
        self.resource_skus = SimpleNamespace(list=lambda: skus)
        self.usage = SimpleNamespace(list=lambda location: usages)


@pytest.mark.parametrize(
    ("gpu_type", "gpu_count", "expected_size", "expected_vcpus"),
    [
        (GPUType.A100_40, 1, "Standard_NC24ads_A100_v4", 24),
        (GPUType.A100_80, 1, "Standard_NC24ads_A100_v4", 24),
        (GPUType.A100_80, 2, "Standard_NC48ads_A100_v4", 48),
        (GPUType.A100_80, 4, "Standard_NC96ads_A100_v4", 96),
        (GPUType.H100, 1, "Standard_NC40ads_H100_v5", 40),
        (GPUType.H100, 2, "Standard_NC80adis_H100_v5", 80),
    ],
)
def test_resolve_accelerator_uses_official_azure_gpu_sizes(
    gpu_type: GPUType,
    gpu_count: int,
    expected_size: str,
    expected_vcpus: int,
) -> None:
    resolved = resolve_accelerator(gpu_type, gpu_count)
    assert resolved.vm_size == expected_size
    assert resolved.vcpus == expected_vcpus
    assert resolved.gpu_count == gpu_count


def test_resolve_accelerator_rejects_an_unavailable_gpu_shape() -> None:
    with pytest.raises(AzureAcceleratorUnavailable, match="supported GPU counts"):
        resolve_accelerator(GPUType.H100, 4)


def test_resolve_accelerator_rejects_an_unsupported_gpu_family() -> None:
    with pytest.raises(AzureAcceleratorUnavailable, match="does not support a10g"):
        resolve_accelerator(GPUType.A10G, 1)


def test_preflight_checks_sku_family_and_regional_vcpu_quota() -> None:
    size = resolve_accelerator(GPUType.A100_80, 1)
    client = _ComputeClient(
        skus=[_sku(size.vm_size)],
        usages=[
            _usage("standardNCA100v4Family", 0, 48),
            _usage("cores", 10, 100),
        ],
    )

    result = AzureAcceleratorPreflight(client).check(
        gpu_type=GPUType.A100_80,
        gpu_count=1,
        location="eastus",
    )

    assert result.ready is True
    assert result.vm_size == size.vm_size
    assert result.requested_vcpus == 24
    assert result.family_quota_remaining == 48
    assert result.regional_quota_remaining == 90


def test_preflight_fails_closed_when_family_quota_is_insufficient() -> None:
    size = resolve_accelerator(GPUType.H100, 1)
    client = _ComputeClient(
        skus=[_sku(size.vm_size)],
        usages=[
            _usage("standardNCADSH100v5Family", 20, 40),
            _usage("cores", 0, 100),
        ],
    )

    result = AzureAcceleratorPreflight(client).check(
        gpu_type=GPUType.H100,
        gpu_count=1,
        location="eastus",
    )

    assert result.ready is False
    assert any("family quota" in blocker for blocker in result.blockers)


def test_preflight_fails_closed_when_sku_is_not_offered_in_region() -> None:
    client = _ComputeClient(
        skus=[],
        usages=[
            _usage("standardNCA100v4Family", 0, 96),
            _usage("cores", 0, 100),
        ],
    )

    result = AzureAcceleratorPreflight(client).check(
        gpu_type=GPUType.A100_80,
        gpu_count=1,
        location="eastus",
    )

    assert result.ready is False
    assert any("not available" in blocker for blocker in result.blockers)


def test_preflight_honors_subscription_location_restrictions() -> None:
    size = resolve_accelerator(GPUType.A100_80, 1)
    restriction = SimpleNamespace(
        type="Location",
        values=[],
        restriction_info=SimpleNamespace(locations=["East US"]),
    )
    client = _ComputeClient(
        skus=[_sku(size.vm_size, restrictions=[restriction])],
        usages=[
            _usage("standardNCA100v4Family", 0, 96),
            _usage("cores", 0, 100),
        ],
    )

    result = AzureAcceleratorPreflight(client).check(
        gpu_type=GPUType.A100_80,
        gpu_count=1,
        location="eastus",
    )

    assert result.ready is False
    assert result.sku_available is False


def test_preflight_fails_closed_when_quota_values_are_missing() -> None:
    size = resolve_accelerator(GPUType.A100_80, 1)
    client = _ComputeClient(
        skus=[_sku(size.vm_size)],
        usages=[
            _usage("standardNCA100v4Family", None, 96),
            _usage("cores", 0, None),
        ],
    )

    result = AzureAcceleratorPreflight(client).check(
        gpu_type=GPUType.A100_80,
        gpu_count=1,
        location="eastus",
    )

    assert result.ready is False
    assert any("family quota" in blocker for blocker in result.blockers)
    assert any("regional vCPU quota" in blocker for blocker in result.blockers)


def test_preflight_fails_closed_when_regional_quota_is_insufficient() -> None:
    size = resolve_accelerator(GPUType.H100, 1)
    client = _ComputeClient(
        skus=[_sku(size.vm_size)],
        usages=[
            _usage("standardNCADSH100v5Family", 0, 80),
            _usage("cores", 30, 40),
        ],
    )

    result = AzureAcceleratorPreflight(client).check(
        gpu_type=GPUType.H100,
        gpu_count=1,
        location="eastus",
    )

    assert result.ready is False
    assert any("insufficient regional quota" in blocker for blocker in result.blockers)


def test_budget_timeout_never_exceeds_user_ttl_or_spend_ceiling() -> None:
    assert effective_timeout_minutes(
        requested_timeout_minutes=120,
        max_cost_usd=10,
        hourly_rate_usd=20,
    ) == 30
    assert effective_timeout_minutes(
        requested_timeout_minutes=15,
        max_cost_usd=10,
        hourly_rate_usd=20,
    ) == 15


def test_budget_timeout_requires_positive_inputs_and_accepts_unknown_rate() -> None:
    assert effective_timeout_minutes(
        requested_timeout_minutes=15,
        max_cost_usd=10,
        hourly_rate_usd=None,
    ) == 15
    with pytest.raises(ValueError, match="requested_timeout_minutes"):
        effective_timeout_minutes(
            requested_timeout_minutes=0,
            max_cost_usd=10,
            hourly_rate_usd=None,
        )
    with pytest.raises(ValueError, match="max_cost_usd"):
        effective_timeout_minutes(
            requested_timeout_minutes=15,
            max_cost_usd=0,
            hourly_rate_usd=None,
        )
    with pytest.raises(ValueError, match="hourly_rate_usd"):
        effective_timeout_minutes(
            requested_timeout_minutes=15,
            max_cost_usd=10,
            hourly_rate_usd=0,
        )
