"""CLI subcommand: ``gludd core-changes list`` (FIM change-export SLICE 2).

Renders the durable, tamper-evident agentic change log
(:class:`~general_ludd.integrity.change_log.ChangeRecordStore`, SLICE 1) as
human-readable unified diffs.  Each recorded change captures a lossless
``old_content``/``new_content`` snapshot, so a full diff of what an agent (or
the operator) changed can be produced WITHOUT the source file.

``list`` classifies every record as ``core`` (a change under the installed
``general_ludd`` package or a ``src/general_ludd/`` checkout — i.e. a change to
gludd ITSELF) or ``user`` (anything else — the operator's project/config),
and can be filtered to either side.  Paths matching the canonical FIM exclude
set (compiled artefacts, ``.git``, on-disk DBs) are dropped from the listing.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import importlib.util
import json as _json
import os
import re
from pathlib import Path
from typing import Any, Literal

from general_ludd.integrity.change_log import ChangeLogEntry, ChangeRecordStore

# Canonical FIM exclude set (mirrors cli.py ``_scan_local_integrity``): never
# surface compiled artefacts, VCS internals, or on-disk databases as "changes".
_EXCLUDES = [r"\.pyc$", r"__pycache__", r"\.git/", r"\.db$"]


def _excluded(path: str) -> bool:
    """Return True if ``path`` matches any canonical FIM exclude pattern."""
    return any(re.search(p, path) for p in _EXCLUDES)


def _package_dir() -> Path | None:
    """Resolve the installed ``general_ludd`` package directory (or None).

    Defensive: :func:`importlib.util.find_spec` can return ``None`` (or a spec
    with no submodule search locations) in an oddly-packaged environment — the
    caller treats that as "cannot prove core", i.e. ``user``.
    """
    try:
        spec = importlib.util.find_spec("general_ludd")
    except (ImportError, ValueError, ModuleNotFoundError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    try:
        return Path(next(iter(spec.submodule_search_locations))).resolve()
    except (OSError, RuntimeError):
        return None


def classify(file_path: str) -> Literal["core", "user"]:
    """Classify a change as ``core`` (gludd itself) or ``user`` (everything else).

    A path is ``core`` when its resolved location is under the installed
    ``general_ludd`` package directory, OR it lives under a ``src/general_ludd/``
    source checkout.  Anything else — the operator's project files, a
    ``~/.config/gludd`` overlay, an arbitrary path — is ``user``.  Fails safe to
    ``user`` when the package location cannot be determined.
    """
    try:
        resolved = str(Path(file_path).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        resolved = file_path
    norm = resolved.replace(os.sep, "/")

    # Source-checkout heuristic: a change to gludd's own tree.
    if "src/general_ludd/" in norm or norm.endswith("src/general_ludd"):
        return "core"

    pkg_dir = _package_dir()
    if pkg_dir is not None:
        pkg_norm = str(pkg_dir).replace(os.sep, "/").rstrip("/")
        if norm == pkg_norm or norm.startswith(pkg_norm + "/"):
            return "core"
    return "user"


def _as_text(content: str | bytes | None) -> str:
    """Coerce a captured snapshot to text for diffing (None -> empty)."""
    if content is None:
        return ""
    if isinstance(content, bytes):
        # A binary snapshot is diffed on its best-effort text projection; the
        # lossless bytes remain available in --json.
        return content.decode("utf-8", errors="replace")
    return content


def _diff(entry: ChangeLogEntry) -> str:
    """Render a unified diff of ``old_content`` -> ``new_content`` for ``entry``.

    A created record (empty/None ``old_content``) renders as an all-added diff;
    a deletion (empty ``new_content``) renders as all-removed.  Headers name the
    record's ``file_path`` on both the ``---`` (old) and ``+++`` (new) sides.
    """
    old_text = _as_text(entry.old_content)
    new_text = _as_text(getattr(entry, "new_content", None))
    diff = difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile="--- old " + entry.file_path,
        tofile="+++ new " + entry.file_path,
        lineterm="",
    )
    return "\n".join(diff)


def _print_json(obj: Any) -> None:
    print(_json.dumps(obj, indent=2, default=str))


def _cmd_core_changes_list(args: argparse.Namespace) -> None:
    store_dir = getattr(args, "store_dir", "") or ""
    store = ChangeRecordStore(store_dir=store_dir)
    records = store.list_records()

    # Drop excluded artefacts (compiled files, .git, DBs) from the listing.
    records = [r for r in records if not _excluded(r.file_path)]

    core_only = bool(getattr(args, "core_only", False))
    user_only = bool(getattr(args, "user_only", False))
    if core_only:
        records = [r for r in records if classify(r.file_path) == "core"]
    elif user_only:
        records = [r for r in records if classify(r.file_path) == "user"]

    if bool(getattr(args, "json", False)):
        _print_json([dataclasses.asdict(r) for r in records])
        return

    if not records:
        print("(no recorded changes)")
        return

    for r in records:
        tag = classify(r.file_path)
        print(f"#{r.id} [{tag}] {r.file_path}  ({r.change_type})")
        print(f"    reason: {r.reason}")
        diff = _diff(r)
        if diff:
            print(diff)
        print()


def add_core_changes_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "core-changes",
        help="Render agentic changes (change log) as core/user unified diffs",
    )
    p.set_defaults(func=None)
    csub = p.add_subparsers(dest="core_changes_command")

    lst = csub.add_parser("list", help="List recorded changes as unified diffs")
    lst.add_argument("--json", action="store_true", help="Dump records as JSON")
    lst.add_argument(
        "--core-only",
        action="store_true",
        dest="core_only",
        help="Only changes to gludd itself (the installed package / checkout)",
    )
    lst.add_argument(
        "--user-only",
        action="store_true",
        dest="user_only",
        help="Only changes outside gludd (the operator's project / config)",
    )
    lst.add_argument(
        "--store-dir",
        default="",
        dest="store_dir",
        help="Read a specific ChangeRecordStore directory (default: user store)",
    )
    lst.set_defaults(func=_cmd_core_changes_list)
