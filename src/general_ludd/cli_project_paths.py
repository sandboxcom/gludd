"""CLI subcommand: ``gludd project paths``.

Diagnostic that prints the resolved 3-tier ansible collections precedence
for a project dir, with per-tier role/module counts. Local filesystem only
— no daemon connection.

Output shape (default):

    Collection search path (highest precedence first):
      1. PROJECT   /path/to/.gludd/collections/   (exists, 3 roles, 5 modules)
      2. USER      /path/to/.config/gludd/colls/  (missing)
      3. BUNDLED   /path/to/collections/          (exists, 70 roles, 18 modules)

With ``--json`` the same data is emitted as a JSON list for scripting.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from general_ludd.ansible.paths import (
    CollectionsPathEntry,
    resolve_collections_paths,
)

_SOURCE_LABELS = {
    "project": "PROJECT",
    "user": "USER   ",
    "bundled": "BUNDLED",
}


def _count_roles(tier_root: Path) -> int:
    """Count distinct roles across all collections under *tier_root*.

    Roles live at ``ansible_collections/<ns>/<coll>/roles/<name>/``.
    """
    if not tier_root.is_dir():
        return 0
    ac_root = tier_root / "ansible_collections"
    if not ac_root.is_dir():
        return 0
    count = 0
    for ns_dir in ac_root.iterdir():
        if not ns_dir.is_dir():
            continue
        for coll_dir in ns_dir.iterdir():
            roles_dir = coll_dir / "roles"
            if not roles_dir.is_dir():
                continue
            for role_dir in roles_dir.iterdir():
                if role_dir.is_dir() and (role_dir / "tasks").is_dir():
                    count += 1
                elif role_dir.is_dir() and role_dir.name != "__pycache__":
                    # Be lenient: a role without tasks/ is still a role dir.
                    count += 1
    return count


def _count_modules(tier_root: Path) -> int:
    """Count distinct module files across all collections under *tier_root*.

    Modules live at ``ansible_collections/<ns>/<coll>/plugins/modules/<x>.py``.
    """
    if not tier_root.is_dir():
        return 0
    ac_root = tier_root / "ansible_collections"
    if not ac_root.is_dir():
        return 0
    count = 0
    for ns_dir in ac_root.iterdir():
        if not ns_dir.is_dir():
            continue
        for coll_dir in ns_dir.iterdir():
            mods_dir = coll_dir / "plugins" / "modules"
            if not mods_dir.is_dir():
                continue
            for mod_file in mods_dir.glob("*.py"):
                if mod_file.name == "__init__.py":
                    continue
                count += 1
    return count


def _entry_to_record(entry: CollectionsPathEntry) -> dict[str, Any]:
    exists = entry.path.is_dir()
    return {
        "source": entry.source,
        "path": str(entry.path),
        "precedence": entry.precedence,
        "exists": exists,
        "roles": _count_roles(entry.path) if exists else 0,
        "modules": _count_modules(entry.path) if exists else 0,
    }


def _format_table(records: list[dict[str, Any]]) -> str:
    lines = ["Collection search path (highest precedence first):"]
    for rec in records:
        idx = rec["precedence"] + 1
        label = _SOURCE_LABELS.get(rec["source"], rec["source"].upper())
        path = rec["path"]
        if rec["exists"]:
            roles_s = "" if rec["roles"] == 1 else "s"
            mods_s = "" if rec["modules"] == 1 else "s"
            state = (
                f"exists, {rec['roles']} role{roles_s}, "
                f"{rec['modules']} module{mods_s}"
            )
        else:
            state = "missing"
        lines.append(f"  {idx}. {label}   {path}   ({state})")
    return "\n".join(lines)


def render_project_paths(
    project_root: Path | str | None, *, as_json: bool
) -> str:
    """Render the precedence table or JSON. Returns the string to print."""
    entries = resolve_collections_paths(project_root=project_root)
    records = [_entry_to_record(e) for e in entries]
    if as_json:
        return json.dumps(records, indent=2)
    return _format_table(records)


def _cmd_project_paths(args: argparse.Namespace) -> None:
    project_dir = getattr(args, "project_dir", None)
    as_json = bool(getattr(args, "json", False))
    project_root = Path(project_dir) if project_dir else None
    out = render_project_paths(project_root, as_json=as_json)
    print(out)


def add_project_paths_subparser(
    proj_sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = proj_sub.add_parser(
        "paths",
        help=(
            "Print the resolved 3-tier ansible collections precedence "
            "(project > user > bundled) with per-tier role/module counts."
        ),
    )
    p.add_argument(
        "project_dir",
        nargs="?",
        default=None,
        help=(
            "Project directory to resolve from (default: current working "
            "directory)."
        ),
    )
    p.add_argument(
        "--json",
        dest="json",
        action="store_true",
        help="Emit the precedence table as JSON for scripting.",
    )
    p.set_defaults(func=_cmd_project_paths)


__all__ = [
    "_cmd_project_paths",
    "add_project_paths_subparser",
    "render_project_paths",
]
