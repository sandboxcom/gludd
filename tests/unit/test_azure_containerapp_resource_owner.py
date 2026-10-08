"""Ownership contracts for assembled Azure Container App runtime resources."""

from __future__ import annotations

from general_ludd.infra.azure_containerapp_resource_owner import (
    AzureContainerAppRuntimeResources,
)


def test_resource_owner_closes_every_client_and_releases_identity_last() -> None:
    """Idempotent cleanup preserves the acquisition/identity release boundary."""
    calls: list[str] = []

    class Client:
        def __init__(self, name: str) -> None:
            self.name = name

        def get_token(self, *_scopes: str) -> object:
            return object()

        def close(self) -> None:
            calls.append(self.name)

    resources = AzureContainerAppRuntimeResources(
        runtime=object(),  # type: ignore[arg-type]
        environment_runtime=object(),  # type: ignore[arg-type]
        credential=Client("credential"),
        environment_transport=Client("environment"),
        lifecycle_transport=Client("lifecycle"),
        app_transport=Client("app"),
        credential_release=lambda: calls.append("release"),
    )

    resources.close()
    resources.close()

    assert calls == ["app", "lifecycle", "environment", "credential", "release"]
