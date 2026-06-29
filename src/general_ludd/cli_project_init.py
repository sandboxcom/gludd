"""CLI subcommand: ``gludd project init``.

Scaffolds a project-local ansible collection under
``<project_dir>/.gludd/collections/ansible_collections/<namespace>/<collection>/``
so an operator can immediately drop project-specific roles/modules into a
collection that gludd discovers via the 3-tier precedence contract
(project-local > user-level > bundled).

This module performs only local filesystem operations — no daemon connection.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_COLLECTION_NAME = "project"
DEFAULT_VERSION = "1.0.0"

_GALAXY_YML_TEMPLATE = """\
---
namespace: {namespace}
name: {collection}
version: {version}
readme: README.md
description: >
  Project-local ansible collection for {namespace}.{collection}. Holds
  project-specific roles, modules, and terraform/OPA content discovered by
  gludd at the project tier of the collection precedence contract.
license:
  - MIT
authors:
  - Project operator <operator@example.invalid>
tags:
  - project
  - gludd
  - automation
dependencies: {{}}
"""

_README_TEMPLATE = """\
# {namespace}.{collection}

Project-local ansible collection scaffolded by `gludd project init`.

## Collection precedence

gludd resolves FQCNs (`{namespace}.{collection}.<role_or_module>`) through a
3-tier precedence contract — first match wins:

1. **Project-local** (`<project>/.gludd/collections/...`) — this collection.
2. **User-level** (`~/.local/share/gludd/collections/...`) — operator-wide overrides.
3. **Bundled** (`general_ludd.agent`) — general-purpose roles shipped with gludd.

Add roles and modules here when they encode project-specific business logic or
team-private automation. Add to the bundled collection when the role is
general-purpose and shareable across projects.

## Layout

```
roles/                 project-specific ansible roles
plugins/modules/       project-specific ansible modules
plugins/module_utils/  shared module helpers
plugins/terraform/     project terraform modules / OPA policies (see COLLECTION_STRUCTURE.md)
```

Re-run `gludd project init --force` to refresh this scaffold.
"""

_GITIGNORE = """\
*.pyc
__pycache__/
"""


def _write_file(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _touch_keepfile(path: Path, *, force: bool) -> None:
    target = path / ".gitkeep"
    if target.exists() and not force:
        return
    path.mkdir(parents=True, exist_ok=True)
    target.write_text("")


def _update_config_yml(config_path: Path, namespace: str, collection: str) -> None:
    """Merge a `collection:` section into <project_dir>/.gludd/config.yml."""
    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            loaded = yaml.safe_load(config_path.read_text())
            if isinstance(loaded, dict):
                data = loaded
        except yaml.YAMLError:
            data = {}
    data["collection"] = {"namespace": namespace, "name": collection}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False))


def scaffold_project_collection(
    project_dir: Path,
    namespace: str,
    collection: str,
    *,
    force: bool,
) -> Path:
    """Create the collection scaffold and return the collection root path."""
    collection_root = (
        project_dir
        / ".gludd"
        / "collections"
        / "ansible_collections"
        / namespace
        / collection
    )
    galaxy_path = collection_root / "galaxy.yml"
    if galaxy_path.exists() and not force:
        print(
            f"Error: {galaxy_path} already exists. "
            "Pass --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)

    collection_root.mkdir(parents=True, exist_ok=True)

    _write_file(
        galaxy_path,
        _GALAXY_YML_TEMPLATE.format(
            namespace=namespace,
            collection=collection,
            version=DEFAULT_VERSION,
        ),
        force=force,
    )
    _write_file(
        collection_root / "README.md",
        _README_TEMPLATE.format(namespace=namespace, collection=collection),
        force=force,
    )
    _write_file(collection_root / ".gitignore", _GITIGNORE, force=force)

    _touch_keepfile(collection_root / "roles", force=force)
    _touch_keepfile(collection_root / "plugins" / "modules", force=force)
    _touch_keepfile(collection_root / "plugins" / "module_utils", force=force)
    _touch_keepfile(collection_root / "plugins" / "terraform", force=force)

    config_path = project_dir / ".gludd" / "config.yml"
    _update_config_yml(config_path, namespace, collection)

    return collection_root


def _print_summary(
    collection_root: Path,
    namespace: str,
    collection: str,
    project_dir: Path,
) -> None:
    fqcn_prefix = f"{namespace}.{collection}"
    try:
        rel_root = collection_root.relative_to(project_dir)
    except ValueError:
        rel_root = collection_root
    print(f"Scaffolded project collection at: {rel_root}")
    print(f"FQCN prefix: {fqcn_prefix}.<role_or_module>")
    print(f"Config updated: {Path('.gludd') / 'config.yml'}")
    print()
    print("Precedence (first match wins):")
    print("  1. project-local  (.gludd/collections/...)  <- this collection")
    print("  2. user-level     (~/.local/share/gludd/collections/...)")
    print("  3. bundled        (general_ludd.agent)")


def _cmd_project_init(args: argparse.Namespace) -> None:
    project_dir = Path(getattr(args, "project_dir", None) or os_cwd())
    namespace = getattr(args, "namespace", None)
    collection = getattr(args, "collection", None) or DEFAULT_COLLECTION_NAME
    force = bool(getattr(args, "force", False))

    if not namespace:
        print("Error: --namespace is required.", file=sys.stderr)
        sys.exit(2)

    collection_root = scaffold_project_collection(
        project_dir,
        namespace,
        collection,
        force=force,
    )
    _print_summary(collection_root, namespace, collection, project_dir)


def os_cwd() -> str:
    import os

    return os.getcwd()


def add_project_init_subparser(proj_sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = proj_sub.add_parser(
        "init",
        help=(
            "Scaffold a project-local ansible collection under "
            ".gludd/collections/ (namespace + collection skeleton)."
        ),
    )
    p.add_argument(
        "project_dir",
        nargs="?",
        default=None,
        help="Project directory (default: current working directory).",
    )
    p.add_argument(
        "--namespace",
        required=True,
        help="Galaxy namespace for the project collection (e.g. acme).",
    )
    p.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help=f"Collection name (default: {DEFAULT_COLLECTION_NAME}).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files if the scaffold already exists.",
    )
    p.set_defaults(func=_cmd_project_init)
