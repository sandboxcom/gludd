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
import shlex
from pathlib import Path
from typing import Any, Literal

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.integrity.change_log import ChangeLogEntry, ChangeRecordStore
from general_ludd.integrity.fim_excludes import FIM_EXCLUDE_PATTERNS
from general_ludd.self_update.safe_writer import AtomicSafeWriter

# Canonical FIM exclude set — the SINGLE shared constant imported by both this
# module and cli.py ``_scan_local_integrity`` (was a copy-pasted literal that
# could drift). Backward-compatible alias kept for callers/tests.
_EXCLUDES = FIM_EXCLUDE_PATTERNS


def _excluded(path: str) -> bool:
    """Return True if ``path`` matches any canonical FIM exclude pattern.

    The path is separator-normalised (``\\`` -> ``/``) and case-folded before
    the (lower-case, ``/``-anchored) exclude regexes are applied, so a Windows
    backslash path or a case variant like ``X.PYC`` is still recognised.
    """
    norm = path.replace("\\", "/").casefold()
    return any(re.search(p, norm) for p in FIM_EXCLUDE_PATTERNS)


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
    # Normalise separators, collapse ``.``/``..``/redundant slashes, and
    # case-fold so comparisons are robust on case-insensitive filesystems
    # (macOS/Windows) without over-classifying real core paths.
    norm = os.path.normpath(resolved).replace(os.sep, "/").casefold()

    # Source-checkout heuristic: match ``src/general_ludd`` ONLY as whole path
    # COMPONENTS (anchored on ``/`` or start/end), so a sibling path that merely
    # *contains* the literal — e.g. ``.../src/general_ludd_notes/x`` — is NOT
    # misclassified as core.
    if re.search(r"(^|/)src/general_ludd(/|$)", norm):
        return "core"

    pkg_dir = _package_dir()
    if pkg_dir is not None:
        pkg_norm = os.path.normpath(str(pkg_dir)).replace(os.sep, "/").casefold().rstrip("/")
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


def _rel_from_src(file_path: str) -> str:
    """Return the target path relative to a ``src/general_ludd/`` checkout root.

    A recorded ``file_path`` is the ABSOLUTE on-disk location where the change
    was applied (e.g. ``/some/worktree/src/general_ludd/routers/x.py``).  To
    replay it into a *different* repo checkout (``--repo``), we re-anchor it at
    the ``src/general_ludd/`` marker so the write lands at the same package
    location beneath the target repo root (``src/general_ludd/routers/x.py``).

    When the path contains no ``src/general_ludd/`` marker it is returned
    UNCHANGED — an absolute path that does not live under the target repo will
    then be refused by :class:`AtomicSafeWriter` (fail-closed: we never smuggle
    an out-of-tree path into a write).
    """
    norm = file_path.replace(os.sep, "/")
    marker = "src/general_ludd/"
    idx = norm.find(marker)
    if idx == -1:
        return file_path
    return norm[idx:]


def _cmd_core_changes_commit(args: argparse.Namespace) -> int:
    """Materialise recorded change(s) into ``--repo`` and gated-commit them.

    Selection (over the UNPRUNED records only):

    * ``--id N`` — exactly the record with id ``N``.
    * ``--all`` — every unpruned record.
    * neither — default to records classified as ``core`` (changes to gludd
      itself); ``user`` changes are left for the operator's own project VCS.

    Excluded artefacts (compiled files, ``.git``, DBs) are always dropped.  Each
    selected record's ``new_content`` is written (via the confined
    :class:`AtomicSafeWriter`) to its ``src/general_ludd/`` location beneath the
    resolved repo root, then a single gated commit is attempted.  A record is
    marked committed ONLY after the gate passes (fail-closed: a failed gate
    leaves every record unpruned so it can be retried).
    """
    store = ChangeRecordStore(store_dir=getattr(args, "store_dir", "") or "")
    records = store.list_records(unpruned_only=True)

    # Never try to commit excluded artefacts.
    records = [r for r in records if not _excluded(r.file_path)]

    record_id = getattr(args, "id", None)
    if record_id is not None:
        selected = [r for r in records if r.id == int(record_id)]
    elif bool(getattr(args, "all", False)):
        selected = list(records)
    else:
        selected = [r for r in records if classify(r.file_path) == "core"]

    if not selected:
        print("(no matching recorded changes to commit)")
        return 0

    repo_root = Path(args.repo).resolve()
    writer = AtomicSafeWriter(workspace_root=repo_root)

    written: list[str] = []
    for rec in selected:
        rel = _rel_from_src(rec.file_path)
        # A path escaping the repo root raises ValueError here — fail-closed,
        # BEFORE any commit, so no record is marked.
        writer.write(rel, _as_text(rec.new_content))
        written.append(rel)

    gate_cmd = shlex.split(args.gate) if getattr(args, "gate", "") else ["make", "gate"]
    message = getattr(args, "message", "") or selected[0].reason
    result = GitAutomation(repo_path=args.repo).gated_commit(
        files=written, message=message, gate_cmd=gate_cmd
    )

    if not result.success:
        # Fail-closed: do NOT mark anything committed so it can be retried.
        print(f"commit failed: {result.message}")
        return 1

    for rec in selected:
        store.mark_committed(rec.id)
    print(result.commit_sha or "")
    return 0


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

    cmt = csub.add_parser(
        "commit",
        help="Materialise recorded change(s) into a repo and gated-commit them",
    )
    cmt.add_argument(
        "--all",
        action="store_true",
        dest="all",
        help="Commit every unpruned record (default: only core changes)",
    )
    cmt.add_argument(
        "--id",
        dest="id",
        type=int,
        default=None,
        help="Commit only the single record with this id",
    )
    cmt.add_argument(
        "--message",
        "-m",
        dest="message",
        default="",
        help="Commit message (default: the record's reason)",
    )
    cmt.add_argument(
        "--repo",
        dest="repo",
        required=True,
        help="Target repo checkout to materialise the change(s) into",
    )
    cmt.add_argument(
        "--gate",
        dest="gate",
        default="make gate",
        help="Gate command run before committing (default: 'make gate')",
    )
    cmt.add_argument(
        "--store-dir",
        default="",
        dest="store_dir",
        help="Read a specific ChangeRecordStore directory (default: user store)",
    )
    cmt.set_defaults(func=_cmd_core_changes_commit)
