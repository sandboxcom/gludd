"""CLI subcommand: ``gludd collection`` — multi-version collection management.

``gludd collection versions <namespace> [<collection>]``
    Lists available versions for a collection namespace, optionally filtered
    by collection name. Scans the resolved tiers (project > user > bundled).

``gludd collection activate <namespace.collection@version>``
    Creates a symlink-based activation for a specific collection version so
    the next playbook run uses the requested version. Prints the activation
    root path.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from general_ludd.ansible.paths import (
    activate_collection_version,
    list_collection_versions,
    resolve_collections_paths,
)

_FQCN_RE = r"^([a-z0-9_]+)\.([a-z0-9_]+)(?:@(.+))?$"


def _find_base(project_dir: str | None) -> Path:
    entries = resolve_collections_paths(
        project_root=Path(project_dir) if project_dir else None
    )
    for entry in entries:
        if entry.path.is_dir():
            return entry.path
    raise FileNotFoundError("No collections directory found in any tier")


def _cmd_collection_versions(args: argparse.Namespace) -> None:
    base = _find_base(getattr(args, "project_dir", None))
    versions = list_collection_versions(
        base, namespace=args.namespace, collection=getattr(args, "collection", None)
    )
    if versions:
        for v in versions:
            print(v)
    else:
        ns = args.namespace
        coll = getattr(args, "collection", None)
        label = f"{ns}" if coll is None else f"{ns}.{coll}"
        print(f"No versions found for {label}")


def _cmd_collection_activate(args: argparse.Namespace) -> None:
    import re

    spec: str = args.spec
    m = re.match(_FQCN_RE, spec)
    if m is None:
        print(f"error: invalid spec {spec!r}. Expected <namespace>.<collection>@<version>")
        raise SystemExit(2)
    namespace, collection, version = m.group(1), m.group(2), m.group(3)
    if version is None:
        version = None

    base = _find_base(getattr(args, "project_dir", None))
    root, _cleanup = activate_collection_version(
        base, namespace=namespace, collection=collection, version=version
    )
    link = root / "ansible_collections" / namespace / collection
    resolved = link.resolve()
    print(f"Activated {namespace}.{collection}@{version or '(latest)'}")
    print(f"  activation root: {root}")
    print(f"  resolved: {resolved}")


def add_collection_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    coll_parser = sub.add_parser(
        "collection", help="Multi-version collection management"
    )
    coll_parser.set_defaults(func=None)
    coll_sub = coll_parser.add_subparsers(dest="collection_command")

    versions_p = coll_sub.add_parser(
        "versions", help="List available collection versions"
    )
    versions_p.add_argument("namespace", help="Collection namespace")
    versions_p.add_argument(
        "collection", nargs="?", default=None, help="Collection name (optional)"
    )
    versions_p.add_argument(
        "--project-dir",
        default=None,
        help="Project directory to resolve from",
    )
    versions_p.set_defaults(func=_cmd_collection_versions)

    activate_p = coll_sub.add_parser(
        "activate", help="Activate a specific collection version"
    )
    activate_p.add_argument(
        "spec",
        help="Collection spec: <namespace>.<collection>@<version> or <namespace>.<collection>",
    )
    activate_p.add_argument(
        "--project-dir",
        default=None,
        help="Project directory to resolve from",
    )
    activate_p.set_defaults(func=_cmd_collection_activate)


__all__ = [
    "_cmd_collection_activate",
    "_cmd_collection_versions",
    "add_collection_subparser",
]
