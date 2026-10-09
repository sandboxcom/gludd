#!/usr/bin/env python3
"""Enforce the repository's strict line ceiling over every tracked text file."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING or __package__:
    from scripts.staged_snapshot import (
        GITLINK_MODE,
        SnapshotError,
        discover_staged_paths,
        discover_tracked_paths,
        read_index_blob,
    )
else:
    from staged_snapshot import (
        GITLINK_MODE,
        SnapshotError,
        discover_staged_paths,
        discover_tracked_paths,
        read_index_blob,
    )

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "config" / "file_line_limits.json"
REQUIRED_MAX_LINES_EXCLUSIVE = 2500
POLICY_KEYS = frozenset({"version", "max_lines_exclusive", "non_text_paths", "large_text_paths"})
NON_TEXT_ENTRY_KEYS = frozenset({"path", "reason"})
LARGE_TEXT_ENTRY_KEYS = frozenset({"path", "reason"})


class PolicyError(RuntimeError):
    """Raised when the checked-in policy is missing, malformed, or drifting."""


class InventoryError(RuntimeError):
    """Raised when Git cannot prove the complete tracked-file inventory."""


class AuditError(RuntimeError):
    """Raised when a tracked path cannot be classified and read safely."""


@dataclass(frozen=True, slots=True)
class Policy:
    """Validated line limit and exact non-text/large-text path exceptions."""

    max_lines_exclusive: int
    non_text_paths: frozenset[str]
    large_text_paths: frozenset[str]


@dataclass(frozen=True, slots=True)
class Finding:
    """One tracked text file at or above the exclusive line limit."""

    path: str
    line_count: int
    limit: int

    def render(self) -> str:
        return f"{self.path}: {self.line_count} lines; must be less than {self.limit} lines"


def _safe_relative_path(raw: object, *, source: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise PolicyError(f"{source} must be a non-empty repository-relative path")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or ".." in candidate.parts or str(candidate) != raw:
        raise PolicyError(f"{source} is not a normalized repository-relative path: {raw!r}")
    return raw


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read policy {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PolicyError("policy root must be a JSON object")
    return payload


def _parse_path_reason_entries(
    raw_entries: object,
    category: str,
    required_keys: frozenset[str],
    existing: set[str],
) -> set[str]:
    """Validate a list of {path, reason} policy entries and return the paths."""
    if not isinstance(raw_entries, list):
        raise PolicyError(f"{category} must be a list of exact path/reason objects")
    paths: set[str] = set()
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict) or frozenset(raw_entry) != required_keys:
            raise PolicyError(f"{category}[{index}] must contain exactly path and reason")
        path_value = _safe_relative_path(raw_entry["path"], source=f"{category}[{index}].path")
        reason = raw_entry["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise PolicyError(f"{category}[{index}].reason must be non-empty")
        if path_value in existing:
            raise PolicyError(f"path already listed in another policy category: {path_value}")
        if path_value in paths:
            raise PolicyError(f"duplicate {category} path: {path_value}")
        paths.add(path_value)
    return paths


def load_policy(path: Path) -> Policy:
    """Load the strict schema; the 2500-line ceiling is not configurable away."""

    payload = _load_json_object(path)
    actual_keys = frozenset(payload)
    if actual_keys != POLICY_KEYS:
        missing = sorted(POLICY_KEYS - actual_keys)
        unexpected = sorted(actual_keys - POLICY_KEYS)
        raise PolicyError(f"policy keys drifted: missing={missing} unexpected={unexpected}")
    if payload["version"] != 1:
        raise PolicyError("policy version must be exactly 1")
    if payload["max_lines_exclusive"] != REQUIRED_MAX_LINES_EXCLUSIVE:
        raise PolicyError(f"max_lines_exclusive must remain exactly {REQUIRED_MAX_LINES_EXCLUSIVE}")

    paths = _parse_path_reason_entries(payload["non_text_paths"], "non_text_paths", NON_TEXT_ENTRY_KEYS, set())
    large_paths = _parse_path_reason_entries(
        payload["large_text_paths"], "large_text_paths", LARGE_TEXT_ENTRY_KEYS, paths
    )
    return Policy(REQUIRED_MAX_LINES_EXCLUSIVE, frozenset(paths), frozenset(large_paths))


def _validate_inventory_path(raw: str) -> str:
    try:
        return _safe_relative_path(raw, source="Git tracked path")
    except PolicyError as exc:
        raise InventoryError(str(exc)) from exc


def _read_tracked_file(root: Path, relative_path: str) -> bytes:
    path = root / relative_path
    if path.is_symlink():
        try:
            return os.readlink(os.fsencode(path))
        except OSError as exc:
            raise AuditError(f"{relative_path}: tracked symbolic link is unreadable") from exc
    try:
        return path.read_bytes()
    except OSError as exc:
        raise AuditError(f"{relative_path}: tracked path is missing or unreadable") from exc


def _decode_text(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def audit_paths(
    root: Path,
    paths: Sequence[str],
    policy: Policy,
    *,
    staged: bool = False,
) -> list[Finding]:
    """Audit an exact tracked inventory, rejecting implicit skips and stale policy."""

    normalized = tuple(_validate_inventory_path(path) for path in paths)
    if len(normalized) != len(set(normalized)):
        raise AuditError("tracked path inventory contains duplicates")
    tracked = set(normalized)
    stale = sorted(policy.non_text_paths - tracked)
    if stale and not staged:
        raise AuditError("explicit non_text_paths entries are not tracked: " + ", ".join(stale))
    stale_large = sorted(policy.large_text_paths - tracked)
    if stale_large and not staged:
        raise AuditError("explicit large_text_paths entries are not tracked: " + ", ".join(stale_large))

    findings: list[Finding] = []
    for relative_path in normalized:
        repository_path = root / relative_path
        try:
            index_blob = read_index_blob(root, relative_path) if staged else None
        except SnapshotError as exc:
            raise AuditError(str(exc)) from exc
        is_gitlink = index_blob is not None and index_blob.mode == GITLINK_MODE
        if relative_path in policy.non_text_paths and (is_gitlink or (not staged and repository_path.is_dir())):
            # Git submodule entries are tracked gitlinks, not text files. They
            # remain exact-path policy entries so a new directory is never
            # silently omitted from the inventory.
            continue
        data = index_blob.data if index_blob is not None else _read_tracked_file(root, relative_path)
        text = _decode_text(data)
        if relative_path in policy.non_text_paths:
            if text is not None:
                raise AuditError(
                    f"{relative_path}: explicit non-text path is now UTF-8 text; remove the stale policy entry"
                )
            continue
        if relative_path in policy.large_text_paths:
            # Generated large text files (e.g., npm package-lock.json) are
            # exempt from the line-count ceiling but must remain UTF-8 text.
            continue
        if text is None:
            raise AuditError(f"{relative_path}: non-text content is absent from explicit non_text_paths policy")
        line_count = len(text.splitlines())
        if line_count >= policy.max_lines_exclusive:
            findings.append(Finding(relative_path, line_count, policy.max_lines_exclusive))
    return sorted(findings, key=lambda item: (-item.line_count, item.path))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Require every Git-tracked UTF-8 text file to have <2500 lines.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_POLICY)
    parser.add_argument(
        "--path",
        action="append",
        default=None,
        help="explicit tracked path for focused tests; repeat as needed",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="audit exact staged index blobs rather than working-tree files",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = load_policy(args.config)
        if args.path is not None:
            paths = tuple(args.path)
        elif args.staged:
            paths = discover_staged_paths(args.root)
        else:
            paths = discover_tracked_paths(args.root)
        findings = audit_paths(args.root, paths, policy, staged=args.staged)
    except (PolicyError, InventoryError, AuditError, SnapshotError) as exc:
        print(f"file-line-limits: ERROR: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding.render())
    if findings:
        print(
            f"file-line-limits: FAIL oversized={len(findings)} limit=<{policy.max_lines_exclusive}",
            file=sys.stderr,
        )
        return 1
    print(
        f"file-line-limits: PASS tracked={len(paths)} "
        f"source={'index' if args.staged else 'worktree'} "
        f"limit=<{policy.max_lines_exclusive}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
