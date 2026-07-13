"""AG.4 — Tool permission scoping: Cedar-style RBAC, per-tool capability
lattice, fine-grained deny.

This module implements the tool-level permission model for the agent
framework: which tools an agent may use, with which actions, scoped to
which projects.  It is a separate dimension from the resource-level
:class:`~general_ludd.security.permissions.PermissionSpec` (which gates
OpenBao / file / network resource access).

Design
------

* ``ToolPermission`` — a single tool rule: tool name, allowed actions,
  denied actions, optional project-scope constraint.
* ``ToolPermissionSpec`` — a collection of ``ToolPermission`` rules
  associated with a role.
* ``CapabilityLattice`` — a hierarchical role-capability graph where each
  role inherits its parents' native actions.  The built-in default chain is
  reader → writer → coder → admin.  Custom chains are supported.
* ``PermissionEvaluator`` — the single enforcement point:
  1. Consult the spec deny rules: any match → denied (deny wins).
  2. Consult the spec allow rules: any match → allowed.
  3. Consult the lattice: does the role hold the required action?
     Skipped when a wildcard (``"*"``) action rule matched above.
  4. Default-deny.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# ToolAction — the vocabulary of tool verbs
# ---------------------------------------------------------------------------


class ToolAction(enum.StrEnum):
    """Verbs an agent may perform with a tool."""

    READ = "read"
    WRITE = "write"
    CREATE = "create"
    OVERWRITE = "overwrite"
    EXECUTE = "execute"
    DELETE = "delete"


# ---------------------------------------------------------------------------
# ToolPermission — a single tool rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolPermission:
    """A single per-tool permission rule.

    Attributes:
        tool: Tool name (e.g. ``"read_file"``, ``"bash"``, ``"*"``).
        allowed_actions: Actions this tool may perform.
        denied_actions: Actions explicitly denied (takes precedence).
        scope: Optional project-scope constraint (e.g. ``"project:gludd"``).
            ``None`` means global — matches all scopes.
    """

    tool: str
    allowed_actions: tuple[str, ...] = ()
    denied_actions: tuple[str, ...] = ()
    scope: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_actions", tuple(self.allowed_actions))
        object.__setattr__(self, "denied_actions", tuple(self.denied_actions))


# ---------------------------------------------------------------------------
# ToolPermissionSpec — a role's tool permission set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolPermissionSpec:
    """A collection of :class:`ToolPermission` rules for a single role.

    Attributes:
        role: Role name (e.g. ``"coder"``, ``"viewer"``).
        permissions: The ordered tuple of tool permission rules.
    """

    role: str
    permissions: tuple[ToolPermission, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "permissions", tuple(self.permissions))

    def permissions_for(self, tool: str) -> list[ToolPermission]:
        """Return all :class:`ToolPermission` entries matching ``tool``.

        Matches exact tool names and the ``"*"`` wildcard.
        """
        return [p for p in self.permissions if p.tool == tool or p.tool == "*"]


# ---------------------------------------------------------------------------
# CapabilityLattice — hierarchical role capabilities
# ---------------------------------------------------------------------------

_BUILTIN_NATIVE_GRANTS: dict[str, frozenset[str]] = {
    "reader": frozenset({ToolAction.READ}),
    "viewer": frozenset({ToolAction.READ}),
    "writer": frozenset({ToolAction.WRITE, ToolAction.CREATE, ToolAction.OVERWRITE}),
    "coder": frozenset({ToolAction.EXECUTE}),
    "admin": frozenset({ToolAction.DELETE}),
}

_BUILTIN_CHAIN: dict[str, frozenset[str]] = {
    "admin": frozenset({"coder"}),
    "coder": frozenset({"writer"}),
    "writer": frozenset({"reader"}),
    "reader": frozenset(),
    "viewer": frozenset(),
}


def _infer_action(role: str) -> str:
    """Infer a native action name from a role name.

    Strips common suffixes: ``viewer`` → ``view``, ``editor`` → ``edit``,
    ``publisher`` → ``publish``.
    """
    for suffix in ("er", "or", "ar"):
        if role.endswith(suffix) and len(role) > len(suffix):
            return role[: -len(suffix)]
    return role


@dataclass(frozen=True)
class CapabilityLattice:
    """Hierarchical role-capability graph.

    Each role has a set of *native* actions and inherits from its parents
    transitively.  The built-in default chain is
    ``reader → writer → coder → admin``
    plus the standalone ``viewer`` role (read-only).

    When a custom ``chain`` is supplied, native actions are inferred from
    role names (``viewer`` → ``"view"``, ``editor`` → ``"edit"``, etc.).
    """

    chain: dict[str, frozenset[str]] = field(default_factory=dict)

    _BUILTIN_DEFAULT: bool = field(default=True, repr=False)

    def __post_init__(self) -> None:
        if not self.chain:
            object.__setattr__(self, "chain", dict(_BUILTIN_CHAIN))
            object.__setattr__(self, "_BUILTIN_DEFAULT", True)
        else:
            object.__setattr__(self, "_BUILTIN_DEFAULT", False)

    def native_actions(self, role: str) -> frozenset[str]:
        """Return the native (non-inherited) actions for ``role``."""
        if self._BUILTIN_DEFAULT and role in _BUILTIN_NATIVE_GRANTS:
            return _BUILTIN_NATIVE_GRANTS[role]
        return frozenset({_infer_action(role)})

    def _all_parents(self, role: str) -> frozenset[str]:
        """Transitive closure of parent roles."""
        seen: set[str] = set()
        stack = [role]
        while stack:
            r = stack.pop()
            for parent in self.chain.get(r, frozenset()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return frozenset(seen)

    def all_actions(self, role: str) -> frozenset[str]:
        """Return all actions (native + inherited) granted to ``role``."""
        actions: set[str] = set(self.native_actions(role))
        for parent in self._all_parents(role):
            actions |= set(self.native_actions(parent))
        return frozenset(actions)

    def is_granted(self, role: str, action: str) -> bool:
        """True iff ``role`` holds ``action`` (native or inherited)."""
        return action in self.all_actions(role)


# ---------------------------------------------------------------------------
# PermissionEvaluator — tool-usage gate
# ---------------------------------------------------------------------------


def _matches_scope(
    rule_scope: str | None,
    request_scope: str | None,
) -> bool:
    """Check whether ``rule_scope`` covers ``request_scope``.

    * ``rule_scope`` is ``None`` → global, matches everything.
    * ``rule_scope`` is ``"project:*"`` → matches any ``project:*``.
    * Exact match.
    """
    if rule_scope is None:
        return True
    if request_scope is None:
        return False
    if rule_scope.endswith(":*"):
        prefix = rule_scope[:-1]  # "project:" from "project:*"
        return request_scope.startswith(prefix)
    return rule_scope == request_scope


def _action_in(rule_actions: tuple[str, ...], request_action: str) -> bool:
    """Check if ``request_action`` is covered by ``rule_actions``.

    ``"*"`` in ``rule_actions`` matches any action.
    """
    if "*" in rule_actions:
        return True
    return request_action in rule_actions


def _spec_allows_wildcard(spec: ToolPermissionSpec, tool: str, scope: str | None) -> bool:
    """True iff ``spec`` has a wildcard (``"*"``) allow for ``tool``/``scope``."""
    for perm in spec.permissions_for(tool):
        if _action_in(perm.allowed_actions, "*") and _matches_scope(perm.scope, scope):
            return True
    return False


@dataclass
class PermissionEvaluator:
    """Evaluates whether an agent may use a tool with given arguments.

    Enforcement order:

    1. Deny rules — any matching deny overrides everything.
    2. Allow rules — explicit and wildcard rules.
    3. CapabilityLattice — role must hold the action.
       Skipped when a wildcard (``"*"``) allow rule matched in step 2.
    4. Default-deny.
    """

    lattice: CapabilityLattice

    def may_use(
        self,
        spec: ToolPermissionSpec,
        tool: str,
        action: str,
        *,
        scope: str | None = None,
    ) -> bool:
        """Check whether ``spec`` permits ``action`` on ``tool``.

        Args:
            spec: The role's tool permission spec.
            tool: The tool name (e.g. ``"read_file"``).
            action: The requested action verb.
            scope: Optional project-scope constraint.

        Returns:
            ``True`` if the action is permitted.
        """
        # Collect matching permissions (exact + wildcard tool)
        perms = spec.permissions_for(tool)

        # Layer 1: deny rules (deny takes precedence)
        for perm in perms:
            if (
                perm.denied_actions
                and _action_in(perm.denied_actions, action)
                and _matches_scope(perm.scope, scope)
            ):
                return False

        # Track whether a wildcard allow was found
        wildcard_allowed = False

        # Layer 2: allow rules
        for perm in perms:
            if (
                perm.allowed_actions
                and _action_in(perm.allowed_actions, action)
                and _matches_scope(perm.scope, scope)
            ):
                if "*" in perm.allowed_actions:
                    wildcard_allowed = True
                    break
                break
        else:
            # No allow rule matched at all
            return False

        # Layer 3: role capability lattice
        # Skip for wildcard actions (explicit admin override)
        return wildcard_allowed or self.lattice.is_granted(spec.role, action)

    # -- convenience methods --

    def may_read(self, spec: ToolPermissionSpec, tool: str, *, scope: str | None = None) -> bool:
        return self.may_use(spec, tool, ToolAction.READ, scope=scope)

    def may_write(self, spec: ToolPermissionSpec, tool: str, *, scope: str | None = None) -> bool:
        return self.may_use(spec, tool, ToolAction.WRITE, scope=scope)

    def may_execute(self, spec: ToolPermissionSpec, tool: str, *, scope: str | None = None) -> bool:
        return self.may_use(spec, tool, ToolAction.EXECUTE, scope=scope)

    def evaluate_all(
        self,
        spec: ToolPermissionSpec,
        queries: list[tuple[str, str]],
        *,
        scope: str | None = None,
    ) -> dict[tuple[str, str], bool]:
        """Bulk-evaluate a list of (tool, action) queries.

        Returns:
            Dict mapping each ``(tool, action)`` to its permission verdict.
        """
        return {
            (tool, action): self.may_use(spec, tool, action, scope=scope)
            for tool, action in queries
        }


__all__ = [
    "CapabilityLattice",
    "PermissionEvaluator",
    "ToolAction",
    "ToolPermission",
    "ToolPermissionSpec",
]
