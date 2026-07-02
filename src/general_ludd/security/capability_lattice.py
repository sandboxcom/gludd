"""Per-role capability lattice + self-modification guards (issue #58).

This is the *daemon-side* enforcement layer for gludd's self-modification
guards.  It is deliberately self-contained (stdlib only, no Ansible /
``module_utils`` import) so it is importable and unit-testable from anywhere in
``general_ludd`` and can be consulted at the live enforcement sites:

* :class:`general_ludd.reload.hot_reloader.HotReloader` — before swapping a
  leaf module's source over the live file, the reloader asks this module
  whether (a) the live path is a *protected* guardrail/policy/permission file
  (always refused) and (b) if it lands inside a ``collections/`` tree, whether
  the acting role holds ``collections_self_modify``.
* :class:`general_ludd.dispatch.dynamic_dispatcher.DynamicDispatcher` — before
  invoking a handler it consults :func:`role_may_dispatch` so a role can never
  drive a tool-call ``kind`` whose required capability it lacks.

The companion Ansible ``module_utils/capability_policy.py`` answers the same
"may this role do this?" question *inside managed-node module execution*; this
module answers it inside the long-running daemon process.  Both are
**default-DENY**: an unknown role grants nothing, and every guard fails closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Capability vocabulary
# ---------------------------------------------------------------------------

#: Path component that marks the agent's own Ansible source tree.  A write whose
#: resolved path contains this component is a *self-modification*.
COLLECTIONS_COMPONENT = "collections"

#: Filename stems (case-insensitive, ``.py`` stripped) that are NEVER hot-
#: swappable: the guardrail / policy / permission surface itself.  Hot-reloading
#: any of these would let a self-improvement turn disable the very guards that
#: constrain it, so the reloader refuses them outright — even for a role that
#: otherwise holds ``collections_self_modify``.
PROTECTED_FILE_STEMS: frozenset[str] = frozenset(
    {
        "guardrails",
        "capability_policy",
        "capability_lattice",
        "fs_write_policy",
        "action_policy",
        "permissions",
        "permission",
        "policy",
        "enforce_make",
    }
)

#: Substrings that, if present in a resolved path (normalised to ``/``), mark it
#: protected regardless of the leaf filename — the harness/guardrail config
#: surface.  Conservative + case-insensitive.
PROTECTED_PATH_SUBSTRINGS: tuple[str, ...] = (
    "/.opencode/",
    "/.claude/",
    "/module_utils/capability_policy",
    "/module_utils/fs_write_policy",
    "/security/capability_lattice",
)

#: Bare path SEGMENTS that mark a protected harness control-surface directory
#: regardless of a leading slash.  The ``/.claude/`` / ``/.opencode/`` substrings
#: above only match when a slash precedes the directory, so a workspace-RELATIVE
#: target like ``.claude/hooks/evil.py`` (no leading slash, ``.claude`` at
#: position 0) would EVADE them.  Matching ``.claude`` / ``.opencode`` as a whole
#: path segment (path split on ``/``) at ANY position closes that drift against
#: the sibling ``self_update/applier.py`` deny-list — which already catches the
#: relative form.  This only ADDS the relative case; it never loosens the
#: existing absolute-path coverage.
PROTECTED_PATH_SEGMENTS: frozenset[str] = frozenset({".opencode", ".claude"})


class CapabilityError(Exception):
    """Raised (fail-closed) when a role lacks a requested capability."""


class ProtectedPathError(Exception):
    """Raised (fail-closed) when a write/swap targets a protected guard file."""


# ---------------------------------------------------------------------------
# Per-role grant set (daemon side)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleCapabilities:
    """The capabilities a single role is granted.  Default-DENY everywhere."""

    role: str = "<unknown>"
    #: May rotate/write files under a ``collections/`` tree (self-modify).
    collections_self_modify: bool = False
    #: Tool-call ``kind`` values the role may dispatch (e.g. ``"role"``,
    #: ``"collection"``, ``"mcp"``, ``"skill"``).  Empty => no dispatch.
    dispatch_kinds: frozenset[str] = field(default_factory=frozenset)


#: Built-in role grants — minimal, each role gets ONLY what its job needs.
#: Mirrors the ``collections_self_modify`` posture of the Ansible-side
#: ``module_utils/capability_policy.py`` built-in table.
_BUILTIN: dict[str, RoleCapabilities] = {
    # Self-improvement / self-research: the ONLY roles allowed to write under
    # collections/ and to drive the collection dispatch kind.
    "self_improve_agent": RoleCapabilities(
        role="self_improve_agent",
        collections_self_modify=True,
        dispatch_kinds=frozenset({"role", "collection", "mcp", "skill"}),
    ),
    "self_research_agent": RoleCapabilities(
        role="self_research_agent",
        collections_self_modify=True,
        dispatch_kinds=frozenset({"role", "collection", "mcp", "skill"}),
    ),
    # Ordinary coder: may dispatch roles/mcp/skills but NOT collections (no
    # self-modify), so it can never drive a collections write through dispatch.
    "coder": RoleCapabilities(
        role="coder",
        collections_self_modify=False,
        dispatch_kinds=frozenset({"role", "mcp", "skill"}),
    ),
    "operator": RoleCapabilities(
        role="operator",
        collections_self_modify=False,
        dispatch_kinds=frozenset({"role", "collection", "mcp", "skill"}),
    ),
    "report_status": RoleCapabilities(
        role="report_status",
        collections_self_modify=False,
        dispatch_kinds=frozenset({"skill"}),
    ),
    "security_auditor": RoleCapabilities(
        role="security_auditor",
        collections_self_modify=False,
        dispatch_kinds=frozenset({"role", "skill"}),
    ),
    # Event-loop turn handler: may dispatch role/mcp/skill kinds when a model
    # turn returns tool_calls.  Deliberately excludes "collection" — the loop
    # never drives self-modification writes, so collections_self_modify stays
    # False and the collection kind is absent from dispatch_kinds.
    "event_loop": RoleCapabilities(
        role="event_loop",
        collections_self_modify=False,
        dispatch_kinds=frozenset({"role", "mcp", "skill"}),
    ),
}

#: The deny-everything baseline an unknown role receives.
_BASELINE = RoleCapabilities()


def capabilities_for(role: str | None) -> RoleCapabilities:
    """Return the :class:`RoleCapabilities` for ``role`` (default-DENY).

    An unknown / missing / empty role gets the empty baseline (deny all).
    """
    if not role or not isinstance(role, str):
        return _BASELINE
    return _BUILTIN.get(role.strip(), RoleCapabilities(role=role.strip()))


# ---------------------------------------------------------------------------
# Dispatch lattice: which capability a tool-call kind requires
# ---------------------------------------------------------------------------

#: A tool-call ``kind`` is dispatchable by a role iff the kind is in the role's
#: ``dispatch_kinds`` grant.  The ``collection`` kind additionally requires the
#: ``collections_self_modify`` capability (a collection handler can write the
#: agent's own source), so it is gated on BOTH.
_KIND_REQUIRES_SELF_MODIFY: frozenset[str] = frozenset({"collection"})


def role_may_dispatch(role: str | None, kind: str) -> bool:
    """Return True iff ``role`` may dispatch a tool-call of ``kind``.

    Default-DENY: an unknown role or an ungranted kind returns False.
    """
    caps = capabilities_for(role)
    if kind not in caps.dispatch_kinds:
        return False
    # The collection kind additionally requires collections_self_modify.
    return not (
        kind in _KIND_REQUIRES_SELF_MODIFY and not caps.collections_self_modify
    )


def check_dispatch(role: str | None, kind: str) -> None:
    """Fail-closed variant of :func:`role_may_dispatch`."""
    if not role_may_dispatch(role, kind):
        raise CapabilityError(
            f"role {role!r} lacks the capability to dispatch tool-call kind "
            f"{kind!r} (default-deny)"
        )


# ---------------------------------------------------------------------------
# Self-modification guards (reload site)
# ---------------------------------------------------------------------------


def _normalise(path: str) -> str:
    return (path or "").replace("\\", "/")


def is_collections_path(path: str) -> bool:
    """True if ``path`` lands inside a ``collections/`` tree."""
    parts = _normalise(path).split("/")
    return COLLECTIONS_COMPONENT in parts


def is_protected_path(path: str) -> bool:
    """True if ``path`` is a guardrail/policy/permission file (never swappable).

    Matches on the leaf stem (``foo.py`` -> ``foo``) against
    :data:`PROTECTED_FILE_STEMS`, on any :data:`PROTECTED_PATH_SEGMENTS`
    (``.claude`` / ``.opencode``) appearing as a whole path segment regardless of
    a leading slash — so a workspace-RELATIVE ``.claude/hooks/x.py`` is caught,
    not just an absolute ``/ws/.claude/hooks/x.py`` — or on any of
    :data:`PROTECTED_PATH_SUBSTRINGS` appearing in the normalised path.
    Case-insensitive.
    """
    norm = _normalise(path).lower()
    # Segment match FIRST so the workspace-RELATIVE ``.claude``/``.opencode``
    # form (no leading slash) is caught as well as the absolute ``/.claude/``.
    if PROTECTED_PATH_SEGMENTS.intersection(norm.split("/")):
        return True
    for sub in PROTECTED_PATH_SUBSTRINGS:
        if sub in norm:
            return True
    leaf = norm.rsplit("/", 1)[-1]
    stem = re.sub(r"\.py[cod]?$", "", leaf)
    return stem in PROTECTED_FILE_STEMS


def check_self_modification(path: str, role: str | None) -> None:
    """Authorise modifying the file at ``path`` by ``role`` (fail-closed).

    Two ordered gates:

    1. **Protected-path deny-list** — a guardrail/policy/permission file is
       NEVER swappable (raises :class:`ProtectedPathError`), regardless of role.
    2. **Collections self-modify** — if the path lands inside ``collections/``
       the role must hold ``collections_self_modify`` (raises
       :class:`CapabilityError`).

    A path that is neither protected nor inside ``collections/`` passes.
    """
    if is_protected_path(path):
        raise ProtectedPathError(
            f"refusing to modify protected guard file {path!r} "
            "(guardrail/policy/permission deny-list)"
        )
    if is_collections_path(path):
        caps = capabilities_for(role)
        if not caps.collections_self_modify:
            raise CapabilityError(
                f"role {role!r} may not self-modify collections/ "
                f"(default-deny; only self-improvement roles may): {path!r}"
            )
