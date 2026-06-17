#!/usr/bin/env python3
"""
multitasking_backlog_check.py

CLI + importable module for the gludd multitasking/subagent-architecture backlog.

Exit codes:
  0 — all items done (or --open-count / --list-open queries completed OK)
  1 — one or more items are open/in_progress  (--assert-done)
  2 — file missing, unreadable, or malformed JSON / schema error

Env:
  GLUDD_MT_BACKLOG — override path to the JSON backlog file.

Anti-rubber-stamp rule: a "done" item with an empty "evidence" field is treated
as still-open (not done).  This mirrors the repo's anti-false-completion policy.

Fail-safe rule: a missing or malformed file exits 2 so a Stop hook sees a
non-zero exit and can fail-open rather than silently claiming all work done.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_DEFAULT = Path(__file__).parent / "multitasking_backlog.json"
_VALID_STATUSES = {"open", "in_progress", "done"}


# ---------------------------------------------------------------------------
# Pure data helpers
# ---------------------------------------------------------------------------

def _item_is_effectively_done(item: dict[str, Any]) -> bool:
    """
    Return True only when status == "done" AND evidence is non-empty.

    A "done" with empty evidence is treated as still-open to prevent
    rubber-stamping.
    """
    status = item.get("status", "")
    evidence = item.get("evidence", "")
    return status == "done" and bool(evidence and evidence.strip())


def _validate_item(item: Any, index: int) -> str | None:
    """Return an error string if the item is malformed, else None."""
    if not isinstance(item, dict):
        return f"items[{index}] is not an object"
    for required in ("id", "title", "status"):
        if required not in item:
            return f"items[{index}] missing required field '{required}'"
    if item["status"] not in _VALID_STATUSES:
        return (
            f"items[{index}] has invalid status {item['status']!r}; "
            f"must be one of {sorted(_VALID_STATUSES)}"
        )
    return None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_backlog(path: Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Load and validate the backlog JSON.

    Returns (items, errors).  If errors is non-empty the caller should treat
    the file as malformed and exit 2.

    Raises FileNotFoundError if the file does not exist (caller handles).
    """
    if path is None:
        env_path = os.environ.get("GLUDD_MT_BACKLOG", "")
        path = Path(env_path) if env_path else _REPO_DEFAULT

    if not path.exists():
        raise FileNotFoundError(f"Backlog file not found: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"Cannot read backlog file {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], [f"JSON parse error: {exc}"]

    if not isinstance(data, dict):
        return [], ["Top-level JSON value must be an object"]

    if "items" not in data:
        return [], ["Missing required top-level key 'items'"]

    items_raw = data["items"]
    if not isinstance(items_raw, list):
        return [], ["'items' must be a JSON array"]

    errors: list[str] = []
    items: list[dict[str, Any]] = []
    for i, item in enumerate(items_raw):
        err = _validate_item(item, i)
        if err:
            errors.append(err)
        else:
            items.append(item)

    return items, errors


# ---------------------------------------------------------------------------
# Query functions (pure — no sys.exit, no print)
# ---------------------------------------------------------------------------

def open_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return items that are not effectively done."""
    return [item for item in items if not _item_is_effectively_done(item)]


def open_count(items: list[dict[str, Any]]) -> int:
    """Return count of non-done items."""
    return len(open_items(items))


def all_done(items: list[dict[str, Any]]) -> bool:
    """Return True iff every item is effectively done."""
    return open_count(items) == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_path() -> Path | None:
    env_path = os.environ.get("GLUDD_MT_BACKLOG", "")
    return Path(env_path) if env_path else None


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    """Entry point; returns exit code."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspect the gludd multitasking backlog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--open-count",
        action="store_true",
        help="Print integer count of non-done items and exit 0.",
    )
    group.add_argument(
        "--list-open",
        action="store_true",
        help="Print 'id: title [status]' lines for non-done items and exit 0.",
    )
    group.add_argument(
        "--assert-done",
        action="store_true",
        help="Exit 0 if all items done, exit 1 if any open/in_progress.",
    )

    args = parser.parse_args(argv)
    path = _resolve_path()

    # --- load ---
    try:
        items, errors = load_backlog(path)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 2

    # --- dispatch ---
    if args.open_count:
        print(open_count(items))
        return 0

    if args.list_open:
        for item in open_items(items):
            print(f"{item['id']}: {item['title']} [{item['status']}]")
        return 0

    if args.assert_done:
        remaining = open_items(items)
        if remaining:
            print(
                f"Backlog has {len(remaining)} non-done item(s):",
                file=sys.stderr,
            )
            for item in remaining:
                print(
                    f"  {item['id']}: {item['title']} [{item['status']}]",
                    file=sys.stderr,
                )
            return 1
        return 0

    # unreachable (argparse enforces the mutually exclusive group)
    return 2


if __name__ == "__main__":
    sys.exit(main())
