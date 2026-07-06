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

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_RESOURCE_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "secret:openbao": ("openbao_paths",),
    "file:": ("path_prefix",),
    "net:": ("allowed_hosts", "allowed_ports"),
    "agent:": (),
}


class PermissionDeniedError(RuntimeError):
    """Raised when a subject spec requests capabilities its issuer lacks."""


class PermissionSubject(enum.StrEnum):
    """Who a :class:`PermissionSpec` applies to.

    ``AGENT`` is the back-compat default for agent-type specs.
    ``HUMAN`` marks specs that apply to human users (human-admin /
    human-operator / human-viewer).
    ``STS_TOKEN`` marks a derived spec produced by
    :meth:`PermissionSpecParser.intersection` — it is the effective scope
    handed to a subagent.
    """

    AGENT = "agent"
    HUMAN = "human"
    STS_TOKEN = "sts_token"


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
    """The set of capabilities granted to an agent type or STS token.

    Attributes:
        agent_type: Label identifying the spec source (``build`` / ``primary``
            / ``subagent`` / ``human-admin`` / a custom type). For
            intersection results this is the literal string ``"sts_token"``.
        capabilities: Positive grants.
        version: Schema version.
        parent_agent_id: The agent_id of the issuer when this spec arrived
            via STS delegation. ``None`` for top-level specs.
        parent_human_id: The human_id whose spec participated in the
            intersection that produced this spec (audit only). ``None`` when
            no human intersection occurred.
        denied: Negative grants.
        max_sts_ttl_seconds: Hard ceiling for any STS token minted FROM this
            spec.
        max_subagent_permissions: Delegation policy (``same_or_fewer``).
        subject: Who/what the spec applies to (agent / human / sts_token).
            Defaults to :attr:`PermissionSubject.AGENT` for back-compat.
    """

    agent_type: str
    capabilities: list[Capability] = field(default_factory=list)
    version: int = 1
    parent_agent_id: str | None = None
    parent_human_id: str | None = None
    denied: list[Capability] = field(default_factory=list)
    max_sts_ttl_seconds: int = 3600
    max_subagent_permissions: str = "same_or_fewer"
    subject: PermissionSubject = PermissionSubject.AGENT

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


# ---------------------------------------------------------------------------
# Default human specs
#
# Humans carry specs too. The human spec is one of three roles shipped in
# ``config/permissions/human-*.yml``; the daemon config field
# ``default_human_role`` (default ``human-operator``) selects which applies
# when no per-user override exists. A human spec never widens a subagent's
# scope — the EFFECTIVE subagent spec is the INTERSECTION of the human spec
# and the dispatching agent's spec (see ``PermissionSpecParser.intersection``).
# ---------------------------------------------------------------------------


def _human_admin_default_spec() -> PermissionSpec:
    """``human-admin``: full permissions — the admin can do anything."""
    return PermissionSpec(
        agent_type="human-admin",
        subject=PermissionSubject.HUMAN,
        capabilities=[
            Capability(
                resource="file:",
                actions=["read", "write"],
                constraints={"path_prefix": "/"},
            ),
            Capability(
                resource="net:egress:any",
                actions=["connect"],
                constraints={"allowed_hosts": ["*"]},
            ),
            Capability(
                resource="secret:openbao",
                actions=["read", "write", "list", "delete"],
                constraints={"openbao_paths": ["secret/data/gludd/*"]},
            ),
        ],
        max_sts_ttl_seconds=3600,
    )


def _human_operator_default_spec() -> PermissionSpec:
    """``human-operator``: can run playbooks + read secrets, cannot mutate OpenBao config."""
    return PermissionSpec(
        agent_type="human-operator",
        subject=PermissionSubject.HUMAN,
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read", "write"],
                constraints={"path_prefix": "/repo/"},
            ),
            Capability(
                resource="net:egress:any",
                actions=["connect"],
                constraints={"allowed_hosts": ["*"]},
            ),
            Capability(
                resource="secret:openbao",
                actions=["read"],
                constraints={"openbao_paths": ["secret/data/gludd/*"]},
            ),
        ],
        max_sts_ttl_seconds=3600,
    )


