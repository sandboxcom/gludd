"""Project / user / bundled ansible collections path resolver.

Implements the 3-tier collections precedence model documented in
``docs/design/PROJECT_COLLECTIONS.md``:

    1. ``<project_root>/.gludd/collections/``            (project-specific)
    2. ``${XDG_CONFIG_HOME:-~/.config}/gludd/collections/`` (user-wide)
    3. ``<install_root>/collections/``                    (gludd-bundled)

A higher tier SHADOWS the same FQCN in lower tiers — this is standard
ansible-collections behavior given an ``ANSIBLE_COLLECTIONS_PATH`` env var
ordered project-first. The functions in this module produce that ordered
list (and the corresponding ``ansible.cfg`` line / env dict) so the daemon,
the CLI diagnostic, and tests share one source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_BUNDLED_COLLECTIONS_ROOT_DEFAULT = (
    Path(__file__).resolve().parent.parent.parent.parent.parent / "collections"
)


def _bundled_collections_root() -> Path:
    """Resolve the gludd-bundled collections root.

    Indirected so tests can monkeypatch this module attribute and point the
    bundled tier at a tmp dir without touching the real install.
    """
    return _BUNDLED_COLLECTIONS_ROOT_DEFAULT


@dataclass(frozen=True)
class CollectionsPathEntry:
    """One tier in the 3-tier collections search path.

    Attributes:
        source:   human-readable tier label (``project`` / ``user`` / ``bundled``).
        path:     absolute filesystem path to the collections root for this tier.
        precedence: 0 for highest precedence, ascending for lower tiers.
    """

    source: str
    path: Path
    precedence: int


def _user_collections_root() -> Path:
    """Resolve the user-wide collections root from XDG_CONFIG_HOME (or ~/.config)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "gludd" / "collections"
    return Path.home() / ".config" / "gludd" / "collections"


def _project_collections_root(project_root: Path | None) -> Path | None:
    """Resolve the project-tier collections root, or None if no project given."""
    if project_root is None:
        return None
    return Path(project_root) / ".gludd" / "collections"


def resolve_collections_paths(
    project_root: Path | str | None = None,
) -> list[CollectionsPathEntry]:
    """Return the ordered collections search path (highest precedence first).

    Missing tiers are skipped silently — only tiers whose directory exists on
    disk are returned, except the bundled tier which is ALWAYS present (it is
    the install-time fallback and the test suite patches
    ``_bundled_collections_root`` to point it at a tmp dir).
    """
    entries: list[CollectionsPathEntry] = []
    prec = 0

    proj = _project_collections_root(
        Path(project_root) if project_root is not None else None
    )
    if proj is not None and proj.is_dir():
        entries.append(CollectionsPathEntry("project", proj, prec))
        prec += 1

    user = _user_collections_root()
    if user.is_dir():
        entries.append(CollectionsPathEntry("user", user, prec))
        prec += 1

    bundled = _bundled_collections_root()
    # Bundled tier is always present (install-time fallback). If the dir is
    # somehow missing we still surface it so operators can see the resolved
    # target — but the canonical case is "exists".
    entries.append(CollectionsPathEntry("bundled", bundled, prec))

    return entries


def to_ansible_env(entries: list[CollectionsPathEntry]) -> dict[str, str]:
    """Build the ``ANSIBLE_COLLECTIONS_PATH`` / ``ANSIBLE_ROLES_PATH`` env dict.

    The gludd tiers come FIRST (precedence order), then any pre-existing value
    of those env vars is appended so third-party collections remain reachable
    — just lower-precedence than gludd's three tiers.
    """
    tier_paths = [str(e.path) for e in entries]

    existing_cp = os.environ.get("ANSIBLE_COLLECTIONS_PATH", "")
    cp_parts = list(tier_paths)
    if existing_cp:
        for p in existing_cp.split(os.pathsep):
            if p and p not in cp_parts:
                cp_parts.append(p)

    existing_rp = os.environ.get("ANSIBLE_ROLES_PATH", "")
    rp_parts = list(tier_paths)
    if existing_rp:
        for p in existing_rp.split(os.pathsep):
            if p and p not in rp_parts:
                rp_parts.append(p)

    env: dict[str, str] = {
        "ANSIBLE_COLLECTIONS_PATH": os.pathsep.join(cp_parts),
        "ANSIBLE_ROLES_PATH": os.pathsep.join(rp_parts),
    }
    return env


def to_ansible_cfg(entries: list[CollectionsPathEntry]) -> str:
    """Render an ``ansible.cfg``-style ``collections_path = ...`` line."""
    tier_paths = [str(e.path) for e in entries]
    return f"collections_path = {os.pathsep.join(tier_paths)}"


def _split_fqcn(fqcn: str) -> tuple[str, str, str] | None:
    """Split ``namespace.collection.resource`` into a 3-tuple, or None."""
    parts = fqcn.split(".")
    if len(parts) < 3:
        return None
    return parts[0], parts[1], ".".join(parts[2:])


def find_resource(
    fqcn: str, entries: list[CollectionsPathEntry]
) -> Path | None:
    """Locate a resource by FQCN, walking tiers in precedence order.

    Looks for both modules (``plugins/modules/<resource>.py``) and roles
    (``roles/<resource>/``) under each tier's
    ``ansible_collections/<ns>/<coll>/`` directory. Returns the first hit
    (highest-precedence tier wins), or None if no tier has the resource.
    """
    split = _split_fqcn(fqcn)
    if split is None:
        return None
    namespace, collection, resource = split

    for entry in entries:
        col_root = entry.path / "ansible_collections" / namespace / collection
        if not col_root.is_dir():
            continue
        # Role lookup: roles/<resource>/  (directory)
        role_dir = col_root / "roles" / resource
        if role_dir.is_dir():
            return role_dir
        # Module lookup: plugins/modules/<resource>.py
        module_file = col_root / "plugins" / "modules" / f"{resource}.py"
        if module_file.is_file():
            return module_file
    return None


__all__ = [
    "CollectionsPathEntry",
    "find_resource",
    "resolve_collections_paths",
    "to_ansible_cfg",
    "to_ansible_env",
]
