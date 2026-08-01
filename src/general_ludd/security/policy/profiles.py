"""Strict, immutable and monotonic sandbox policy resolution.

The built-in profile is a secure baseline.  An administrator may explicitly
configure the approved envelope; every subsequent scope is validated as a
narrowing of that envelope.  The resulting canonical JSON and SHA-256 digest
are stable inputs to runtime attestation and zero-downtime policy pinning.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

BackendName: TypeAlias = Literal["firecracker", "gvisor", "nsjail", "bubblewrap"]
BackendStrength: TypeAlias = Literal[
    "process-isolation", "application-kernel", "virtual-machine"
]
Posture: TypeAlias = Literal["locked", "standard", "development"]
AccessMode: TypeAlias = Literal["deny", "read-only", "read-write"]
NetworkMode: TypeAlias = Literal["deny", "allowlist", "proxy"]
FallbackMode: TypeAlias = Literal["deny", "audit"]
SecretMode: TypeAlias = Literal["none", "brokered"]
PolicyScope: TypeAlias = Literal["administrator", "user", "project", "agent", "work_item"]

PositiveInt = Annotated[int, Field(strict=True, gt=0)]
PositiveFloat = Annotated[float, Field(strict=True, gt=0)]

_HOST_RE = re.compile(
    r"(?=^.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_BACKEND_STRENGTH = {
    "process-isolation": 0,
    "application-kernel": 1,
    "virtual-machine": 2,
}
_ACCESS_STRENGTH = {"deny": 0, "read-only": 1, "read-write": 2}
_SECRET_STRENGTH = {"none": 0, "brokered": 1}


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _tuple_input(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


def _sorted_unique_strings(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} contains duplicate entries")
    if any(not value or len(value) > 512 for value in values):
        raise ValueError(f"{field_name} entries must contain 1..512 characters")
    return tuple(sorted(values))


class BackendPolicy(_StrictFrozenModel):
    preference: tuple[BackendName, ...]
    minimum_strength: BackendStrength
    require_attestation: StrictBool
    fallback: FallbackMode

    @field_validator("preference", mode="before")
    @classmethod
    def _normalize_preference_input(cls, value: object) -> object:
        return _tuple_input(value)

    @field_validator("preference")
    @classmethod
    def _validate_preference(
        cls, value: tuple[BackendName, ...]
    ) -> tuple[BackendName, ...]:
        if not value:
            raise ValueError("backend.preference must not be empty")
        if len(value) != len(set(value)):
            raise ValueError("backend.preference contains duplicate entries")
        return value


class FilesystemPolicy(_StrictFrozenModel):
    workspace: AccessMode
    source: AccessMode
    host_paths: tuple[str, ...]
    max_bytes: PositiveInt
    max_inodes: PositiveInt

    @field_validator("host_paths", mode="before")
    @classmethod
    def _normalize_host_path_input(cls, value: object) -> object:
        return _tuple_input(value)

    @field_validator("host_paths")
    @classmethod
    def _validate_host_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _sorted_unique_strings(value, field_name="filesystem.host_paths")
        if any(not path.startswith("/") for path in normalized):
            raise ValueError("filesystem.host_paths entries must be absolute")
        return normalized


class NetworkPolicy(_StrictFrozenModel):
    mode: NetworkMode
    hosts: tuple[str, ...]
    cidrs: tuple[str, ...]
    ports: tuple[Annotated[int, Field(strict=True, ge=1, le=65535)], ...]
    max_connections: Annotated[int, Field(strict=True, ge=1, le=100_000)]
    max_bytes: PositiveInt
    deny_metadata: StrictBool

    @field_validator("hosts", "cidrs", "ports", mode="before")
    @classmethod
    def _normalize_sequence_input(cls, value: object) -> object:
        return _tuple_input(value)

    @field_validator("hosts")
    @classmethod
    def _validate_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        lowered = tuple(host.casefold().rstrip(".") for host in value)
        normalized = _sorted_unique_strings(lowered, field_name="network.hosts")
        if any(_HOST_RE.fullmatch(host) is None for host in normalized):
            raise ValueError("network.hosts contains an invalid hostname")
        return normalized

    @field_validator("cidrs")
    @classmethod
    def _validate_cidrs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("network.cidrs contains duplicate entries")
        try:
            networks = tuple(str(ipaddress.ip_network(cidr, strict=True)) for cidr in value)
        except ValueError as error:
            raise ValueError("network.cidrs contains a non-canonical CIDR") from error
        if len(networks) != len(set(networks)):
            raise ValueError("network.cidrs contains duplicate networks")
        return tuple(sorted(networks))

    @field_validator("ports")
    @classmethod
    def _validate_ports(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(value) != len(set(value)):
            raise ValueError("network.ports contains duplicate entries")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def _deny_has_no_grants(self) -> NetworkPolicy:
        if self.mode == "deny" and (self.hosts or self.cidrs or self.ports):
            raise ValueError("network.mode=deny contradicts destination grants")
        return self


class ProcessPolicy(_StrictFrozenModel):
    executable_allowlist: tuple[str, ...]
    syscall_profile: Annotated[str, Field(min_length=1, max_length=128, pattern=_NAME_RE.pattern)]
    max_pids: Annotated[int, Field(strict=True, ge=1, le=65_536)]
    no_new_privileges: StrictBool

    @field_validator("executable_allowlist", mode="before")
    @classmethod
    def _normalize_executable_input(cls, value: object) -> object:
        return _tuple_input(value)

    @field_validator("executable_allowlist")
    @classmethod
    def _validate_executables(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _sorted_unique_strings(value, field_name="process.executable_allowlist")
        if any(not executable.startswith("/") for executable in normalized):
            raise ValueError("process.executable_allowlist entries must be absolute")
        return normalized


class ResourcePolicy(_StrictFrozenModel):
    cpu_quota: Annotated[float, Field(strict=True, gt=0, le=1024)]
    cpu_seconds: PositiveInt
    wall_seconds: PositiveInt
    memory_bytes: PositiveInt
    output_bytes: PositiveInt
    open_files: Annotated[int, Field(strict=True, ge=3, le=1_048_576)]

    @model_validator(mode="after")
    def _wall_time_covers_cpu_time(self) -> ResourcePolicy:
        if self.wall_seconds < self.cpu_seconds:
            raise ValueError("resources.wall_seconds must be >= resources.cpu_seconds")
        return self


class SecretsPolicy(_StrictFrozenModel):
    mode: SecretMode
    max_ttl_seconds: Annotated[int, Field(strict=True, ge=1, le=86_400)]
    allowed_refs: tuple[str, ...]

    @field_validator("allowed_refs", mode="before")
    @classmethod
    def _normalize_ref_input(cls, value: object) -> object:
        return _tuple_input(value)

    @field_validator("allowed_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _sorted_unique_strings(value, field_name="secrets.allowed_refs")
        if any(ref.startswith("/") or ".." in ref.split("/") for ref in normalized):
            raise ValueError("secrets.allowed_refs must be opaque non-traversing references")
        return normalized

    @model_validator(mode="after")
    def _none_has_no_refs(self) -> SecretsPolicy:
        if self.mode == "none" and self.allowed_refs:
            raise ValueError("secrets.mode=none contradicts allowed_refs")
        return self


class AuditPolicy(_StrictFrozenModel):
    sink: Literal["durable"]
    heartbeat_seconds: Annotated[int, Field(strict=True, ge=1, le=3600)]
    include_denials: StrictBool


class SandboxProfile(_StrictFrozenModel):
    """Complete version-1 sandbox policy; partial/unknown policies never dispatch."""

    schema_version: Literal[1]
    posture: Posture
    profile: Annotated[str, Field(min_length=1, max_length=64, pattern=_NAME_RE.pattern)]
    backend: BackendPolicy
    filesystem: FilesystemPolicy
    network: NetworkPolicy
    process: ProcessPolicy
    resources: ResourcePolicy
    secrets: SecretsPolicy
    audit: AuditPolicy

    @model_validator(mode="after")
    def _validate_posture_contract(self) -> SandboxProfile:
        if self.posture == "locked":
            if self.backend.fallback != "deny":
                raise ValueError("locked posture forbids audit fallback")
            if not self.backend.require_attestation:
                raise ValueError("locked posture requires runtime attestation")
            if _BACKEND_STRENGTH[self.backend.minimum_strength] < _BACKEND_STRENGTH[
                "application-kernel"
            ]:
                raise ValueError("locked posture requires application-kernel strength or better")
        if self.posture != "development" and self.backend.fallback == "audit":
            raise ValueError("audit fallback is development-only")
        return self


@dataclass(frozen=True)
class PolicyLayer:
    """One non-administrator override scope, defensively copied on construction."""

    scope: PolicyScope
    values: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.scope not in {"administrator", "user", "project", "agent", "work_item"}:
            raise ValueError(f"unsupported policy scope: {self.scope}")
        if not isinstance(self.values, Mapping):
            raise TypeError("policy layer values must be a mapping")
        object.__setattr__(self, "values", MappingProxyType(copy.deepcopy(dict(self.values))))


@dataclass(frozen=True)
class ResolvedSandboxProfile:
    requested_profile: str
    policy: SandboxProfile
    canonical_json: str
    policy_hash: str
    applied_layers: tuple[str, ...]

    @property
    def policy_version(self) -> str:
        return f"v{self.policy.schema_version}:{self.policy_hash}"


class PolicyWideningError(ValueError):
    """Raised when a non-administrator layer expands its approved envelope."""

    def __init__(self, scope: str, paths: tuple[str, ...]) -> None:
        self.scope = scope
        self.paths = paths
        super().__init__(f"{scope} policy widens protected fields: {', '.join(paths)}")


def _profile_data(
    *,
    posture: Posture,
    minimum_strength: BackendStrength,
    fallback: FallbackMode,
    require_attestation: bool,
    cpu_quota: float,
    memory_bytes: int,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "posture": posture,
        "profile": "untrusted-code" if posture != "development" else "trusted-development",
        "backend": {
            "preference": ["firecracker", "gvisor", "nsjail", "bubblewrap"],
            "minimum_strength": minimum_strength,
            "require_attestation": require_attestation,
            "fallback": fallback,
        },
        "filesystem": {
            "workspace": "read-write",
            "source": "read-only",
            "host_paths": [],
            "max_bytes": 1_073_741_824,
            "max_inodes": 100_000,
        },
        "network": {
            "mode": "deny",
            "hosts": [],
            "cidrs": [],
            "ports": [],
            "max_connections": 32,
            "max_bytes": 104_857_600,
            "deny_metadata": True,
        },
        "process": {
            "executable_allowlist": [],
            "syscall_profile": "untrusted-code-v1",
            "max_pids": 64,
            "no_new_privileges": True,
        },
        "resources": {
            "cpu_quota": cpu_quota,
            "cpu_seconds": 300,
            "wall_seconds": 360,
            "memory_bytes": memory_bytes,
            "output_bytes": 1_000_000,
            "open_files": 256,
        },
        "secrets": {"mode": "brokered", "max_ttl_seconds": 900, "allowed_refs": []},
        "audit": {"sink": "durable", "heartbeat_seconds": 10, "include_denials": True},
    }


_BUILTINS = {
    "locked": SandboxProfile.model_validate(
        _profile_data(
            posture="locked",
            minimum_strength="application-kernel",
            fallback="deny",
            require_attestation=True,
            cpu_quota=1.0,
            memory_bytes=536_870_912,
        )
    ),
    "standard": SandboxProfile.model_validate(
        _profile_data(
            posture="standard",
            minimum_strength="process-isolation",
            fallback="deny",
            require_attestation=True,
            cpu_quota=2.0,
            memory_bytes=1_073_741_824,
        )
    ),
    "development": SandboxProfile.model_validate(
        _profile_data(
            posture="development",
            minimum_strength="process-isolation",
            fallback="audit",
            require_attestation=False,
            cpu_quota=4.0,
            memory_bytes=2_147_483_648,
        )
    ),
}
BUILTIN_SANDBOX_PROFILES: Mapping[str, SandboxProfile] = MappingProxyType(_BUILTINS)


def _deep_merge(base: Mapping[str, object], override: Mapping[str, object]) -> dict[str, object]:
    merged = copy.deepcopy(dict(base))
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _subset_widened(parent: tuple[object, ...], child: tuple[object, ...]) -> bool:
    return not set(child).issubset(parent)


def _find_widening_paths(parent: SandboxProfile, child: SandboxProfile) -> tuple[str, ...]:
    paths: list[str] = []
    if _subset_widened(parent.backend.preference, child.backend.preference):
        paths.append("backend.preference")
    if _BACKEND_STRENGTH[child.backend.minimum_strength] < _BACKEND_STRENGTH[
        parent.backend.minimum_strength
    ]:
        paths.append("backend.minimum_strength")
    if parent.backend.require_attestation and not child.backend.require_attestation:
        paths.append("backend.require_attestation")
    if parent.backend.fallback == "deny" and child.backend.fallback == "audit":
        paths.append("backend.fallback")

    for field_name in ("workspace", "source"):
        if _ACCESS_STRENGTH[getattr(child.filesystem, field_name)] > _ACCESS_STRENGTH[
            getattr(parent.filesystem, field_name)
        ]:
            paths.append(f"filesystem.{field_name}")
    if _subset_widened(parent.filesystem.host_paths, child.filesystem.host_paths):
        paths.append("filesystem.host_paths")
    for field_name in ("max_bytes", "max_inodes"):
        if getattr(child.filesystem, field_name) > getattr(parent.filesystem, field_name):
            paths.append(f"filesystem.{field_name}")

    if child.network.mode != parent.network.mode and child.network.mode != "deny":
        paths.append("network.mode")
    for field_name in ("hosts", "cidrs", "ports"):
        if _subset_widened(
            getattr(parent.network, field_name), getattr(child.network, field_name)
        ):
            paths.append(f"network.{field_name}")
    for field_name in ("max_connections", "max_bytes"):
        if getattr(child.network, field_name) > getattr(parent.network, field_name):
            paths.append(f"network.{field_name}")
    if parent.network.deny_metadata and not child.network.deny_metadata:
        paths.append("network.deny_metadata")

    if _subset_widened(
        parent.process.executable_allowlist, child.process.executable_allowlist
    ):
        paths.append("process.executable_allowlist")
    if child.process.syscall_profile != parent.process.syscall_profile:
        paths.append("process.syscall_profile")
    if child.process.max_pids > parent.process.max_pids:
        paths.append("process.max_pids")
    if parent.process.no_new_privileges and not child.process.no_new_privileges:
        paths.append("process.no_new_privileges")

    for field_name in (
        "cpu_quota",
        "cpu_seconds",
        "wall_seconds",
        "memory_bytes",
        "output_bytes",
        "open_files",
    ):
        if getattr(child.resources, field_name) > getattr(parent.resources, field_name):
            paths.append(f"resources.{field_name}")

    if _SECRET_STRENGTH[child.secrets.mode] > _SECRET_STRENGTH[parent.secrets.mode]:
        paths.append("secrets.mode")
    if child.secrets.max_ttl_seconds > parent.secrets.max_ttl_seconds:
        paths.append("secrets.max_ttl_seconds")
    if _subset_widened(parent.secrets.allowed_refs, child.secrets.allowed_refs):
        paths.append("secrets.allowed_refs")
    if child.audit.heartbeat_seconds > parent.audit.heartbeat_seconds:
        paths.append("audit.heartbeat_seconds")
    if parent.audit.include_denials and not child.audit.include_denials:
        paths.append("audit.include_denials")
    return tuple(sorted(set(paths)))


def _raw_widening_paths(
    parent: Mapping[str, object], child: Mapping[str, object]
) -> tuple[str, ...]:
    """Detect obvious widening before posture validation can mask the cause."""

    paths: list[str] = []

    def nested(name: str, field_name: str) -> tuple[object, object]:
        parent_section = parent.get(name, {})
        child_section = child.get(name, {})
        if not isinstance(parent_section, Mapping) or not isinstance(child_section, Mapping):
            return None, None
        return parent_section.get(field_name), child_section.get(field_name)

    for section, field_name in (
        ("filesystem", "max_bytes"),
        ("filesystem", "max_inodes"),
        ("network", "max_connections"),
        ("network", "max_bytes"),
        ("process", "max_pids"),
        ("resources", "cpu_quota"),
        ("resources", "cpu_seconds"),
        ("resources", "wall_seconds"),
        ("resources", "memory_bytes"),
        ("resources", "output_bytes"),
        ("resources", "open_files"),
        ("secrets", "max_ttl_seconds"),
        ("audit", "heartbeat_seconds"),
    ):
        old, new = nested(section, field_name)
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) and new > old:
            paths.append(f"{section}.{field_name}")

    for section, field_name in (
        ("network", "hosts"),
        ("network", "cidrs"),
        ("network", "ports"),
        ("filesystem", "host_paths"),
        ("process", "executable_allowlist"),
        ("secrets", "allowed_refs"),
        ("backend", "preference"),
    ):
        old, new = nested(section, field_name)
        if (
            isinstance(old, (list, tuple))
            and isinstance(new, (list, tuple))
            and not set(new).issubset(old)
        ):
            paths.append(f"{section}.{field_name}")

    old_mode, new_mode = nested("network", "mode")
    if new_mode != old_mode and new_mode != "deny":
        paths.append("network.mode")
    old_attestation, new_attestation = nested("backend", "require_attestation")
    if old_attestation is True and new_attestation is False:
        paths.append("backend.require_attestation")
    return tuple(sorted(set(paths)))


def _canonicalize(policy: SandboxProfile) -> tuple[str, str]:
    canonical = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reject_identity_override(scope: str, values: Mapping[str, object]) -> None:
    paths = tuple(sorted({key for key in values if key in {"schema_version", "posture", "profile"}}))
    if paths:
        raise PolicyWideningError(scope, paths)


def resolve_sandbox_profile(
    requested_profile: Literal["locked", "standard", "development"] = "locked",
    *,
    administrator: Mapping[str, object] | None = None,
    layers: tuple[PolicyLayer, ...] = (),
) -> ResolvedSandboxProfile:
    """Resolve a strict profile and reject every post-administrator widening."""

    base = BUILTIN_SANDBOX_PROFILES[requested_profile]
    current_data = base.model_dump(mode="python")
    applied = ["builtin"]

    if administrator is not None:
        if not isinstance(administrator, Mapping):
            raise TypeError("administrator policy must be a mapping")
        _reject_identity_override("administrator", administrator)
        current_data = _deep_merge(current_data, administrator)
        current = SandboxProfile.model_validate(current_data)
        if administrator:
            applied.append("administrator")
    else:
        current = base

    for layer in layers:
        if layer.scope == "administrator":
            raise ValueError("administrator policy must use the administrator argument")
        _reject_identity_override(layer.scope, layer.values)
        candidate_data = _deep_merge(current.model_dump(mode="python"), layer.values)
        raw_widening = _raw_widening_paths(current.model_dump(mode="python"), candidate_data)
        if raw_widening:
            raise PolicyWideningError(layer.scope, raw_widening)
        candidate = SandboxProfile.model_validate(candidate_data)
        widening = _find_widening_paths(current, candidate)
        if widening:
            raise PolicyWideningError(layer.scope, widening)
        current = candidate
        current_data = candidate_data
        applied.append(layer.scope)

    canonical, digest = _canonicalize(current)
    return ResolvedSandboxProfile(
        requested_profile=requested_profile,
        policy=current,
        canonical_json=canonical,
        policy_hash=digest,
        applied_layers=tuple(applied),
    )


__all__ = [
    "BUILTIN_SANDBOX_PROFILES",
    "BackendName",
    "BackendStrength",
    "PolicyLayer",
    "PolicyWideningError",
    "ResolvedSandboxProfile",
    "SandboxProfile",
    "resolve_sandbox_profile",
]
