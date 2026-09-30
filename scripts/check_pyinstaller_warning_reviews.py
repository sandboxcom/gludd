#!/usr/bin/env python3
"""Require complete receipts whenever a PyInstaller graph becomes accepted."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_ROOT_KEYS = {
    "after",
    "architecture",
    "before",
    "delta",
    "platform",
    "pyinstaller_version",
    "schema_version",
}
_GRAPH_KEYS = {"transitive_count", "transitive_sha256", "warning_sha256"}
_DELTA_KEYS = {"added", "added_count", "removed", "removed_count"}


class ReviewCheckError(ValueError):
    """Raised when policy history cannot be inspected safely."""


def _accepted(policy: dict[str, Any]) -> dict[str, set[str]]:
    primaries = policy.get("transitive_warning_sha256_by_architecture")
    alternates = policy.get("reviewed_transitive_warning_sha256_alternates_by_architecture")
    if not isinstance(primaries, dict) or not isinstance(alternates, dict):
        raise ReviewCheckError("policy digest maps must be JSON objects")
    accepted: dict[str, set[str]] = {}
    for architecture, primary in primaries.items():
        if not isinstance(architecture, str) or not isinstance(primary, str) or not _SHA256_RE.fullmatch(primary):
            raise ReviewCheckError("policy primary digests must be canonical SHA-256 strings")
        raw_alternates = alternates.get(architecture, [])
        if not isinstance(raw_alternates, list) or any(
            not isinstance(value, str) or not _SHA256_RE.fullmatch(value) for value in raw_alternates
        ):
            raise ReviewCheckError(f"policy alternates for {architecture} must be SHA-256 strings")
        accepted[architecture] = {primary, *raw_alternates}
    unknown_alternate_architectures = set(alternates) - set(primaries)
    if unknown_alternate_architectures:
        raise ReviewCheckError(
            "policy alternates have no primary architecture: "
            + ", ".join(sorted(unknown_alternate_architectures))
        )
    return accepted


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewCheckError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewCheckError(f"JSON root must be an object: {path}")
    return value


def _receipt_errors(
    receipt: dict[str, Any],
    *,
    architecture: str,
    digest: str,
    previous_accepted: set[str],
    platform: object,
    pyinstaller_version: object,
) -> list[str]:
    errors: list[str] = []
    if set(receipt) != _RECEIPT_ROOT_KEYS:
        return ["receipt root keys are incomplete or unknown"]
    if receipt.get("schema_version") != 1:
        errors.append("receipt schema_version must be 1")
    if receipt.get("architecture") != architecture:
        errors.append(f"receipt architecture must be {architecture}")
    if receipt.get("platform") != platform:
        errors.append(f"receipt platform must be {platform}")
    if receipt.get("pyinstaller_version") != pyinstaller_version:
        errors.append(f"receipt PyInstaller version must be {pyinstaller_version}")

    before = receipt.get("before")
    after = receipt.get("after")
    delta = receipt.get("delta")
    if not isinstance(before, dict) or set(before) != _GRAPH_KEYS:
        errors.append("receipt before graph keys are incomplete or unknown")
        before = {}
    if not isinstance(after, dict) or set(after) != _GRAPH_KEYS:
        errors.append("receipt after graph keys are incomplete or unknown")
        after = {}
    if not isinstance(delta, dict) or set(delta) != _DELTA_KEYS:
        errors.append("receipt delta keys are incomplete or unknown")
        delta = {}

    before_digest = before.get("transitive_sha256")
    if before_digest not in previous_accepted:
        errors.append("receipt before digest is not accepted by the previous policy")
    if after.get("transitive_sha256") != digest:
        errors.append("receipt after digest does not match the newly accepted digest")
    for graph_name, graph in (("before", before), ("after", after)):
        warning_sha = graph.get("warning_sha256")
        transitive_sha = graph.get("transitive_sha256")
        count = graph.get("transitive_count")
        if not isinstance(warning_sha, str) or not _SHA256_RE.fullmatch(warning_sha):
            errors.append(f"receipt {graph_name} warning_sha256 is invalid")
        if not isinstance(transitive_sha, str) or not _SHA256_RE.fullmatch(transitive_sha):
            errors.append(f"receipt {graph_name} transitive_sha256 is invalid")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            errors.append(f"receipt {graph_name} transitive_count is invalid")

    added = delta.get("added")
    removed = delta.get("removed")
    added_count = delta.get("added_count")
    removed_count = delta.get("removed_count")
    for name, edges in (("added", added), ("removed", removed)):
        if not isinstance(edges, list) or any(not isinstance(edge, str) or not edge for edge in edges):
            errors.append(f"receipt {name} edges must be non-empty strings")
        elif edges != sorted(set(edges)):
            errors.append(f"receipt {name} edges must be sorted and unique")
    if (
        not isinstance(added_count, int)
        or isinstance(added_count, bool)
        or not isinstance(added, list)
        or added_count != len(added)
    ):
        errors.append("receipt added_count does not match the complete edge list")
    if (
        not isinstance(removed_count, int)
        or isinstance(removed_count, bool)
        or not isinstance(removed, list)
        or removed_count != len(removed)
    ):
        errors.append("receipt removed_count does not match the complete edge list")
    if isinstance(added, list) and isinstance(removed, list) and set(added).intersection(removed):
        errors.append("receipt edge cannot be both added and removed")
    before_count = before.get("transitive_count")
    after_count = after.get("transitive_count")
    counts = (before_count, after_count, added_count, removed_count)
    if all(isinstance(value, int) and not isinstance(value, bool) for value in counts):
        # ``all`` does not narrow the four heterogeneous JSON values for mypy.
        assert isinstance(before_count, int)
        assert isinstance(after_count, int)
        assert isinstance(added_count, int)
        assert isinstance(removed_count, int)
        if after_count != before_count + added_count - removed_count:
            errors.append("receipt transitive counts do not reconcile with the delta")
    return errors


def validate_policy_change(
    before_policy: dict[str, Any],
    after_policy: dict[str, Any],
    receipt_dir: Path,
) -> list[str]:
    """Return every missing or invalid receipt for newly accepted digests."""
    previous = _accepted(before_policy)
    current = _accepted(after_policy)
    errors: list[str] = []
    for architecture, accepted_digests in sorted(current.items()):
        previous_digests = previous.get(architecture, set())
        for digest in sorted(accepted_digests - previous_digests):
            receipt_path = receipt_dir / f"{architecture}-{digest}.json"
            if not receipt_path.is_file():
                errors.append(f"missing review receipt for {architecture} digest {digest}")
                continue
            try:
                receipt = _read_json(receipt_path)
            except ReviewCheckError as exc:
                errors.append(str(exc))
                continue
            receipt_errors = _receipt_errors(
                receipt,
                architecture=architecture,
                digest=digest,
                previous_accepted=previous_digests,
                platform=after_policy.get("platform"),
                pyinstaller_version=after_policy.get("pyinstaller_version"),
            )
            errors.extend(f"{receipt_path}: {error}" for error in receipt_errors)
    return errors


def _git_json(ref: str, policy_path: Path) -> dict[str, Any] | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{policy_path.as_posix()}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReviewCheckError(f"cannot parse policy at {ref}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewCheckError(f"policy at {ref} is not a JSON object")
    return value


def _git_parent_refs(ref: str = "HEAD") -> list[str]:
    result = subprocess.run(
        ["git", "rev-list", "--parents", "--max-count=1", ref],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReviewCheckError(f"cannot inspect parents for {ref}: {result.stderr.strip()}")
    fields = result.stdout.strip().split()
    if not fields:
        raise ReviewCheckError(f"cannot inspect parents for {ref}: empty git response")
    return fields[1:]


def _union_parent_acceptance(policies: list[dict[str, Any]]) -> dict[str, Any]:
    accepted_by_architecture: dict[str, set[str]] = {}
    for policy in policies:
        for architecture, digests in _accepted(policy).items():
            accepted_by_architecture.setdefault(architecture, set()).update(digests)

    merged = dict(policies[0])
    merged["transitive_warning_sha256_by_architecture"] = {
        architecture: sorted(digests)[0]
        for architecture, digests in sorted(accepted_by_architecture.items())
    }
    merged["reviewed_transitive_warning_sha256_alternates_by_architecture"] = {
        architecture: sorted(digests)[1:]
        for architecture, digests in sorted(accepted_by_architecture.items())
    }
    return merged


def _historical_policy(policy_path: Path, current: dict[str, Any]) -> dict[str, Any]:
    head = _git_json("HEAD", policy_path)
    if head is None:
        return current
    if head != current:
        return head
    parents = [
        parent
        for ref in _git_parent_refs()
        if (parent := _git_json(ref, policy_path)) is not None
    ]
    if not parents:
        return current
    if len(parents) == 1:
        return parents[0]
    return _union_parent_acceptance(parents)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--receipt-dir", required=True, type=Path)
    parser.add_argument("--before-policy", type=Path)
    return parser


def main() -> int:
    """Validate working-tree or just-committed policy changes."""
    args = _build_parser().parse_args()
    try:
        current = _read_json(args.policy)
        previous = _read_json(args.before_policy) if args.before_policy else _historical_policy(args.policy, current)
        errors = validate_policy_change(previous, current, args.receipt_dir)
    except ReviewCheckError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: every newly accepted PyInstaller warning digest has a complete exact-delta receipt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