def _human_viewer_default_spec() -> PermissionSpec:
    """``human-viewer``: read-only everywhere; connect-only on LLM APIs."""
    return PermissionSpec(
        agent_type="human-viewer",
        subject=PermissionSubject.HUMAN,
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read"],
                constraints={"path_prefix": "/repo/"},
            ),
            Capability(
                resource="net:egress:llm_api",
                actions=["connect"],
                constraints={"allowed_hosts": ["api.anthropic.com", "api.openai.com", "api.z.ai"]},
            ),
            Capability(
                resource="secret:openbao",
                actions=["read"],
                constraints={"openbao_paths": ["secret/data/gludd/read-only/*"]},
            ),
        ],
        max_sts_ttl_seconds=3600,
    )


_HUMAN_DEFAULTS: dict[str, PermissionSpec] = {
    "human-admin": _human_admin_default_spec(),
    "human-operator": _human_operator_default_spec(),
    "human-viewer": _human_viewer_default_spec(),
}


def default_human_spec(role: str) -> PermissionSpec:
    """Return the canonical default human :class:`PermissionSpec` for ``role``.

    Unknown roles fall back to ``human-viewer`` (most restrictive) so a typo
    cannot silently widen access.
    """
    return _HUMAN_DEFAULTS.get(role, _human_viewer_default_spec())


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
        subject_raw = data.get("subject", "agent")
        try:
            subject = PermissionSubject(str(subject_raw))
        except ValueError:
            subject = PermissionSubject.AGENT
        return PermissionSpec(
            version=int(data.get("version", 1)),
            agent_type=str(data.get("agent_type", "unknown")),
            parent_agent_id=data.get("parent_agent_id"),
            parent_human_id=data.get("parent_human_id"),
            capabilities=capabilities,
            denied=denied,
            max_sts_ttl_seconds=int(data.get("max_sts_ttl_seconds", 3600)),
            max_subagent_permissions=str(
                data.get("max_subagent_permissions", "same_or_fewer")
            ),
            subject=subject,
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
    def intersection(
        a: PermissionSpec,
        b: PermissionSpec,
        *,
        parent_human_id: str | None = None,
    ) -> PermissionSpec:
        """Return the conservative intersection of two :class:`PermissionSpec`s.

        Formal definition (also in ``docs/design/PERMISSION_SYSTEM.md`` §10):

            intersection(a, b).capabilities =
                [intersect(c, c') for c in a.capabilities
                                   if exists c' in b.capabilities
                                   where c.resource == c'.resource
                                   and intersect(c, c') is non-empty]

        For each overlapping capability:

        * ``actions`` = set intersection.
        * ``path_prefix`` (file:) = the NARROWER (more specific) prefix when
          one scope truly contains the other; disjoint scopes drop the
          capability (no shared file access).
        * ``allowed_hosts`` / ``allowed_ports`` (net:) = set intersection.
        * ``openbao_paths`` (secret:openbao) = set intersection.

        Whole-spec fields:

        * ``denied`` = UNION of both denied lists.
        * ``max_sts_ttl_seconds`` = MIN of the two.
        * ``subject`` = :attr:`PermissionSubject.STS_TOKEN` (a derived scope).
        * ``parent_agent_id`` / ``parent_human_id`` = recorded for audit
          (``parent_agent_id`` carries ``b``'s issuer id when present;
          ``parent_human_id`` is the explicit kwarg or ``a``'s when ``a`` is
          a HUMAN subject).

        If two capabilities share a resource but their intersected actions or
        constraints are EMPTY, the capability is dropped entirely
        (conservative: if unsure whether to grant, deny).
        """
        out_caps: list[Capability] = []
        for cap_a in a.capabilities:
            cap_b = next(
                (c for c in b.capabilities if c.resource == cap_a.resource),
                None,
            )
            if cap_b is None:
                continue
            family = PermissionSpecParser._family(cap_a.resource)
            if family is None:
                continue
            actions = sorted(set(cap_a.actions) & set(cap_b.actions))
            if not actions:
                continue
            constraints = PermissionSpecParser._intersect_constraints(
                cap_a.constraints, cap_b.constraints, family
            )
            if constraints is None:
                continue
            out_caps.append(
                Capability(resource=cap_a.resource, actions=actions, constraints=constraints)
            )

        denied_union: list[Capability] = []
        seen_denied: set[tuple[str, tuple[str, ...]]] = set()
        for d in list(a.denied) + list(b.denied):
            key = (d.resource, tuple(sorted(d.actions)))
            if key in seen_denied:
                continue
            seen_denied.add(key)
            denied_union.append(d)

        parent_agent_id = b.parent_agent_id or a.parent_agent_id
        if parent_human_id is None and a.subject == PermissionSubject.HUMAN:
            parent_human_id = a.parent_agent_id or a.agent_type

        return PermissionSpec(
            agent_type="sts_token",
            capabilities=out_caps,
            denied=denied_union,
            max_sts_ttl_seconds=min(a.max_sts_ttl_seconds, b.max_sts_ttl_seconds),
            max_subagent_permissions="same_or_fewer",
            parent_agent_id=parent_agent_id,
            parent_human_id=parent_human_id,
            subject=PermissionSubject.STS_TOKEN,
        )

    @staticmethod
    def _intersect_constraints(
        a: dict[str, Any],
        b: dict[str, Any],
        family: str,
    ) -> dict[str, Any] | None:
        """Return the narrowed constraints for ``family`` or ``None`` if empty.

        ``None`` is the signal to drop the capability entirely (no overlap).
        """
        if family == "file:":
            ap = a.get("path_prefix")
            bp = b.get("path_prefix")
            if not isinstance(ap, str) or not isinstance(bp, str):
                return None
            if not ap or not bp:
                return None
            # Containment-aware intersection: the narrower (more specific)
            # scope wins ONLY when one prefix truly contains the other. Compare
            # on path SEGMENTS (not bare string startswith) so "/a/bc" is not
            # treated as nested under "/a/b". Disjoint scopes have an EMPTY
            # intersection and drop the capability (return None) — never widen
            # to whichever prefix happens to be longer.
            a_segs = [s for s in ap.split("/") if s]
            b_segs = [s for s in bp.split("/") if s]
            if a_segs[: len(b_segs)] == b_segs:
                # ``bp`` is a path-prefix of (or equal to) ``ap`` -> ``ap`` is
                # the narrower/equal scope.
                return {"path_prefix": ap}
            if b_segs[: len(a_segs)] == a_segs:
                # ``ap`` is a path-prefix of ``bp`` -> ``bp`` is narrower.
                return {"path_prefix": bp}
            return None
        if family == "net:":
            out: dict[str, Any] = {}
            for key in ("allowed_hosts", "allowed_ports"):
                aset = set(a.get(key, []) or [])
                bset = set(b.get(key, []) or [])
                inter = sorted(aset & bset)
                if inter:
                    out[key] = inter
            if not out:
                return None
            return out
        if family == "secret:openbao":
            aset = set(a.get("openbao_paths", []) or [])
            bset = set(b.get("openbao_paths", []) or [])
            inter = sorted(aset & bset)
            if not inter:
                return None
            return {"openbao_paths": inter}
        return None

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
            wide_present = isinstance(wp, str) and bool(wp)
            narrow_present = isinstance(np, str) and bool(np)
            if not wide_present:
                # ABSENT path_prefix on the wide side == UNCONSTRAINED == the
                # whole filesystem, which contains any scope. A capability WITH
                # a constraint is therefore narrower than one WITHOUT (and two
                # unconstrained caps are equal == narrower-or-equal).
                return True
            if not narrow_present:
                # The wide side restricts a subtree but the narrow side is
                # UNCONSTRAINED == WIDER -> it is NOT narrower. Fail safe.
                return False
            # Both constrained: ``narrow`` is narrower-or-equal iff ``wide``'s
            # prefix truly CONTAINS ``narrow``'s. Compare on path SEGMENTS (not
            # bare string ``startswith``) so "/etc/passwd2" is not mistaken for
            # a child of "/etc/pass" -- those are DISJOINT and neither dominates
            # (mirrors the containment fix in ``_intersect_constraints``).
            assert isinstance(np, str) and isinstance(wp, str)
            n_segs = [s for s in np.split("/") if s]
            w_segs = [s for s in wp.split("/") if s]
            return n_segs[: len(w_segs)] == w_segs
        if resource == "net:":
            for key in ("allowed_hosts", "allowed_ports"):
                ns = set(narrow.get(key, []) or [])
                ws = set(wide.get(key, []) or [])
                if not ws:
                    # ABSENT/empty on the wide side == UNCONSTRAINED on this
                    # dimension (the enforcers emit ``(allow network-outbound)``
                    # for an empty host list) == the widest scope, so any narrow
                    # value is narrower-or-equal. Nothing to check.
                    continue
                # The wide side RESTRICTS this dimension. An ABSENT/empty narrow
                # set is UNCONSTRAINED == WIDER (allows everything), so it is NOT
                # narrower -> reject. Otherwise the narrow set must be a subset.
                if not ns or not ns.issubset(ws):
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
    "PermissionSubject",
    "default_human_spec",
    "default_spec",
]
