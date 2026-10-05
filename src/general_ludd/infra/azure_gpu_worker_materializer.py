"""Materialize owned OpenTofu roots for Azure GPU VM model workers.

The reviewed modules remain the infrastructure source of truth.  This boundary
copies exactly one module into an isolated root, adds only the constrained
AzureRM provider configuration, and writes public desired state with mode 0600.
Private SSH keys and cloud credentials never enter OpenTofu inputs or state.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

from general_ludd.infra.azure_gpu_worker_materializer_contracts import (
    _HEX_DIGEST,
    _MODULE_BY_STRATEGY,
    AzureGpuWorkerProvisioningSpec,
)

_SOURCE_FILES = ("main.tf", "variables.tf", "outputs.tf")
_MARKER = ".gludd-azure-gpu-worker-owner.json"
_PROTOCOL = "gludd-azure-gpu-worker-v1"
_PROVIDER = """provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
}
"""


class AzureGpuWorkerMaterializerError(RuntimeError):
    """Censored materialization failure that never carries input values."""

    def __init__(self, phase: str) -> None:
        """Retain one bounded failure phase only."""
        self.phase = phase
        super().__init__(f"Azure GPU worker materialization failed: {phase}")




def _assets_root() -> Path:
    source_tree = Path(__file__).resolve().parents[3] / "infra" / "terraform"
    if source_tree.is_dir():
        return source_tree
    packaged = Path(__file__).resolve().parents[1] / "terraform"
    if packaged.is_dir():
        return packaged
    raise AzureGpuWorkerMaterializerError("assets")


def _regular_source(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise AzureGpuWorkerMaterializerError("assets") from None
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise AzureGpuWorkerMaterializerError("assets")


def _private_json(path: Path, value: object) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
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
        raise AzureGpuWorkerMaterializerError("materialize") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _verify_state_boundary(destination: Path, operation_digest: str) -> None:
    try:
        metadata = destination.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise AzureGpuWorkerMaterializerError("state-ownership") from None
    if not stat.S_ISDIR(metadata.st_mode) or destination.is_symlink():
        raise AzureGpuWorkerMaterializerError("state-ownership")
    try:
        if next(destination.iterdir(), None) is None:
            return
        marker_path = destination / _MARKER
        marker_metadata = marker_path.lstat()
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if (
            not stat.S_ISREG(marker_metadata.st_mode)
            or marker_path.is_symlink()
            or marker_metadata.st_size > 4_096
            or marker_metadata.st_mode & 0o077
            or marker
            != {"operation_digest": operation_digest, "protocol": _PROTOCOL}
        ):
            raise ValueError
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise AzureGpuWorkerMaterializerError("state-ownership") from None


class AzureGpuWorkerTerraformMaterializer:
    """Copy one reviewed Azure GPU worker module into an owned root."""

    def __init__(self, assets_root: str | os.PathLike[str] | None = None) -> None:
        """Bind an optional test asset root; production resolves packaged assets."""
        self._assets_root = (
            Path(assets_root).resolve() if assets_root is not None else None
        )

    def materialize(
        self,
        spec: AzureGpuWorkerProvisioningSpec,
        destination: str | os.PathLike[str],
        *,
        operation_digest: str,
    ) -> Path:
        """Write an owned root and exact public tfvars for one immutable spec."""
        if not isinstance(spec, AzureGpuWorkerProvisioningSpec):
            raise AzureGpuWorkerMaterializerError("spec")
        if not isinstance(operation_digest, str) or _HEX_DIGEST.fullmatch(
            operation_digest
        ) is None:
            raise AzureGpuWorkerMaterializerError("operation-digest")
        unresolved = Path(destination)
        if unresolved.is_symlink():
            raise AzureGpuWorkerMaterializerError("state-ownership")
        destination_path = unresolved.resolve()
        _verify_state_boundary(destination_path, operation_digest)
        module_name = _MODULE_BY_STRATEGY[spec.strategy]
        module = (self._assets_root or _assets_root()) / "modules" / module_name
        sources = tuple(module / name for name in _SOURCE_FILES)
        for source in sources:
            _regular_source(source)
        try:
            destination_path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(destination_path, 0o700)
            for source in sources:
                shutil.copy2(source, destination_path / source.name)
            (destination_path / "provider.tf").write_text(_PROVIDER, encoding="utf-8")
            _private_json(
                destination_path / "terraform.tfvars.json",
                spec.terraform_variables(),
            )
            _private_json(
                destination_path / _MARKER,
                {"operation_digest": operation_digest, "protocol": _PROTOCOL},
            )
        except AzureGpuWorkerMaterializerError:
            raise
        except (OSError, UnicodeError):
            raise AzureGpuWorkerMaterializerError("materialize") from None
        return destination_path


__all__ = (
    "AzureGpuWorkerMaterializerError",
    "AzureGpuWorkerProvisioningSpec",
    "AzureGpuWorkerTerraformMaterializer",
)
