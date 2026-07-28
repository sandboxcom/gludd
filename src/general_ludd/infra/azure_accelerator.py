"""Azure accelerator SKU resolution and read-only deployment preflight.

The Azure Compute API exposes two independent gates before an accelerator VM
can be created:

* the requested VM SKU must be offered to the subscription in the region; and
* both the regional vCPU quota and the VM-family vCPU quota must have room.

This module keeps those checks separate from provisioning.  The preflight only
uses ``resource_skus.list`` and ``usage.list`` and therefore never creates,
updates, or deletes a paid Azure resource.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Protocol, cast

from general_ludd.infra.compute import GPUType


class AzureAcceleratorUnavailable(ValueError):
    """Raised when gludd cannot represent the requested Azure GPU shape."""


class AzureResourceSku(Protocol):
    name: str | None
    resource_type: str | None
    locations: Sequence[str] | None
    restrictions: Sequence[object] | None


class AzureUsageName(Protocol):
    value: str | None
    localized_value: str | None


class AzureUsage(Protocol):
    name: AzureUsageName
    current_value: int | None
    limit: int | None


class AzureResourceSkuOperations(Protocol):
    def list(self) -> Iterable[AzureResourceSku]: ...


class AzureUsageOperations(Protocol):
    def list(self, location: str) -> Iterable[AzureUsage]: ...


class AzureComputeClient(Protocol):
    resource_skus: AzureResourceSkuOperations
    usage: AzureUsageOperations


@dataclass(frozen=True, slots=True)
class AzureAcceleratorSize:
    """One Azure VM size capable of serving a requested accelerator shape."""

    gpu_type: GPUType
    gpu_count: int
    gpu_memory_gb: int
    vm_size: str
    vcpus: int
    quota_family_names: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AzurePreflightResult:
    """Read-only Azure quota/SKU readiness result."""

    ready: bool
    location: str
    gpu_type: GPUType
    gpu_count: int
    vm_size: str
    requested_vcpus: int
    sku_available: bool
    family_quota_remaining: int | None
    regional_quota_remaining: int | None
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["gpu_type"] = self.gpu_type.value
        return payload


_A100_FAMILY_NAMES = (
    "standardNCA100v4Family",
    "Standard NC A100 v4 Family vCPUs",
)
_H100_FAMILY_NAMES = (
    "standardNCADSH100v5Family",
    "Standard NCads H100 v5 Family vCPUs",
)
_T4_FAMILY_NAMES = (
    "standardNCASv3_T4Family",
    "Standard NCASv3 T4 Family vCPUs",
)

_AZURE_ACCELERATOR_SIZES: dict[tuple[GPUType, int], AzureAcceleratorSize] = {
    # Azure NC A100 v4 is an 80-GB A100 family. A request for a 40-GB A100 is
    # safely promoted to the 80-GB shape rather than silently under-provisioned.
    (GPUType.A100_40, 1): AzureAcceleratorSize(
        GPUType.A100_40,
        1,
        80,
        "Standard_NC24ads_A100_v4",
        24,
        _A100_FAMILY_NAMES,
    ),
    (GPUType.A100_80, 1): AzureAcceleratorSize(
        GPUType.A100_80,
        1,
        80,
        "Standard_NC24ads_A100_v4",
        24,
        _A100_FAMILY_NAMES,
    ),
    (GPUType.A100_80, 2): AzureAcceleratorSize(
        GPUType.A100_80,
        2,
        160,
        "Standard_NC48ads_A100_v4",
        48,
        _A100_FAMILY_NAMES,
    ),
    (GPUType.A100_80, 4): AzureAcceleratorSize(
        GPUType.A100_80,
        4,
        320,
        "Standard_NC96ads_A100_v4",
        96,
        _A100_FAMILY_NAMES,
    ),
    (GPUType.H100, 1): AzureAcceleratorSize(
        GPUType.H100,
        1,
        94,
        "Standard_NC40ads_H100_v5",
        40,
        _H100_FAMILY_NAMES,
    ),
    (GPUType.H100, 2): AzureAcceleratorSize(
        GPUType.H100,
        2,
        188,
        "Standard_NC80adis_H100_v5",
        80,
        _H100_FAMILY_NAMES,
    ),
    (GPUType.T4, 1): AzureAcceleratorSize(
        GPUType.T4,
        1,
        16,
        "Standard_NC4as_T4_v3",
        4,
        _T4_FAMILY_NAMES,
    ),
}


def resolve_accelerator(gpu_type: GPUType, gpu_count: int) -> AzureAcceleratorSize:
    """Resolve a gludd GPU request to an explicit Azure VM SKU.

    Azure accelerator VMs expose fixed GPU counts.  Requests which cannot be
    represented exactly are rejected before Terraform can spend money.
    """

    resolved = _AZURE_ACCELERATOR_SIZES.get((gpu_type, gpu_count))
    if resolved is not None:
        return resolved
    supported_counts = sorted(
        count for (known_gpu, count) in _AZURE_ACCELERATOR_SIZES if known_gpu == gpu_type
    )
    if not supported_counts:
        raise AzureAcceleratorUnavailable(
            f"Azure accelerator provisioning does not support {gpu_type.value}; "
            "supported GPU types are a100_40, a100_80, h100, and t4"
        )
    counts = ", ".join(str(count) for count in supported_counts)
    raise AzureAcceleratorUnavailable(
        f"Azure {gpu_type.value} supported GPU counts are [{counts}], not {gpu_count}"
    )


def effective_timeout_minutes(
    *,
    requested_timeout_minutes: float,
    max_cost_usd: float,
    hourly_rate_usd: float | None,
) -> float:
    """Return the earliest TTL imposed by time or a known hourly rate.

    Azure prices are region/offer specific, so callers must provide a rate from
    their price sheet or read-only pricing lookup.  When no trustworthy rate is
    available, the explicit TTL remains the fail-safe boundary.
    """

    if requested_timeout_minutes <= 0:
        raise ValueError("requested_timeout_minutes must be positive")
    if max_cost_usd <= 0:
        raise ValueError("max_cost_usd must be positive")
    if hourly_rate_usd is None:
        return requested_timeout_minutes
    if hourly_rate_usd <= 0:
        raise ValueError("hourly_rate_usd must be positive")
    spend_timeout = (max_cost_usd / hourly_rate_usd) * 60.0
    return min(requested_timeout_minutes, spend_timeout)


def _normalize_quota_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _usage_name(usage: AzureUsage) -> str:
    value = usage.name.value or usage.name.localized_value or ""
    return value


def _quota_remaining(
    usages: Sequence[AzureUsage],
    candidate_names: Sequence[str],
) -> int | None:
    normalized_candidates = {
        _normalize_quota_name(candidate) for candidate in candidate_names
    }
    for usage in usages:
        if _normalize_quota_name(_usage_name(usage)) not in normalized_candidates:
            continue
        if usage.current_value is None or usage.limit is None:
            return None
        return max(int(usage.limit) - int(usage.current_value), 0)
    return None


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value)


def _restriction_blocks_location(restriction: object, location: str) -> bool:
    restriction_type = str(getattr(restriction, "type", "")).casefold()
    if restriction_type not in {"location", "zone"}:
        return False
    blocked = _string_sequence(getattr(restriction, "values", None))
    info = getattr(restriction, "restriction_info", None)
    if info is not None:
        blocked += _string_sequence(getattr(info, "locations", None))
    normalized_location = location.casefold().replace(" ", "")
    return any(
        candidate.casefold().replace(" ", "") == normalized_location
        for candidate in blocked
    )


def _sku_is_available(
    skus: Iterable[AzureResourceSku],
    vm_size: str,
    location: str,
) -> bool:
    normalized_location = location.casefold().replace(" ", "")
    for sku in skus:
        if (sku.name or "").casefold() != vm_size.casefold():
            continue
        if (sku.resource_type or "").casefold() != "virtualmachines":
            continue
        locations = {
            value.casefold().replace(" ", "") for value in (sku.locations or ())
        }
        if normalized_location not in locations:
            continue
        if any(
            _restriction_blocks_location(restriction, location)
            for restriction in (sku.restrictions or ())
        ):
            continue
        return True
    return False


class AzureAcceleratorPreflight:
    """Perform non-mutating SKU and quota checks through Azure Compute."""

    def __init__(self, compute_client: AzureComputeClient) -> None:
        self._compute_client = compute_client

    def check(
        self,
        *,
        gpu_type: GPUType,
        gpu_count: int,
        location: str,
    ) -> AzurePreflightResult:
        resolved = resolve_accelerator(gpu_type, gpu_count)
        sku_available = _sku_is_available(
            self._compute_client.resource_skus.list(),
            resolved.vm_size,
            location,
        )
        usages = tuple(self._compute_client.usage.list(location))
        family_remaining = _quota_remaining(
            usages,
            resolved.quota_family_names,
        )
        regional_remaining = _quota_remaining(
            usages,
            (
                "cores",
                "Total Regional vCPUs",
                "Total Regional Cores",
            ),
        )

        blockers: list[str] = []
        if not sku_available:
            blockers.append(
                f"{resolved.vm_size} is not available to this subscription in {location}"
            )
        if family_remaining is None:
            blockers.append(
                f"Azure did not return the {resolved.vm_size} family quota in {location}"
            )
        elif family_remaining < resolved.vcpus:
            blockers.append(
                f"insufficient VM-family quota: need {resolved.vcpus} vCPUs, "
                f"have {family_remaining}"
            )
        if regional_remaining is None:
            blockers.append(
                f"Azure did not return the total regional vCPU quota in {location}"
            )
        elif regional_remaining < resolved.vcpus:
            blockers.append(
                f"insufficient regional quota: need {resolved.vcpus} vCPUs, "
                f"have {regional_remaining}"
            )

        warnings = (
            "Quota and SKU eligibility do not guarantee physical capacity; "
            "allocation can still fail. Retry, choose another supported size, "
            "or choose another region without leaving partial resources.",
        )
        return AzurePreflightResult(
            ready=not blockers,
            location=location,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            vm_size=resolved.vm_size,
            requested_vcpus=resolved.vcpus,
            sku_available=sku_available,
            family_quota_remaining=family_remaining,
            regional_quota_remaining=regional_remaining,
            blockers=tuple(blockers),
            warnings=warnings,
        )


def build_default_azure_preflight(
    subscription_id: str | None = None,
) -> AzureAcceleratorPreflight:
    """Build a read-only preflight client from standard Azure SDK credentials."""

    resolved_subscription = (
        subscription_id or os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    ).strip()
    if not resolved_subscription:
        raise ValueError(
            "AZURE_SUBSCRIPTION_ID is required for Azure accelerator preflight"
        )
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.compute import ComputeManagementClient
    except ImportError as exc:
        raise RuntimeError(
            "Azure SDK unavailable; install general-ludd-agent[azure]"
        ) from exc
    client = ComputeManagementClient(
        credential=DefaultAzureCredential(),
        subscription_id=resolved_subscription,
    )
    return AzureAcceleratorPreflight(cast(AzureComputeClient, client))
