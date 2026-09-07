"""Materialize reviewed Terraform for one Gludd-owned Azure environment."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
)
from general_ludd.infra.azure_containerapp_make_types import (
    AzureContainerAppMakeRuntimeError,
)
from general_ludd.infra.azure_containerapp_make_validation import (
    OWNERSHIP_MARKER,
    read_bounded_json,
)

_STACK_NAME = "azure-container-app-environment"
_MODULE_NAME = "azure-container-app-environment"
_STACK_SOURCE = '../../modules/azure-container-app-environment'
_RUNTIME_SOURCE = './modules/azure-container-app-environment'
_OWNERSHIP_PROTOCOL = "gludd-azure-containerapp-live-proof-v1"
_MAX_MARKER_BYTES = 4096


def _terraform_assets_root() -> Path:
    source_tree = Path(__file__).resolve().parents[3] / "infra" / "terraform"
    if source_tree.is_dir():
        return source_tree
    packaged = Path(__file__).resolve().parents[1] / "terraform"
    if packaged.is_dir():
        return packaged
    raise AzureContainerAppMakeRuntimeError("assets")


def _require_regular_source(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise AzureContainerAppMakeRuntimeError("assets") from None
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise AzureContainerAppMakeRuntimeError("assets")


def _write_private_json(path: Path, value: object) -> None:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor: int | None = None
    try:
        temporary.unlink(missing_ok=True)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
    except OSError:
        raise AzureContainerAppMakeRuntimeError("materialize") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def verify_existing_state_boundary(path: Path, state_digest: str) -> None:
    """Refuse to adopt a nonempty Terraform root without exact ownership."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise AzureContainerAppMakeRuntimeError("state-ownership") from None
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise AzureContainerAppMakeRuntimeError("state-ownership")
    try:
        if next(path.iterdir(), None) is None:
            return
        marker_path = path / OWNERSHIP_MARKER
        marker_metadata = marker_path.lstat()
        marker = read_bounded_json(marker_path, phase="state-ownership")
        if (
            not stat.S_ISREG(marker_metadata.st_mode)
            or marker_path.is_symlink()
            or not 0 < marker_metadata.st_size <= _MAX_MARKER_BYTES
            or marker_metadata.st_mode & 0o077
            or not isinstance(marker, dict)
            or set(marker) != {"operation_digest", "protocol"}
            or marker.get("operation_digest") != state_digest
            or marker.get("protocol") != _OWNERSHIP_PROTOCOL
        ):
            raise ValueError
    except AzureContainerAppMakeRuntimeError:
        raise
    except (OSError, ValueError):
        raise AzureContainerAppMakeRuntimeError("state-ownership") from None


class AzureContainerAppEnvironmentTerraformMaterializer:
    """Materialize the reviewed environment stack without its remote backend."""

    def __init__(self, assets_root: str | os.PathLike[str] | None = None) -> None:
        """Bind an optional test asset root; production resolves packaged assets."""
        self._assets_root = (
            Path(assets_root).resolve() if assets_root is not None else None
        )

    def materialize(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        destination: str | os.PathLike[str],
    ) -> Path:
        """Copy exact reviewed HCL and atomically refresh public desired values."""
        if not isinstance(policy, AzureEnvironmentLifecyclePolicy):
            raise AzureContainerAppMakeRuntimeError("policy")
        destination_path = Path(destination).resolve()
        try:
            destination_path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(destination_path, 0o700)
        except OSError:
            raise AzureContainerAppMakeRuntimeError("materialize") from None
        assets = self._assets_root or _terraform_assets_root()
        stack = assets / "stacks" / _STACK_NAME
        module = assets / "modules" / _MODULE_NAME
        names = ("main.tf", "variables.tf", "outputs.tf")
        sources = [*(stack / name for name in names), *(module / name for name in names)]
        for source in sources:
            _require_regular_source(source)
        try:
            stack_main = (stack / "main.tf").read_text(encoding="utf-8")
            if stack_main.count(_STACK_SOURCE) != 1:
                raise ValueError
            (destination_path / "main.tf").write_text(
                stack_main.replace(_STACK_SOURCE, _RUNTIME_SOURCE),
                encoding="utf-8",
            )
            for name in names[1:]:
                shutil.copy2(stack / name, destination_path / name)
            module_destination = destination_path / "modules" / _MODULE_NAME
            module_destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name in names:
                shutil.copy2(module / name, module_destination / name)
        except (OSError, UnicodeError, ValueError):
            raise AzureContainerAppMakeRuntimeError("materialize") from None
        tfvars = {
            "environment_name": policy.environment_name,
            "resource_group_id": policy.resource_group_id,
            "region": policy.location,
            "workload_profiles": [
                {
                    "profile_name": profile.profile_name,
                    "workload_profile_type": profile.workload_profile_type,
                }
                for profile in policy.profiles
            ],
            "owner_digest": policy.owner_digest,
            "plan_digest": policy.plan_digest,
            "expires_at_utc": policy.expires_at_utc,
        }
        _write_private_json(destination_path / "terraform.tfvars.json", tfvars)
        return destination_path


__all__ = [
    "AzureContainerAppEnvironmentTerraformMaterializer",
    "verify_existing_state_boundary",
]
