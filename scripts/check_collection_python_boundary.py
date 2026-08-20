#!/usr/bin/env python3
"""Enforce Gludd's collection/controller Python runtime boundary.

The migration inventory is intentionally exact: each accepted legacy finding
is keyed by path, line, rule, and a SHA-256 of the offending source line.  It
therefore cannot act as a broad path or glob allowlist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_CORE_IMPORT = re.compile(
    r"(?:^|[;\s])(?:from\s+general_ludd(?:\.|\s)|import\s+general_ludd(?:\.|\s|$)|"
    r"(?:import_module|__import__)\(\s*['\"]general_ludd(?:\.|['\"]))"
)
_SYS_PATH = re.compile(r"\bsys\.path\.(?:insert|append|extend)\s*\(")
_AMBIENT_PYTHON = re.compile(
    r"(?:^|[\s:'\"=])(?:/usr/bin/python3?|/usr/local/bin/python3?|python3?|py)(?:\s|$)"
)
_PYTHON_SUFFIXES = {".py"}
_YAML_SUFFIXES = {".yml", ".yaml"}
_SKIP_PARTS = {"docs", "tests", "molecule", ".pytest_cache", "__pycache__"}


@dataclass(frozen=True, order=True)
class Finding:
    """One exact release-path boundary violation."""

    path: str
    line: int
    rule: str
    text_hash: str

    def key(self) -> tuple[str, int, str, str]:
        """Return the immutable exact-inventory identity."""
        return (self.path, self.line, self.rule, self.text_hash)

    def as_dict(self) -> dict[str, object]:
        """Render stable JSON data."""
        return {
            "path": self.path,
            "line": self.line,
            "rule": self.rule,
            "text_hash": self.text_hash,
        }


def _is_release_path(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in _SKIP_PARTS for part in relative.parts):
        return False
    if path.suffix in _PYTHON_SUFFIXES:
        return "plugins" in relative.parts or (
            "roles" in relative.parts and "files" in relative.parts
        )
    if path.suffix in _YAML_SUFFIXES:
        return "roles" in relative.parts and "tasks" in relative.parts
    return False


def _line_findings(path: Path, relative: str, line_no: int, line: str) -> Iterable[Finding]:
    rules: list[str] = []
    if path.suffix in _PYTHON_SUFFIXES:
        if _CORE_IMPORT.search(line):
            rules.append("core-import")
        if _SYS_PATH.search(line):
            rules.append("sys-path-mutation")
    elif path.suffix in _YAML_SUFFIXES and _AMBIENT_PYTHON.search(line):
        rules.append("ambient-python")
    text_hash = hashlib.sha256(line.strip().encode("utf-8")).hexdigest()
    for rule in rules:
        yield Finding(path=relative, line=line_no, rule=rule, text_hash=text_hash)


def scan_collections(root: Path) -> list[Finding]:
    """Return deterministic findings from shipped collection execution paths."""
    if not root.is_dir():
        raise FileNotFoundError(f"collections root does not exist: {root}")
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _is_release_path(path, root):
            continue
        relative = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError(f"non-UTF-8 collection source: {relative}") from exc
        for line_no, line in enumerate(lines, start=1):
            findings.extend(_line_findings(path, relative, line_no, line))
    return sorted(findings)


def load_inventory(path: Path) -> dict[tuple[str, int, str, str], Finding]:
    """Load and validate an exact migration inventory."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or not isinstance(raw.get("findings"), list):
        raise ValueError("inventory must use schema_version 1 with a findings list")
    result: dict[tuple[str, int, str, str], Finding] = {}
    for item in raw["findings"]:
        if not isinstance(item, dict):
            raise ValueError("inventory findings must be objects")
        finding = Finding(
            path=str(item.get("path", "")),
            line=int(item.get("line", 0)),
            rule=str(item.get("rule", "")),
            text_hash=str(item.get("text_hash", "")),
        )
        if (
            not finding.path
            or finding.line < 1
            or finding.rule not in {"ambient-python", "core-import", "sys-path-mutation"}
            or re.fullmatch(r"[0-9a-f]{64}", finding.text_hash) is None
        ):
            raise ValueError(f"invalid inventory finding: {item!r}")
        if finding.key() in result:
            raise ValueError(f"duplicate inventory finding: {finding.key()!r}")
        result[finding.key()] = finding
    return result


def validate_inventory(
    findings: list[Finding],
    inventory: dict[tuple[str, int, str, str], Finding],
    *,
    strict_zero: bool,
) -> list[str]:
    """Compare findings with the exact inventory or enforce zero findings."""
    if strict_zero:
        if findings:
            return [f"strict-zero violation: {len(findings)} collection Python boundary finding(s) remain"]
        return []
    actual = {finding.key(): finding for finding in findings}
    errors: list[str] = []
    for key in sorted(actual.keys() - inventory.keys()):
        finding = actual[key]
        errors.append(f"new finding: {finding.path}:{finding.line} [{finding.rule}] {finding.text_hash}")
    for key in sorted(inventory.keys() - actual.keys()):
        finding = inventory[key]
        errors.append(f"stale inventory: {finding.path}:{finding.line} [{finding.rule}] {finding.text_hash}")
    return errors


def _write_inventory(path: Path, findings: list[Finding]) -> None:
    payload = {
        "schema_version": 1,
        "policy": "exact-path-line-rule-and-source-line-sha256",
        "findings": [finding.as_dict() for finding in findings],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the checker CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collections-root", type=Path, default=Path("collections/ansible_collections"))
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("config/ansible/collection-python-boundary-inventory.json"),
    )
    parser.add_argument("--strict-zero", action="store_true")
    parser.add_argument("--write-inventory", action="store_true")
    args = parser.parse_args(argv)

    findings = scan_collections(args.collections_root)
    if args.write_inventory:
        if args.strict_zero:
            parser.error("--write-inventory and --strict-zero are mutually exclusive")
        _write_inventory(args.inventory, findings)
        print(f"COLLECTION_PYTHON_BOUNDARY_INVENTORY_WRITTEN findings={len(findings)} path={args.inventory}")
        return 0

    try:
        inventory = load_inventory(args.inventory)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"COLLECTION_PYTHON_BOUNDARY_FAIL inventory={exc}", file=sys.stderr)
        return 2
    errors = validate_inventory(findings, inventory, strict_zero=args.strict_zero)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    mode = "strict-zero" if args.strict_zero else "exact-inventory"
    print(f"COLLECTION_PYTHON_BOUNDARY_PASS mode={mode} findings={len(findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
