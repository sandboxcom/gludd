"""Validated, monotonic OpenBao scopes for per-agent AppRoles.

The scope model deliberately accepts only relative secret-engine paths and a
single terminal ``*`` subtree marker.  OpenBao's full ACL language is more
expressive, but accepting that language from delegated agents would make
parent/child intersection ambiguous and create privilege-escalation edges.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_MOUNT_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_POLICY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_RESERVED_MOUNTS = frozenset({"auth", "cubbyhole", "identity", "sys"})
_ALLOWED_CAPABILITIES = frozenset(
    {"create", "delete", "list", "patch", "read", "update"}
)
_MAX_MOUNT_CHARS = 128
_MAX_PATH_CHARS = 512
_MAX_SCOPE_PATHS = 64


class OpenBaoScopeDenied(ValueError):
    """The requested child scope has no safe overlap with its parent."""


def validate_openbao_mount(mount: str, *, allow_reserved: bool = False) -> str:
    """Return a validated relative OpenBao mount alias.

    Nested aliases such as ``secret/team-a`` remain supported.  Empty,
    absolute, encoded, backslash, dot-segment, and system mounts fail closed.
    """

    if not isinstance(mount, str) or not mount or len(mount) > _MAX_MOUNT_CHARS:
        raise ValueError("OpenBao mount must be a non-empty bounded string")
    if mount != mount.strip() or mount.startswith("/") or mount.endswith("/"):
        raise ValueError("OpenBao mount must be a canonical relative alias")
    if "\\" in mount or "%" in mount or "\x00" in mount:
        raise ValueError("OpenBao mount contains forbidden characters")
    segments = mount.split("/")
    if any(
        segment in {"", ".", ".."} or _MOUNT_SEGMENT_RE.fullmatch(segment) is None
        for segment in segments
    ):
        raise ValueError("OpenBao mount contains an invalid or traversal segment")
    if not allow_reserved and segments[0].lower() in _RESERVED_MOUNTS:
        raise ValueError("OpenBao system mounts cannot be delegated")
    return "/".join(segments)


def validate_openbao_path(path: str, *, allow_terminal_wildcard: bool) -> str:
    """Return a validated relative OpenBao path or terminal subtree pattern."""

    if not isinstance(path, str) or not path or len(path) > _MAX_PATH_CHARS:
        raise ValueError("OpenBao path must be a non-empty bounded string")
    if path != path.strip() or path.startswith("/") or path.endswith("/"):
        raise ValueError("OpenBao path must be canonical and relative")
    if "\\" in path or "%" in path or "\x00" in path:
        raise ValueError("OpenBao path contains forbidden characters")
    segments = path.split("/")
    for index, segment in enumerate(segments):
        if segment == "*":
            if not allow_terminal_wildcard or index != len(segments) - 1:
                raise ValueError("OpenBao wildcard is allowed only as the final segment")
            continue
        if segment in {"", ".", ".."} or _SEGMENT_RE.fullmatch(segment) is None:
            raise ValueError("OpenBao path contains an invalid or traversal segment")
    return "/".join(segments)


def validate_openbao_policy_name(name: str) -> str:
    if not isinstance(name, str) or _POLICY_NAME_RE.fullmatch(name) is None:
        raise ValueError("OpenBao policy name is not a bounded safe identifier")
    return name


@dataclass(frozen=True)
class OpenBaoScopeEvidence:
    """Redacted, typed evidence for a scope lifecycle decision.

    Raw subject IDs, mount aliases, paths, policies, RoleIDs, and SecretIDs are
    intentionally absent.  Hashes are domain-separated SHA-256 prefixes so
    operators can correlate events without disclosing the protected material.
    """

    event_type: Literal["scope_granted", "scope_denied", "scope_revoked"]
    subject_hash: str
    scope_hash: str
    path_count: int
    capabilities: tuple[str, ...]
    reason_code: str = "ok"

    def as_dict(self) -> dict[str, str | int | list[str]]:
        return {
            "event_type": self.event_type,
            "subject_hash": self.subject_hash,
            "scope_hash": self.scope_hash,
            "path_count": self.path_count,
            "capabilities": list(self.capabilities),
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class _PathPattern:
    segments: tuple[str, ...]
    subtree: bool

    @classmethod
    def parse(cls, path: str) -> _PathPattern:
        parts = tuple(path.split("/"))
        return cls(
            segments=parts[:-1] if parts[-1] == "*" else parts,
            subtree=parts[-1] == "*",
        )

    def render(self) -> str:
        base = "/".join(self.segments)
        return f"{base}/*" if self.subtree else base


def _intersect_pattern(left: _PathPattern, right: _PathPattern) -> _PathPattern | None:
    if left.segments == right.segments:
        return _PathPattern(
            segments=left.segments,
            subtree=left.subtree and right.subtree,
        )
    if (
        left.subtree
        and len(right.segments) > len(left.segments)
        and right.segments[: len(left.segments)] == left.segments
    ):
        return right
    if (
        right.subtree
        and len(left.segments) > len(right.segments)
        and left.segments[: len(right.segments)] == right.segments
    ):
        return left
    return None


@dataclass(frozen=True)
class OpenBaoPathScope:
    """A bounded set of secret-engine paths and ACL capabilities."""

    mount: str
    paths: tuple[str, ...]
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        mount = validate_openbao_mount(self.mount)
        if not self.paths or len(self.paths) > _MAX_SCOPE_PATHS:
            raise ValueError("OpenBao scope requires 1..64 paths")
        paths = tuple(
            sorted(
                {
                    validate_openbao_path(path, allow_terminal_wildcard=True)
                    for path in self.paths
                }
            )
        )
        capabilities = frozenset(str(capability) for capability in self.capabilities)
        if not capabilities:
            raise ValueError("OpenBao scope requires at least one capability")
        unknown = capabilities - _ALLOWED_CAPABILITIES
        if unknown:
            raise ValueError(
                "OpenBao scope contains unsupported capabilities: "
                + ", ".join(sorted(unknown))
            )
        object.__setattr__(self, "mount", mount)
        object.__setattr__(self, "paths", paths)
        object.__setattr__(self, "capabilities", capabilities)

    def intersect(self, requested: OpenBaoPathScope) -> OpenBaoPathScope:
        """Return the monotonic parent/request intersection or fail closed."""

        if self.mount != requested.mount:
            raise OpenBaoScopeDenied("scope denied: mount aliases do not match")
        capabilities = self.capabilities & requested.capabilities
        if not capabilities:
            raise OpenBaoScopeDenied("scope denied: no common capability")
        overlaps: set[str] = set()
        for parent_path in self.paths:
            parent_pattern = _PathPattern.parse(parent_path)
            for requested_path in requested.paths:
                overlap = _intersect_pattern(
                    parent_pattern,
                    _PathPattern.parse(requested_path),
                )
                if overlap is not None:
                    overlaps.add(overlap.render())
        if not overlaps:
            raise OpenBaoScopeDenied("scope denied: no common path")
        return OpenBaoPathScope(
            mount=self.mount,
            paths=tuple(sorted(overlaps)),
            capabilities=capabilities,
        )

    def render_policy(self, policy_name: str) -> str:
        """Render the already-narrowed scope as deterministic OpenBao HCL."""

        validate_openbao_policy_name(policy_name)
        cap_list = ", ".join(f'"{cap}"' for cap in sorted(self.capabilities))
        lines = [f'# Gludd scoped policy "{policy_name}"']
        for path in self.paths:
            lines.extend(
                (
                    f'path "{self.mount}/{path}" {{',
                    f"  capabilities = [{cap_list}]",
                    "}",
                )
            )
        return "\n".join(lines) + "\n"

    def evidence(
        self,
        *,
        event_type: Literal["scope_granted", "scope_denied", "scope_revoked"],
        subject_id: str,
        reason_code: str = "ok",
    ) -> OpenBaoScopeEvidence:
        canonical = json.dumps(
            {
                "mount": self.mount,
                "paths": self.paths,
                "capabilities": sorted(self.capabilities),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return OpenBaoScopeEvidence(
            event_type=event_type,
            subject_hash=_digest("gludd-openbao-subject", subject_id),
            scope_hash=_digest("gludd-openbao-scope", canonical),
            path_count=len(self.paths),
            capabilities=tuple(sorted(self.capabilities)),
            reason_code=reason_code,
        )


@dataclass(frozen=True)
class OpenBaoScopeRequest:
    """Parent authority and child-request pair resolved at mint time."""

    parent: OpenBaoPathScope
    requested: OpenBaoPathScope

    def grant(self) -> OpenBaoPathScope:
        return self.parent.intersect(self.requested)


def policy_name_for_agent(agent_id: str) -> str:
    """Return a bounded policy identifier that never exposes the agent ID."""

    if not isinstance(agent_id, str) or not agent_id or len(agent_id) > 512:
        raise ValueError("agent ID must be a non-empty bounded string")
    return f"gludd-agent-{_digest('gludd-openbao-policy', agent_id)[:24]}"


def _digest(domain: str, value: str) -> str:
    return hashlib.sha256(f"{domain}\x00{value}".encode()).hexdigest()[:32]


__all__ = [
    "OpenBaoPathScope",
    "OpenBaoScopeDenied",
    "OpenBaoScopeEvidence",
    "OpenBaoScopeRequest",
    "policy_name_for_agent",
    "validate_openbao_mount",
    "validate_openbao_path",
    "validate_openbao_policy_name",
]
