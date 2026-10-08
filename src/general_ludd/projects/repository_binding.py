"""Stable, host-local repository bindings for project execution."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, cast

from general_ludd.projects.workspace import (
    confine_workspace_path,
    default_workspace_base,
)

_BINDING_SCHEMA_VERSION: Final = 1
_REGISTRY_SCHEMA_VERSION: Final = 1
_MAX_BINDING_BYTES: Final = 4_096
_MAX_REGISTRY_BYTES: Final = 262_144
_MAX_REGISTRY_ENTRIES: Final = 1_000
_PROJECT_ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_DIGEST_RE: Final = re.compile(r"[0-9a-f]{64}\Z")


class ProjectRepositoryUnavailable(ValueError):
    """Raised when a configured project repository cannot be resolved."""


class ProjectRepositoryBindingStale(ValueError):
    """Raised when a job references a different binding generation."""


def _duplicate_safe_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"repository binding contains duplicate field: {key}")
        value[key] = item
    return value


def _project_id(value: object) -> str:
    if not isinstance(value, str) or _PROJECT_ID_RE.fullmatch(value) is None:
        raise ValueError("project_id must be a bounded identifier")
    return value


def _workspace_key(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > 512
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError("workspace_key must be a bounded relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("workspace_key must be confined beneath the workspace base")
    if path.as_posix() != value:
        raise ValueError("workspace_key must use canonical POSIX spelling")
    return value


def repository_fingerprint(locator: object) -> str:
    """Return a stable non-secret fingerprint for one configured repository."""
    if (
        not isinstance(locator, str)
        or not locator
        or locator != locator.strip()
        or "\x00" in locator
        or "\n" in locator
        or "\r" in locator
        or len(locator.encode("utf-8")) > 4_096
    ):
        raise ValueError("repository locator must be bounded canonical text")
    return hashlib.sha256(locator.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectRepositoryBinding:
    """Bind one stable project identity to a confined host-local workspace key."""

    project_id: str
    workspace_key: str
    repository_fingerprint: str

    def __post_init__(self) -> None:
        """Reject ambiguous identities and path traversal before resolution."""
        _project_id(self.project_id)
        _workspace_key(self.workspace_key)
        if (
            not isinstance(self.repository_fingerprint, str)
            or _DIGEST_RE.fullmatch(self.repository_fingerprint) is None
        ):
            raise ValueError("repository_fingerprint must be a lowercase SHA-256 digest")

    @classmethod
    def for_project(
        cls,
        *,
        project_id: str,
        workspace_path: str,
        repo_url: str,
    ) -> ProjectRepositoryBinding:
        """Build one binding from persisted project configuration."""
        pid = _project_id(project_id)
        key = _workspace_key(workspace_path or pid)
        locator = repo_url.strip() if isinstance(repo_url, str) and repo_url.strip() else f"project:{pid}"
        return cls(
            project_id=pid,
            workspace_key=key,
            repository_fingerprint=repository_fingerprint(locator),
        )

    @property
    def digest(self) -> str:
        """Return the canonical path-independent binding identity."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        """Serialize this binding without a host filesystem path."""
        raw = json.dumps(
            {
                "project_id": self.project_id,
                "repository_fingerprint": self.repository_fingerprint,
                "schema_version": _BINDING_SCHEMA_VERSION,
                "workspace_key": self.workspace_key,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(raw.encode("utf-8")) > _MAX_BINDING_BYTES:
            raise ValueError("repository binding exceeds its bounded representation")
        return raw

    @classmethod
    def from_json(cls, raw: object) -> ProjectRepositoryBinding:
        """Hydrate one exact, duplicate-safe binding representation."""
        if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > _MAX_BINDING_BYTES:
            raise ValueError("repository binding must be bounded JSON text")
        try:
            value = json.loads(raw, object_pairs_hook=_duplicate_safe_object)
        except json.JSONDecodeError as exc:
            raise ValueError("repository binding is malformed JSON") from exc
        fields = {
            "project_id",
            "repository_fingerprint",
            "schema_version",
            "workspace_key",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("repository binding fields are malformed")
        mapping = cast(dict[str, object], value)
        if mapping["schema_version"] != _BINDING_SCHEMA_VERSION:
            raise ValueError("repository binding schema version is unsupported")
        binding = cls(
            project_id=_project_id(mapping["project_id"]),
            workspace_key=_workspace_key(mapping["workspace_key"]),
            repository_fingerprint=cast(str, mapping["repository_fingerprint"]),
        )
        if binding.to_json() != raw:
            raise ValueError("repository binding JSON is not canonical")
        return binding


class ProjectRepositoryRegistry:
    """Immutable project binding snapshot rooted in one host-local base."""

    def __init__(
        self,
        bindings: Iterable[ProjectRepositoryBinding] = (),
        *,
        base_dir: str | Path | None = None,
    ) -> None:
        """Validate and snapshot unique bindings without touching repositories."""
        resolved_base = Path(base_dir or default_workspace_base()).expanduser().resolve(strict=False)
        values: dict[str, ProjectRepositoryBinding] = {}
        for binding in bindings:
            if not isinstance(binding, ProjectRepositoryBinding):
                raise ValueError("registry entries must be ProjectRepositoryBinding values")
            if binding.project_id in values:
                raise ValueError(f"duplicate project repository binding: {binding.project_id}")
            if len(values) >= _MAX_REGISTRY_ENTRIES:
                raise ValueError("project repository registry exceeds its entry limit")
            values[binding.project_id] = binding
        self._base_dir = resolved_base
        self._bindings = MappingProxyType(values)

    @property
    def base_dir(self) -> Path:
        """Return this host's canonical workspace base."""
        return self._base_dir

    def get(self, project_id: str) -> ProjectRepositoryBinding | None:
        """Return one configured binding without resolving its filesystem root."""
        return self._bindings.get(project_id)

    def resolve(self, project_id: str, expected_digest: str) -> Path:
        """Resolve a matching binding beneath this host's workspace base."""
        binding = self.get(project_id)
        if binding is None:
            raise ProjectRepositoryUnavailable("project repository binding is unavailable")
        if (
            not isinstance(expected_digest, str)
            or _DIGEST_RE.fullmatch(expected_digest) is None
            or not hmac.compare_digest(binding.digest, expected_digest)
        ):
            raise ProjectRepositoryBindingStale("project repository binding is stale")
        try:
            workspace_root = Path(
                confine_workspace_path(str(self._base_dir), binding.workspace_key)
            )
            repo_root = (workspace_root / "repo").resolve(strict=True)
            canonical_base = self._base_dir.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProjectRepositoryUnavailable(
                "configured project repository is unavailable"
            ) from exc
        if (
            not repo_root.is_dir()
            or not repo_root.is_relative_to(canonical_base)
            or not (repo_root / ".git").exists()
        ):
            raise ProjectRepositoryUnavailable(
                "configured project repository is unavailable"
            )
        return repo_root

    def to_json(self) -> str:
        """Serialize the host-independent portion of this registry."""
        raw = json.dumps(
            {
                "bindings": [
                    json.loads(binding.to_json())
                    for binding in sorted(
                        self._bindings.values(), key=lambda item: item.project_id
                    )
                ],
                "schema_version": _REGISTRY_SCHEMA_VERSION,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(raw.encode("utf-8")) > _MAX_REGISTRY_BYTES:
            raise ValueError("project repository registry exceeds its bounded representation")
        return raw

    @classmethod
    def from_json(
        cls,
        raw: object,
        *,
        base_dir: str | Path | None = None,
    ) -> ProjectRepositoryRegistry:
        """Load an exact registry snapshot for this host's workspace base."""
        if not isinstance(raw, str) or not raw:
            return cls(base_dir=base_dir)
        if len(raw.encode("utf-8")) > _MAX_REGISTRY_BYTES:
            raise ValueError("project repository registry exceeds its bounded representation")
        try:
            value = json.loads(raw, object_pairs_hook=_duplicate_safe_object)
        except json.JSONDecodeError as exc:
            raise ValueError("project repository registry is malformed JSON") from exc
        if not isinstance(value, dict) or set(value) != {"bindings", "schema_version"}:
            raise ValueError("project repository registry fields are malformed")
        mapping = cast(dict[str, object], value)
        if mapping["schema_version"] != _REGISTRY_SCHEMA_VERSION:
            raise ValueError("project repository registry schema version is unsupported")
        entries = mapping["bindings"]
        if not isinstance(entries, list) or len(entries) > _MAX_REGISTRY_ENTRIES:
            raise ValueError("project repository registry entries are malformed")
        bindings = tuple(
            ProjectRepositoryBinding.from_json(
                json.dumps(entry, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            )
            for entry in entries
        )
        registry = cls(bindings, base_dir=base_dir)
        if registry.to_json() != raw:
            raise ValueError("project repository registry JSON is not canonical")
        return registry

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        base_dir: str | Path | None = None,
    ) -> ProjectRepositoryRegistry:
        """Load the immutable worker snapshot from operator-owned environment."""
        source = os.environ if environ is None else environ
        selected_base = (
            base_dir
            if base_dir is not None
            else source.get("GLUDD_PROJECT_WORKSPACE_BASE") or None
        )
        return cls.from_json(
            source.get("GLUDD_PROJECT_REPOSITORY_BINDINGS", ""),
            base_dir=selected_base,
        )


__all__ = [
    "ProjectRepositoryBinding",
    "ProjectRepositoryBindingStale",
    "ProjectRepositoryRegistry",
    "ProjectRepositoryUnavailable",
    "repository_fingerprint",
]
