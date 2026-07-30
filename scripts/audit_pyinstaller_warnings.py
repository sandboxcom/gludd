#!/usr/bin/env python3
"""Fail closed on unreviewed PyInstaller missing-module warnings.

PyInstaller's warning file contains one edge per missing module and importer.
Conditional and optional imports can be harmless, but only an exact,
evidence-backed edge in the platform/version-specific allowlist is accepted.
Top-level and delayed-only imports are always actionable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1
_KNOWN_FLAGS = frozenset({"conditional", "delayed", "optional", "top-level"})
_REVIEWABLE_FLAGS = frozenset({"conditional", "optional"})
_CATEGORIES = frozenset(
    {
        "interpreter-specific",
        "optional-dependency",
        "platform-specific",
    }
)
_WARNING_RE = re.compile(
    r"^missing module named (?P<module>.+?) - imported by (?P<importers>.+)$"
)
_IMPORTER_RE = re.compile(
    r"(?:^|, )(?P<importer>[^,]+?) "
    r"\((?P<flags>[^)]+)\)(?=, |$)"
)
_HEADER_PREFIXES = (
    "* ",
    "IMPORTANT:",
    "Do not post",
    "Python and",
    "Python 3rd-party",
    "The following",
    "This file lists",
    "Types if import:",
    "necessarily mean",
    "tracking down",
)


class AuditError(ValueError):
    """Raised when audit inputs violate the fail-closed schema."""


@dataclass(frozen=True, order=True)
class MissingImportEdge:
    """One exact missing-module relationship from PyInstaller analysis."""

    module: str
    importer: str
    flags: tuple[str, ...]

    def render(self) -> str:
        flags = ", ".join(self.flags)
        return f"{self.module} <- {self.importer} ({flags})"


def _normalize_module(raw_module: str) -> str:
    module = raw_module.strip()
    if (
        len(module) >= 2
        and module[0] in {"'", '"'}
        and module[-1] == module[0]
    ):
        module = module[1:-1]
    if not module:
        raise AuditError("missing module name is empty")
    return module


def _parse_importers(raw_importers: str, module: str) -> list[MissingImportEdge]:
    matches = list(_IMPORTER_RE.finditer(raw_importers))
    if not matches:
        raise AuditError(f"unrecognized importer syntax for missing module {module!r}")

    cursor = 0
    edges: list[MissingImportEdge] = []
    for match in matches:
        if match.start() != cursor:
            raise AuditError(
                f"unrecognized importer syntax for missing module {module!r}: "
                f"{raw_importers[cursor:]!r}"
            )
        cursor = match.end()
        importer = match.group("importer").strip()
        raw_flags = [flag.strip() for flag in match.group("flags").split(",")]
        if not importer or any(not flag for flag in raw_flags):
            raise AuditError(f"empty importer or flag for missing module {module!r}")
        flags = tuple(sorted(raw_flags))
        unknown_flags = set(flags) - _KNOWN_FLAGS
        if unknown_flags:
            rendered = ", ".join(sorted(unknown_flags))
            raise AuditError(
                f"unknown PyInstaller import flags for {module!r}: {rendered}"
            )
        if len(flags) != len(set(flags)):
            raise AuditError(f"duplicate import flags for missing module {module!r}")
        edges.append(
            MissingImportEdge(
                module=module,
                importer=importer,
                flags=flags,
            )
        )

    if cursor != len(raw_importers):
        raise AuditError(
            f"unrecognized importer syntax for missing module {module!r}: "
            f"{raw_importers[cursor:]!r}"
        )
    return edges


def _parse_warning_file(path: Path) -> list[MissingImportEdge]:
    if not path.is_file():
        raise AuditError(f"warning file does not exist: {path}")

    edges: list[MissingImportEdge] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        match = _WARNING_RE.fullmatch(line)
        if match:
            module = _normalize_module(match.group("module"))
            edges.extend(_parse_importers(match.group("importers"), module))
            continue
        if line.startswith(_HEADER_PREFIXES):
            continue
        raise AuditError(
            f"unrecognized warning-file line {line_number}: {line!r}"
        )

    duplicates = {edge for edge in edges if edges.count(edge) > 1}
    if duplicates:
        rendered = "; ".join(edge.render() for edge in sorted(duplicates))
        raise AuditError(f"duplicate missing-import edges in warning file: {rendered}")
    return edges


def _require_string(entry: dict[str, Any], key: str, index: int) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AuditError(
            f"allowlist entry {index} requires a non-empty {key}"
        )
    return value.strip()


def _parse_allowlist(
    path: Path,
    *,
    platform: str,
    pyinstaller_version: str,
) -> list[MissingImportEdge]:
    if not path.is_file():
        raise AuditError(f"allowlist file does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot parse allowlist {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AuditError("allowlist root must be a JSON object")
    expected_root_keys = {
        "allowed_missing_imports",
        "platform",
        "pyinstaller_version",
        "schema_version",
    }
    if set(data) != expected_root_keys:
        raise AuditError(
            "allowlist root keys must be exactly: "
            + ", ".join(sorted(expected_root_keys))
        )
    if data["schema_version"] != _SCHEMA_VERSION:
        raise AuditError(
            f"unsupported allowlist schema_version: {data['schema_version']!r}"
        )
    if data["platform"] != platform:
        raise AuditError(
            f"allowlist platform mismatch: expected {platform!r}, "
            f"found {data['platform']!r}"
        )
    if data["pyinstaller_version"] != pyinstaller_version:
        raise AuditError(
            "allowlist PyInstaller version mismatch: "
            f"expected {pyinstaller_version!r}, "
            f"found {data['pyinstaller_version']!r}"
        )

    raw_entries = data["allowed_missing_imports"]
    if not isinstance(raw_entries, list):
        raise AuditError("allowed_missing_imports must be a JSON list")

    edges: list[MissingImportEdge] = []
    expected_entry_keys = {
        "category",
        "evidence",
        "flags",
        "importer",
        "module",
    }
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            raise AuditError(f"allowlist entry {index} must be a JSON object")
        if set(raw_entry) != expected_entry_keys:
            raise AuditError(
                f"allowlist entry {index} keys must be exactly: "
                + ", ".join(sorted(expected_entry_keys))
            )
        module = _require_string(raw_entry, "module", index)
        importer = _require_string(raw_entry, "importer", index)
        raw_category = raw_entry.get("category")
        raw_evidence = raw_entry.get("evidence")
        if (
            not isinstance(raw_category, str)
            or not raw_category.strip()
            or not isinstance(raw_evidence, str)
            or not raw_evidence.strip()
        ):
            raise AuditError(
                f"allowlist entry {index} requires non-empty category and evidence"
            )
        category = raw_category.strip()
        evidence = raw_evidence.strip()
        if category not in _CATEGORIES:
            raise AuditError(
                f"allowlist entry {index} has unsupported category {category!r}"
            )
        if not evidence.startswith("https://"):
            raise AuditError(
                f"allowlist entry {index} evidence must be an https URL"
            )

        raw_flags = raw_entry["flags"]
        if (
            not isinstance(raw_flags, list)
            or not raw_flags
            or any(not isinstance(flag, str) or not flag for flag in raw_flags)
        ):
            raise AuditError(
                f"allowlist entry {index} flags must be a non-empty string list"
            )
        flags = tuple(sorted(raw_flags))
        if len(flags) != len(set(flags)):
            raise AuditError(f"allowlist entry {index} has duplicate flags")
        unknown_flags = set(flags) - _KNOWN_FLAGS
        if unknown_flags:
            rendered = ", ".join(sorted(unknown_flags))
            raise AuditError(
                f"allowlist entry {index} has unknown flags: {rendered}"
            )
        edges.append(
            MissingImportEdge(
                module=module,
                importer=importer,
                flags=flags,
            )
        )

    duplicates = {edge for edge in edges if edges.count(edge) > 1}
    if duplicates:
        rendered = "; ".join(edge.render() for edge in sorted(duplicates))
        raise AuditError(f"duplicate allowlist edges: {rendered}")
    return edges


def _is_actionable(edge: MissingImportEdge) -> bool:
    flags = set(edge.flags)
    return "top-level" in flags or not flags.intersection(_REVIEWABLE_FLAGS)


def _audit(
    warning_edges: list[MissingImportEdge],
    allowed_edges: list[MissingImportEdge],
) -> list[str]:
    warning_set = set(warning_edges)
    allowed_set = set(allowed_edges)
    failures: list[str] = []

    for edge in sorted(warning_set):
        if _is_actionable(edge):
            failures.append(f"actionable import edge: {edge.render()}")
        if edge not in allowed_set:
            failures.append(f"unreviewed missing-import edge: {edge.render()}")
    for edge in sorted(allowed_set - warning_set):
        failures.append(f"stale allowlist edge: {edge.render()}")
    return failures


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warnings", required=True, type=Path)
    parser.add_argument("--allowlist", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--pyinstaller-version", required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        warning_edges = _parse_warning_file(args.warnings)
        allowed_edges = _parse_allowlist(
            args.allowlist,
            platform=args.platform,
            pyinstaller_version=args.pyinstaller_version,
        )
    except AuditError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    failures = _audit(warning_edges, allowed_edges)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    print(
        "PASS: audited "
        f"{len(warning_edges)} reviewed missing-import edges "
        f"(platform={args.platform}, PyInstaller={args.pyinstaller_version})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
