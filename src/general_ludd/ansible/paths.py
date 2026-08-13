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

Versioned collections
---------------------

Each tier may carry versioned variants of a collection stored in directories
following the pattern ``ansible_collections/<ns>@<version>/<coll>/``, e.g.::

    .gludd/collections/ansible_collections/general_ludd@0.1.0/agent/
    .gludd/collections/ansible_collections/general_ludd@beta.2/agent/
    .gludd/collections/ansible_collections/general_ludd@latest/agent/
    .gludd/collections/ansible_collections/general_ludd/agent/          (bare)

Precedence when resolving a version: exact match > ``@latest`` > bare
(unversioned) > highest semver among remaining candidates.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import FrozenInstanceError, dataclass, field
from pathlib import Path

_BUNDLED_COLLECTIONS_ROOT_DEFAULT = (
    Path(__file__).resolve().parent.parent.parent.parent / "collections"
)


def _bundled_collections_root() -> Path:
    """Resolve the gludd-bundled collections root.

    Indirected so tests can monkeypatch this module attribute and point the
    bundled tier at a tmp dir without touching the real install.
    """
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        return Path(frozen_root) / "collections"
    return _BUNDLED_COLLECTIONS_ROOT_DEFAULT


class CollectionsPathMutationError(FrozenInstanceError, ValueError):
    """Stable error raised when an immutable collections entry is mutated.

    ``FrozenInstanceError`` is the dataclass-facing contract, while the
    ``ValueError`` base preserves compatibility with callers that historically
    treated invalid path-entry mutation as a validation error.
    """


@dataclass(slots=True)
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

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name):
            raise CollectionsPathMutationError(f"cannot assign to field {name!r}")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise CollectionsPathMutationError(f"cannot delete field {name!r}")


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


_VERSION_DIR_RE = re.compile(r"^(.+)@(.+)$")


@dataclass
class CollectionVersionInfo:
    """Metadata for one versioned collection directory.

    Attributes:
        namespace:     collection namespace (e.g. ``general_ludd``).
        collection:    collection name (e.g. ``agent``).
        version:       version string (``0.1.0``, ``beta.2``, ``latest``).
        path:          absolute path to the collection root
                       (``.../ansible_collections/<ns>@<v>/<coll>/``).
        is_latest:     True when the version tag is ``latest``.
        is_semver:     True when the version string is a valid semver
                       (``MAJOR.MINOR.PATCH`` with optional pre-release).
    """

    namespace: str
    collection: str
    version: str
    path: Path
    is_latest: bool = field(init=False, default=False)
    is_semver: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "is_latest", self.version == "latest")
        object.__setattr__(
            self, "is_semver", bool(re.fullmatch(r"\d+\.\d+\.\d+[a-zA-Z0-9.\-]*", self.version))
        )


def scan_collection_versions(
    base: Path,
    namespace: str | None = None,
    collection: str | None = None,
) -> list[CollectionVersionInfo]:
    """Find versioned collection dirs under *base*/ansible_collections/.

    Matches directories whose name follows ``<ns>@<version>`` and contain
    at least one subdirectory (the collection name).  When *namespace*
    and/or *collection* are given, only matching entries are returned.

    Returns a list ordered by precedence: exact semver matches sort
    higher than tagged versions, then ``@latest`` is last among versioned
    entries.
    """
    ac_dir = base / "ansible_collections"
    if not ac_dir.is_dir():
        return []

    results: list[CollectionVersionInfo] = []
    for entry in sorted(ac_dir.iterdir()):
        if not entry.is_dir():
            continue
        m = _VERSION_DIR_RE.match(entry.name)
        if not m:
            continue
        ns_name, version = m.group(1), m.group(2)
        if namespace is not None and ns_name != namespace:
            continue
        for coll_dir in sorted(entry.iterdir()):
            if not coll_dir.is_dir():
                continue
            coll_name = coll_dir.name
            if collection is not None and coll_name != collection:
                continue
            results.append(
                CollectionVersionInfo(
                    namespace=ns_name,
                    collection=coll_name,
                    version=version,
                    path=coll_dir,
                )
            )
    return results


