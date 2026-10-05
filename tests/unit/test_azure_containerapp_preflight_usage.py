"""Behavioral tests for bounded supplementary Azure usage evidence."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

import pytest

from general_ludd.infra.azure_containerapp_arm import AzureContainerAppARMError
from general_ludd.infra.azure_containerapp_gpu import T4_PROFILE, GPUProfileSelection
from general_ludd.infra.azure_containerapp_preflight_types import (
    AzureContainerAppPreflightError,
    ContainerAppUsage,
    PreflightTrace,
)
from general_ludd.infra.azure_containerapp_preflight_usage import (
    read_supplementary_usages,
)

SELECTION = GPUProfileSelection(
    profile=T4_PROFILE,
    required_vram_mib=8_000,
    headroom_mib=T4_PROFILE.usable_vram_mib - 8_000,
)


class _Transport:
    def __init__(self, result: object) -> None:
        self.result = result
        self.paths: list[str] = []

    def get_json(self, path: str, _token: str) -> object:
        self.paths.append(path)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _refuse(_location: str, reason: str, message: str) -> NoReturn:
    raise AzureContainerAppPreflightError(f"{reason}: {message}")


def _read(
    transport: _Transport,
    traces: list[PreflightTrace],
    *,
    refuse: Callable[[str, str, str], NoReturn] = _refuse,
) -> tuple[ContainerAppUsage, ...]:
    return read_supplementary_usages(
        transport=transport,
        root="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.App/managedEnvironments/env",
        token="opaque-token",
        location="eastus",
        selection=SELECTION,
        emit=traces.append,
        refuse=refuse,
    )


def test_returns_validated_usage_records_and_emits_only_bounded_metadata() -> None:
    traces: list[PreflightTrace] = []
    transport = _Transport(
        {
            "value": [
                {
                    "name": {"value": "Managed Environment Consumption T4 Gpus"},
                    "currentValue": 1,
                    "limit": 3,
                    "unit": "Count",
                }
            ]
        }
    )

    usages = _read(transport, traces)

    assert len(usages) == 1
    assert usages[0].remaining == 2
    assert transport.paths[0].endswith("/usages?api-version=2026-01-01")
    assert traces == [
        PreflightTrace(
            "quota_discovered",
            "eastus",
            profile_name="T4",
            record_count=1,
            record_names=("Managed Environment Consumption T4 Gpus",),
        )
    ]


@pytest.mark.parametrize("status", [500, 503])
def test_server_failures_are_explicitly_tolerated_as_unavailable(status: int) -> None:
    traces: list[PreflightTrace] = []

    assert _read(
        _Transport(AzureContainerAppARMError("private", status_code=status)),
        traces,
    ) == ()
    assert traces[-1].reason == f"usages_http_{status}"


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "usages_unauthorized"), (403, "usages_unauthorized"), (404, "usages_not_found"), (429, "usages_http_429")],
)
def test_client_failures_refuse_with_a_stable_reason(status: int, reason: str) -> None:
    traces: list[PreflightTrace] = []

    with pytest.raises(AzureContainerAppPreflightError, match=reason):
        _read(
            _Transport(AzureContainerAppARMError("private", status_code=status)),
            traces,
        )


@pytest.mark.parametrize(
    "error",
    [AzureContainerAppARMError("private"), RuntimeError("private")],
)
def test_unclassified_read_failure_is_tolerated_without_leaking_detail(
    error: Exception,
) -> None:
    traces: list[PreflightTrace] = []

    assert _read(_Transport(error), traces) == ()
    assert traces[-1].reason == "usages_read_failed"
    assert "private" not in repr(traces)


def test_invalid_provider_payload_refuses_closed() -> None:
    with pytest.raises(AzureContainerAppPreflightError, match="usage_response_invalid"):
        _read(_Transport({"value": "invalid"}), [])
