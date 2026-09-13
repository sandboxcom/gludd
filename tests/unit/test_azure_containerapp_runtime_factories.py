"""Tests for explicit Azure runtime dependency selection."""

from __future__ import annotations

from general_ludd.infra.azure_containerapp_runtime_factories import (
    resolve_azure_containerapp_runtime_factories,
)


def test_explicit_runtime_factories_are_preserved_by_identity() -> None:
    """Tests and alternate adapters retain every explicitly supplied seam."""
    factories = tuple(lambda *args, **kwargs: (args, kwargs) for _index in range(9))

    resolved = resolve_azure_containerapp_runtime_factories(
        credential=factories[0],
        preflight=factories[1],
        app_runtime=factories[2],
        environment_runtime=factories[3],
        backend=factories[4],
        sdk_client=factories[5],
        sdk_transports=factories[6],
        monitor_client=factories[7],
        gpu_attestor=factories[8],
    )

    assert (
        resolved.credential,
        resolved.preflight,
        resolved.app_runtime,
        resolved.environment_runtime,
        resolved.backend,
        resolved.sdk_client,
        resolved.sdk_transports,
        resolved.monitor_client,
        resolved.gpu_attestor,
    ) == factories
