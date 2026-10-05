#!/usr/bin/env python3
"""Create an exact, reviewable receipt for a PyInstaller graph change.

The normal warning audit intentionally fails on any transitive graph drift.  A
reviewer then uses this tool with the last accepted raw warning file and the new
candidate.  The receipt contains every normalized added and removed transitive
edge, rejects an unrelated baseline, and reuses the production audit to reject
project-owned import regressions.  It never edits the allowlist itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts import audit_pyinstaller_warnings as warning_audit

parse_warning_file = warning_audit._parse_warning_file
warning_digest = warning_audit._warning_digest


class ComparisonError(ValueError):
    """Raised when a graph comparison cannot produce trustworthy evidence."""


@dataclass(frozen=True)
class ComparisonInputs:
    """Immutable inputs that identify one warning-graph review."""

    before: Path
    after: Path
    allowlist: Path
    spec: Path
    platform: str
    architecture: str
    pyinstaller_version: str


def _file_digest(path: Path) -> str:
    """Return the SHA-256 of the retained raw warning artifact."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ComparisonError(f"cannot read warning artifact {path}: {exc}") from exc


def _render(edges: set[warning_audit.MissingImportEdge]) -> list[str]:
    """Render a complete edge set in canonical review order."""
    return [edge.render() for edge in sorted(edges)]


def compare_warning_graphs(inputs: ComparisonInputs) -> dict[str, Any]:
    """Compare two graphs and return a deterministic, complete review receipt."""
    try:
        before_edges = parse_warning_file(inputs.before)
        after_edges = parse_warning_file(inputs.after)
        (
            allowed_edges,
            baseline_modules,
            accepted_primary,
            accepted_alternates,
            controller_runtime_edges,
        ) = warning_audit._parse_allowlist(
            inputs.allowlist,
            platform=inputs.platform,
            architecture=inputs.architecture,
            pyinstaller_version=inputs.pyinstaller_version,
        )
        active_excludes = warning_audit._active_analysis_excludes(
            inputs.spec,
            inputs.platform,
        )
        architecture = warning_audit._normalize_architecture(inputs.architecture)
    except warning_audit.AuditError as exc:
        raise ComparisonError(str(exc)) from exc

    before_set = set(before_edges)
    after_set = set(after_edges)
    before_transitive = warning_audit._transitive_edges(
        before_set,
        active_excludes,
        baseline_modules,
    )
    after_transitive = warning_audit._transitive_edges(
        after_set,
        active_excludes,
        baseline_modules,
    )
    before_digest = warning_digest(before_transitive)
    accepted_digests = {accepted_primary, *accepted_alternates}
    if before_digest not in accepted_digests:
        raise ComparisonError(
            "before graph is not accepted by the current policy: "
            f"found {before_digest}; accepted {', '.join(sorted(accepted_digests))}"
        )

    after_digest = warning_digest(after_transitive)
    after_failures = warning_audit._audit(
        after_edges,
        allowed_edges,
        active_excludes,
        baseline_modules,
        after_digest,
        set(),
        controller_runtime_edges,
    )
    if after_failures:
        raise ComparisonError(
            "candidate graph violates the fail-closed project audit: "
            + "; ".join(after_failures)
        )

    added = after_transitive - before_transitive
    removed = before_transitive - after_transitive
    return {
        "schema_version": 1,
        "platform": inputs.platform,
        "architecture": architecture,
        "pyinstaller_version": inputs.pyinstaller_version,
        "before": {
            "warning_sha256": _file_digest(inputs.before),
            "transitive_sha256": before_digest,
            "transitive_count": len(before_transitive),
        },
        "after": {
            "warning_sha256": _file_digest(inputs.after),
            "transitive_sha256": after_digest,
            "transitive_count": len(after_transitive),
        },
        "delta": {
            "added_count": len(added),
            "removed_count": len(removed),
            "added": _render(added),
            "removed": _render(removed),
        },
    }


def write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    """Atomically write canonical JSON without leaving partial evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--allowlist", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--pyinstaller-version", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    return parser


def main() -> int:
    """Write one review receipt, or fail without changing policy."""
    args = _build_parser().parse_args()
    inputs = ComparisonInputs(
        before=args.before,
        after=args.after,
        allowlist=args.allowlist,
        spec=args.spec,
        platform=args.platform,
        architecture=args.architecture,
        pyinstaller_version=args.pyinstaller_version,
    )
    try:
        receipt = compare_warning_graphs(inputs)
        write_receipt(args.receipt, receipt)
    except (ComparisonError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: PyInstaller warning review receipt "
        f"before={receipt['before']['transitive_sha256']} "
        f"after={receipt['after']['transitive_sha256']} "
        f"added={receipt['delta']['added_count']} "
        f"removed={receipt['delta']['removed_count']} "
        f"receipt={args.receipt}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
