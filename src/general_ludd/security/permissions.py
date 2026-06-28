"""Permission specs and capability resources.

Defines the resource/action model the daemon and worker use to scope an agent's
access to subsystems (OpenBao secrets today; future: git, compute, etc.).

The ``secret:openbao`` resource is the integration point with
:class:`general_ludd.secrets.manager.SecretsManager`: a capability with that
resource name carries an ``openbao_paths`` glob-list in its constraints, and the
SecretsManager consults the active spec before every hvac-delegating call (see
``SecretsManager._enforce_permission``).

Design notes:
- Specs are immutable dataclasses; the active spec is selected by ``agent_type``
  (``build`` / ``primary`` / ``subagent``) and can be narrowed further by an STS
  token (security/sts.py).
- ``default_spec("subagent")`` deliberately has NO ``secret:openbao``
  capability — subagents must be granted secret access explicitly via STS.
- Path matching uses :func:`fnmatch.fnmatchcase` so ``*`` matches across path
  separators (consistent with shell glob semantics callers expect for path trees
  like ``secret/data/gludd/build/*``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_RESOURCE_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "secret:openbao": ("openbao_paths",),
    "file:": ("path_prefix",),
    "net:": ("allowed_hosts", "allowed_ports"),
}


class PermissionDeniedError(RuntimeError):
    """Raised when a subject spec requests capabilities its issuer lacks."""


@dataclass(frozen=True)
class Capability:
    """A grant of ``actions`` on ``resource``, scoped by ``constraints``.

    Attributes:
        resource: Dotted resource name (e.g. ``"secret:openbao"``,
            ``"git:commit"``). The colon-separated final segment (``openbao``)
            identifies the backend; the prefix (``secret``) identifies the
            domain.
        actions: Permitted verbs (``read`` / ``write`` / ``list`` / ``delete``
            for ``secret:openbao``). Order is not significant.
        constraints: Backend-specific scope. For ``secret:openbao`` the key
            ``openbao_paths`` holds a list of glob patterns matched against the
            full hvac secret path.
    """

    resource: str
    actions: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionSpec:
    """The set of capabilities granted to an agent type or STS token."""

    agent_type: str
    capabilities: list[Capability] = field(default_factory=list)
    version: int = 1
    parent_agent_id: str | None = None
    denied: list[Capability] = field(default_factory=list)
    max_sts_ttl_seconds: int = 3600
    max_subagent_permissions: str = "same_or_fewer"

    def capability_for(self, resource: str) -> Capability | None:
        """Return the first capability matching ``resource`` or ``None``."""
        for cap in self.capabilities:
            if cap.resource == resource:
                return cap
        return None


# ---------------------------------------------------------------------------
# Default specs
#
# These are the broadest grants each agent type starts with. STS tokens narrow
# them further (never widen). ``default_spec`` is the single source of truth so
# the daemon, worker, and tests cannot drift on what "the build spec" means.
# ---------------------------------------------------------------------------


def _build_default_spec() -> PermissionSpec:
    """The ``build`` agent: read-only on its own build-secret subtree."""
    return PermissionSpec(
        agent_type="build",
        capabilities=[
            Capability(
                resource="secret:openbao",
                actions=["read"],
                constraints={
                    "openbao_paths": ["secret/data/gludd/build/*"],
                },
            ),
        ],
    )


def _primary_default_spec() -> PermissionSpec:
    """The ``primary`` (orchestrator) agent: read-only across the gludd subtree."""
    return PermissionSpec(
        agent_type="primary",
        capabilities=[
            Capability(
                resource="secret:openbao",
                actions=["read"],
                constraints={
                    "openbao_paths": ["secret/data/gludd/*"],
                },
            ),
        ],
    )


def _subagent_default_spec() -> PermissionSpec:
    """A ``subagent``: NO secret capability by default.

    Secret access must be granted explicitly via an STS token that carries a
    narrower spec. This makes the default-Subagent case fail-closed.
    """
    return PermissionSpec(agent_type="subagent", capabilities=[])


_DEFAULTS: dict[str, PermissionSpec] = {
    "build": _build_default_spec(),
    "primary": _primary_default_spec(),
    "subagent": _subagent_default_spec(),
}


def default_spec(agent_type: str) -> PermissionSpec:
    """Return the canonical default :class:`PermissionSpec` for ``agent_type``.

    Unknown agent types get the most restrictive default (``subagent``) so a
    typo cannot silently widen access.
    """
    return _DEFAULTS.get(agent_type, _subagent_default_spec())


class PermissionSpecParser:
    """Parse and validate :class:`PermissionSpec` from YAML.

    YAML shape::

        version: 1
        agent_type: build
        parent_agent_id: null
        max_sts_ttl_seconds: 3600
        max_subagent_permissions: "same_or_fewer"
        capabilities:
          - resource: file:repo
            actions: ["read", "write"]
            constraints:
              path_prefix: "/repo/"
        denied: []
    """

    @staticmethod
    def parse(yaml_str: str) -> PermissionSpec:
        data = yaml.safe_load(yaml_str) or {}
        capabilities = [
            Capability(
                resource=str(item["resource"]),
                actions=list(item.get("actions") or []),
                constraints=dict(item.get("constraints") or {}),
            )
            for item in (data.get("capabilities") or [])
        ]
        denied = [
            Capability(
                resource=str(item["resource"]),
                actions=list(item.get("actions") or []),
                constraints=dict(item.get("constraints") or {}),
            )
            for item in (data.get("denied") or [])
        ]
        return PermissionSpec(
            version=int(data.get("version", 1)),
            agent_type=str(data["agent_type"]),
            parent_agent_id=data.get("parent_agent_id"),
            capabilities=capabilities,
            denied=denied,
            max_sts_ttl_seconds=int(data.get("max_sts_ttl_seconds", 3600)),
            max_subagent_permissions=str(
                data.get("max_subagent_permissions", "same_or_fewer")
            ),
        )

    @staticmethod
    def parse_file(path: str | Path) -> PermissionSpec:
        return PermissionSpecParser.parse(Path(path).read_text())

    @staticmethod
    def validate(spec: PermissionSpec) -> list[str]:
        errors: list[str] = []
        for cap in spec.capabilities:
            family = PermissionSpecParser._family(cap.resource)
            if family is None:
                errors.append(
                    f"capability resource '{cap.resource}' has an unknown "
                    f"resource type; known families: "
                    f"{sorted(_RESOURCE_CONSTRAINTS.keys())}"
                )
                continue
            if not cap.actions:
                errors.append(
                    f"capability '{cap.resource}' must declare at least one action"
                )
            required = _RESOURCE_CONSTRAINTS[family]
            for key in required:
                if family == "net:":
                    if not any(
                        k in cap.constraints
                        for k in ("allowed_hosts", "allowed_ports")
                    ):
                        errors.append(
                            f"net capability '{cap.resource}' must include at "
                            f"least one of allowed_hosts/allowed_ports"
                        )
                        break
                elif key not in cap.constraints:
                    errors.append(
                        f"capability '{cap.resource}' is missing required "
                        f"constraint '{key}'"
                    )
        for d in spec.denied:
            for cap in spec.capabilities:
                if cap.resource != d.resource:
                    continue
                if set(d.actions) & set(cap.actions):
                    errors.append(
                        f"capability '{cap.resource}' appears in both "
                        f"capabilities and denied (overlapping actions: "
                        f"{sorted(set(d.actions) & set(cap.actions))})"
                    )
        return errors

    @staticmethod
    def is_subset(requested: PermissionSpec, issuer: PermissionSpec) -> bool:
        for r in requested.capabilities:
            issuer_cap = next(
                (c for c in issuer.capabilities if c.resource == r.resource),
                None,
            )
            if issuer_cap is None:
                return False
            if not set(r.actions).issubset(set(issuer_cap.actions)):
                return False
            family = PermissionSpecParser._family(r.resource)
            if family is None:
                return False
            if not PermissionSpecParser._constraints_narrower(
                r.constraints, issuer_cap.constraints, family
            ):
                return False
        return True

    @staticmethod
    def _family(resource: str) -> str | None:
        for key in sorted(_RESOURCE_CONSTRAINTS.keys(), key=len, reverse=True):
            if resource.startswith(key):
                return key
        return None

    @staticmethod
    def _constraints_narrower(
        narrow: dict[str, Any],
        wide: dict[str, Any],
        resource: str,
    ) -> bool:
        if resource == "file:":
            np = narrow.get("path_prefix")
            wp = wide.get("path_prefix")
            if not isinstance(wp, str) or not wp:
                return False
            return isinstance(np, str) and np.startswith(wp)
        if resource == "net:":
            for key in ("allowed_hosts", "allowed_ports"):
                ns = set(narrow.get(key, []) or [])
                ws = set(wide.get(key, []) or [])
                if ns and not ns.issubset(ws):
                    return False
            return True
        if resource == "secret:openbao":
            ns = set(narrow.get("openbao_paths", []) or [])
            ws = set(wide.get("openbao_paths", []) or [])
            return ns.issubset(ws)
        return False


__all__ = [
    "Capability",
    "PermissionDeniedError",
    "PermissionSpec",
    "PermissionSpecParser",
    "default_spec",
]
