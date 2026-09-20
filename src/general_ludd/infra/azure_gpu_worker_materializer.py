"""Materialize owned OpenTofu roots for Azure GPU VM model workers.

The reviewed modules remain the infrastructure source of truth.  This boundary
copies exactly one module into an isolated root, adds only the constrained
AzureRM provider configuration, and writes public desired state with mode 0600.
Private SSH keys and cloud credentials never enter OpenTofu inputs or state.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from general_ludd.infra.azure_gpu_vm_strategy import AzureExecutionStrategy

_MODULE_BY_STRATEGY = {
    AzureExecutionStrategy.SINGLE_VM: "azure-gpu-worker",
    AzureExecutionStrategy.VMSS: "azure-gpu-vmss-worker",
}
_SOURCE_FILES = ("main.tf", "variables.tf", "outputs.tf")
_MARKER = ".gludd-azure-gpu-worker-owner.json"
_PROTOCOL = "gludd-azure-gpu-worker-v1"
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DEPLOYMENT_NAME = re.compile(r"^gludd-[a-z0-9][a-z0-9-]{2,34}$")
_RESOURCE_GROUP_NAME = re.compile(r"^[A-Za-z0-9._()-]{1,90}$")
_REGION = re.compile(r"^[a-z0-9-]{2,32}$")
_VM_SIZE = re.compile(r"^Standard_[A-Za-z0-9_]+$")
_USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
_SSH_PUBLIC_KEY = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) "
    r"[A-Za-z0-9+/=]+(?: .*)?$"
)
_IMAGE_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){2,3}$")
_OWNER_TOKEN = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_EXPIRES_AT = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
_IDENTITY_ID = re.compile(
    r"^/subscriptions/[0-9a-f-]{36}/resourceGroups/[A-Za-z0-9._()-]{1,90}/"
    r"providers/Microsoft\.ManagedIdentity/userAssignedIdentities/"
    r"[A-Za-z0-9._()-]{1,128}$",
    re.IGNORECASE,
)
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


def _bounded_text(value: object, name: str, *, maximum: int = 2_048) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError(f"{name} must be bounded non-empty text")
    return value


def _bounded_int(value: object, name: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _canonical_uuid(value: object, name: str) -> str:
    text = _bounded_text(value, name, maximum=36)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError):
        raise ValueError(f"{name} must be a canonical UUID") from None
    if str(parsed) != text.lower():
        raise ValueError(f"{name} must be a canonical UUID")
    return str(parsed)


def _validate_common(spec: AzureGpuWorkerProvisioningSpec) -> None:
    if spec.strategy not in _MODULE_BY_STRATEGY:
        raise ValueError("strategy must be single_vm or vmss")
    if _DEPLOYMENT_NAME.fullmatch(spec.deployment_name) is None:
        raise ValueError("deployment_name is not an owned Gludd label")
    subscription = _canonical_uuid(spec.subscription_id, "subscription_id")
    group = _bounded_text(spec.resource_group_name, "resource_group_name", maximum=90)
    if _RESOURCE_GROUP_NAME.fullmatch(group) is None:
        raise ValueError("resource_group_name is invalid")
    expected_group_id = f"/subscriptions/{subscription}/resourceGroups/{group}"
    if spec.resource_group_id.lower() != expected_group_id.lower():
        raise ValueError("resource_group_id does not match the exact subscription and group")
    if _REGION.fullmatch(spec.location) is None:
        raise ValueError("location is not canonical")
    if _VM_SIZE.fullmatch(spec.vm_size) is None:
        raise ValueError("vm_size is not a canonical Azure SKU")
    if not isinstance(spec.accelerated_networking_enabled, bool):
        raise ValueError("accelerated_networking_enabled must be bool")
    if _USERNAME.fullmatch(spec.admin_username) is None:
        raise ValueError("admin_username is invalid")
    public_key = _bounded_text(spec.ssh_public_key, "ssh_public_key", maximum=16_384)
    if _SSH_PUBLIC_KEY.fullmatch(public_key.strip()) is None:
        raise ValueError("ssh_public_key is invalid")
    private_path = Path(_bounded_text(spec.ssh_private_key_path, "ssh_private_key_path"))
    if not private_path.is_absolute():
        raise ValueError("ssh_private_key_path must be absolute")
    try:
        network = ipaddress.ip_network(spec.controller_cidr, strict=True)
    except ValueError:
        raise ValueError("controller_cidr must be one IPv4 /32") from None
    if network.version != 4 or network.prefixlen != 32:
        raise ValueError("controller_cidr must be one IPv4 /32")
    _bounded_int(spec.inference_port, "inference_port", 1_024, 65_535)
    if spec.inference_port == 22:
        raise ValueError("inference_port cannot be SSH")
    for value, name in (
        (spec.image_publisher, "image_publisher"),
        (spec.image_offer, "image_offer"),
        (spec.image_sku, "image_sku"),
    ):
        _bounded_text(value, name, maximum=256)
    if _IMAGE_VERSION.fullmatch(spec.image_version) is None:
        raise ValueError("image_version must be immutable and numeric")
    if _OWNER_TOKEN.fullmatch(spec.owner_token) is None:
        raise ValueError("owner_token is invalid")
    if _TRACE_ID.fullmatch(spec.trace_id) is None:
        raise ValueError("trace_id is invalid")
    if _EXPIRES_AT.fullmatch(spec.expires_at_utc) is None:
        raise ValueError("expires_at_utc is invalid")
    if not isinstance(spec.use_spot, bool):
        raise ValueError("use_spot must be bool")
    if isinstance(spec.max_spot_price, bool) or not isinstance(
        spec.max_spot_price, (int, float)
    ):
        raise ValueError("max_spot_price must be numeric")
    if spec.max_spot_price != -1 and spec.max_spot_price <= 0:
        raise ValueError("max_spot_price must be -1 or positive")
    _bounded_int(spec.os_disk_size_gb, "os_disk_size_gb", 64, 2_048)
    if spec.os_disk_storage_account_type not in {
        "Standard_LRS",
        "StandardSSD_LRS",
        "Premium_LRS",
    }:
        raise ValueError("os_disk_storage_account_type is unsupported")


def _validate_strategy(spec: AzureGpuWorkerProvisioningSpec) -> None:
    if spec.strategy is AzureExecutionStrategy.SINGLE_VM:
        if spec.instance_count != 1:
            raise ValueError("single VM requires exactly one instance")
        if spec.availability_zone not in {None, "1", "2", "3"}:
            raise ValueError("availability_zone is invalid")
        if spec.availability_zones or spec.user_assigned_identity_id is not None:
            raise ValueError("single VM cannot carry VMSS-only inputs")
        if spec.rdma_enabled or spec.cache_disk_enabled:
            raise ValueError("single VM cannot carry VMSS storage/topology inputs")
        return
    _bounded_int(spec.instance_count, "instance_count", 2, 100)
    if spec.availability_zone is not None:
        raise ValueError("VMSS cannot carry a single-VM zone")
    if (
        len(spec.availability_zones) > 3
        or len(set(spec.availability_zones)) != len(spec.availability_zones)
        or any(zone not in {"1", "2", "3"} for zone in spec.availability_zones)
    ):
        raise ValueError("availability_zones are invalid")
    if spec.rdma_enabled and len(spec.availability_zones) > 1:
        raise ValueError("RDMA VMSS must remain in one zone")
    identity = spec.user_assigned_identity_id
    if not isinstance(identity, str) or _IDENTITY_ID.fullmatch(identity) is None:
        raise ValueError("VMSS requires one canonical user-assigned identity")
    if not identity.lower().startswith(f"{spec.resource_group_id.lower()}/providers/"):
        raise ValueError("VMSS identity must be in the delegated resource group")
    if spec.cache_disk_enabled and not spec.cache_price_attested:
        raise ValueError("cache storage requires independent price attestation")
    _bounded_int(spec.cache_disk_size_gb, "cache_disk_size_gb", 32, 32_767)
    if spec.cache_disk_storage_account_type not in {"StandardSSD_LRS", "Premium_LRS"}:
        raise ValueError("cache_disk_storage_account_type is unsupported")


@dataclass(frozen=True, slots=True)
class AzureGpuWorkerProvisioningSpec:
    """Immutable public desired state for one Azure VM or Uniform VMSS pool."""

    strategy: AzureExecutionStrategy
    deployment_name: str
    subscription_id: str
    resource_group_id: str
    resource_group_name: str
    location: str
    vm_size: str
    instance_count: int
    accelerated_networking_enabled: bool
    admin_username: str
    ssh_public_key: str
    ssh_private_key_path: str
    controller_cidr: str
    inference_port: int
    image_publisher: str
    image_offer: str
    image_sku: str
    image_version: str
    owner_token: str
    trace_id: str
    expires_at_utc: str
    availability_zone: str | None = None
    availability_zones: tuple[str, ...] = ()
    user_assigned_identity_id: str | None = None
    rdma_enabled: bool = False
    use_spot: bool = False
    max_spot_price: float = -1
    os_disk_storage_account_type: str = "Premium_LRS"
    os_disk_size_gb: int = 128
    cache_disk_enabled: bool = False
    cache_price_attested: bool = False
    cache_disk_storage_account_type: str = "Premium_LRS"
    cache_disk_size_gb: int = 256

    def __post_init__(self) -> None:
        """Reject inferred, unbounded, mutable, or cross-scope desired state."""
        _validate_common(self)
        _validate_strategy(self)

    def terraform_variables(self) -> dict[str, object]:
        """Return the exact public variable set accepted by the selected module."""
        values: dict[str, object] = {
            "deployment_name": self.deployment_name,
            "resource_group_id": self.resource_group_id,
            "resource_group_name": self.resource_group_name,
            "location": self.location,
            "vm_size": self.vm_size,
            "accelerated_networking_enabled": self.accelerated_networking_enabled,
            "admin_username": self.admin_username,
            "ssh_public_key": self.ssh_public_key,
            "controller_cidr": self.controller_cidr,
            "inference_port": self.inference_port,
            "os_disk_storage_account_type": self.os_disk_storage_account_type,
            "os_disk_size_gb": self.os_disk_size_gb,
            "image_publisher": self.image_publisher,
            "image_offer": self.image_offer,
            "image_sku": self.image_sku,
            "image_version": self.image_version,
            "use_spot": self.use_spot,
            "max_spot_price": self.max_spot_price,
            "owner_token": self.owner_token,
            "trace_id": self.trace_id,
            "expires_at_utc": self.expires_at_utc,
        }
        if self.strategy is AzureExecutionStrategy.SINGLE_VM:
            values["availability_zone"] = self.availability_zone
            return values
        values.update(
            {
                "instance_count": self.instance_count,
                "availability_zones": list(self.availability_zones),
                "rdma_enabled": self.rdma_enabled,
                "user_assigned_identity_id": self.user_assigned_identity_id,
                "cache_disk_enabled": self.cache_disk_enabled,
                "cache_price_attested": self.cache_price_attested,
                "cache_disk_storage_account_type": (
                    self.cache_disk_storage_account_type
                ),
                "cache_disk_size_gb": self.cache_disk_size_gb,
            }
        )
        return values


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
