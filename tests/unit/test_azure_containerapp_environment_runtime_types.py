"""Structural contracts for injected Azure environment runtime collaborators."""

from __future__ import annotations

from general_ludd.infra.azure_containerapp_environment_runtime_types import (
    EnvironmentTerraformExecutor,
    EnvironmentTerraformMaterializer,
)


class _Executor:
    def run(self, **_kwargs: object) -> None:
        return None


class _Materializer:
    def materialize(self, *_args: object, **_kwargs: object) -> object:
        return object()


def test_runtime_collaborator_contracts_are_structurally_checkable() -> None:
    assert isinstance(_Executor(), EnvironmentTerraformExecutor)
    assert isinstance(_Materializer(), EnvironmentTerraformMaterializer)
    assert not isinstance(object(), EnvironmentTerraformExecutor)
    assert not isinstance(object(), EnvironmentTerraformMaterializer)
