"""Injected collaborator contracts for the Azure environment Terraform runtime."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from general_ludd.infra.azure_containerapp_environment_types import (
    AzureEnvironmentLifecyclePolicy,
)
from general_ludd.infra.azure_containerapp_terraform_executor import (
    TerraformRuntimeState,
)


@runtime_checkable
class EnvironmentTerraformExecutor(Protocol):
    """Execute one bounded, observable OpenTofu phase."""

    def run(
        self,
        *,
        phase: str,
        terraform_dir: str | os.PathLike[str],
        plan_file: str | os.PathLike[str],
        json_file: str | os.PathLike[str],
        allowed_root: str | os.PathLike[str],
        environment: dict[str, str],
        timeout_seconds: int,
        import_resource_id: str | None = None,
        progress: Callable[[str, TerraformRuntimeState, int], None],
    ) -> None:
        """Run one exact phase without a shell boundary."""
        ...


@runtime_checkable
class EnvironmentTerraformMaterializer(Protocol):
    """Write the reviewed environment module into its owner-bound directory."""

    def materialize(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        destination: str | os.PathLike[str],
    ) -> Path:
        """Materialize one typed policy and return the exact destination."""
        ...


__all__ = ("EnvironmentTerraformExecutor", "EnvironmentTerraformMaterializer")