def _semver_key(version: str) -> tuple[int | str, ...]:
    """Sort key: semver versions sort descending, tagged sort after."""
    parts = version.split(".")
    if len(parts) >= 3 and parts[0].isdigit():
        try:
            return (0, -(int(parts[0])), -(int(parts[1])), -(int(parts[2])))
        except (ValueError, IndexError):
            pass
    return (1, version)


def list_collection_versions(
    base: Path,
    namespace: str,
    collection: str | None = None,
) -> list[str]:
    """List distinct version strings for a collection at *base*.

    Returns versions sorted by precedence: semver (highest first),
    then tagged versions alphabetically, then ``latest`` last.
    """
    infos = scan_collection_versions(base, namespace=namespace, collection=collection)
    versions = sorted({i.version for i in infos}, key=_semver_key)
    return versions


def resolve_collection_version(
    base: Path,
    namespace: str,
    collection: str,
    requested_version: str | None = None,
) -> Path | None:
    """Resolve the best matching versioned collection directory.

    Precedence:
        1. Exact match on *requested_version* (if given).
        2. ``@latest`` tagged directory.
        3. Bare (unversioned) directory
           ``ansible_collections/<ns>/<coll>/``.
        4. Highest semver among remaining versioned directories.

    Returns the collection-root path (the directory containing
    ``roles/``, ``plugins/`` etc.) or *None* if no matching collection
    exists at *base*.
    """
    infos = scan_collection_versions(
        base, namespace=namespace, collection=collection
    )

    if requested_version is not None:
        for info in infos:
            if info.version == requested_version:
                return info.path
        return None

    for info in infos:
        if info.is_latest:
            return info.path

    bare = base / "ansible_collections" / namespace / collection
    if bare.is_dir():
        return bare

    semvers = [i for i in infos if i.is_semver]
    if semvers:
        semvers.sort(key=lambda i: _semver_key(i.version))
        return semvers[0].path

    if infos:
        return infos[0].path

    return None


def activate_collection_version(
    base: Path,
    namespace: str,
    collection: str,
    version: str | None = None,
    temp_dir: Path | None = None,
) -> tuple[Path, Path | None]:
    """Create a symlink-based activation so ansible resolves the right version.

    Creates (or reuses) a temporary directory containing::

        <temp_dir>/ansible_collections/<namespace>/<collection>
            → symlink to the resolved version's collection root.

    Returns ``(activation_root, cleanup_dir)`` where *activation_root* is
    the path to prepend to ``ANSIBLE_COLLECTIONS_PATH`` and *cleanup_dir*
    is the caller-owned temp directory (or *None* if *temp_dir* was
    supplied externally).

    When *version* is *None* the normal precedence rules apply
    (``@latest`` > bare > highest semver).
    """
    resolved = resolve_collection_version(
        base, namespace=namespace, collection=collection,
        requested_version=version,
    )
    if resolved is None:
        raise FileNotFoundError(
            f"No collection found for {namespace}.{collection}"
            + (f" @{version}" if version else "")
            + f" under {base}"
        )

    owned_by_caller = temp_dir is None
    if temp_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gludd-collections-"))
    ns_dir = temp_dir / "ansible_collections" / namespace
    ns_dir.mkdir(parents=True, exist_ok=True)
    link = ns_dir / collection
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(resolved, target_is_directory=True)
    return (temp_dir, temp_dir if owned_by_caller else None)


def list_all_collections(
    base: Path,
    namespace: str = "general_ludd",
) -> list[str]:
    """List all collection names discovered under a namespace at *base*.

    Scans ``<base>/ansible_collections/<namespace>/`` and returns the
    directory names found there (each is a collection name). Returns an
    empty list if the namespace directory does not exist.
    """
    ns_dir = base / "ansible_collections" / namespace
    if not ns_dir.is_dir():
        return []
    return sorted(
        d.name for d in ns_dir.iterdir() if d.is_dir()
    )


__all__ = [
    "CollectionVersionInfo",
    "CollectionsPathEntry",
    "activate_collection_version",
    "find_resource",
    "list_all_collections",
    "list_collection_versions",
    "resolve_collection_version",
    "resolve_collections_paths",
    "scan_collection_versions",
    "to_ansible_cfg",
    "to_ansible_env",
]
