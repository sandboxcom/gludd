#!/usr/bin/env python3
"""Fail closed on unreviewed PyInstaller missing-module warnings.

PyInstaller's warning file contains one edge per missing module and importer.
Conditional and optional imports can be harmless, but only an exact,
evidence-backed edge in the platform/version-specific allowlist is accepted.
Top-level and delayed-only imports are always actionable.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
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
    r"^(?P<kind>missing|excluded) module named "
    r"(?P<module>.+?) - imported by (?P<importers>.+)$"
)
_IMPORTER_RE = re.compile(
    r"(?:^|, )(?P<importer>[^,]+?) "
    r"\((?P<flags>[^)]+)\)(?=, |$)"
)
_HEADER_LINES = frozenset(
    {
        "This file lists modules PyInstaller was not able to find. This does not",
        "necessarily mean these modules are required for running your program. Both",
        "Python's standard library and 3rd-party Python packages often conditionally",
        "import optional modules, some of which may be available only on certain",
        "platforms.",
        "Types of import:",
        "* top-level: imported at the top-level - look at these first",
        "* conditional: imported within an if-statement",
        "* delayed: imported within a function",
        "* optional: imported within a try-except-statement",
        "IMPORTANT: Do NOT post this list to the issue-tracker. Use it as a basis for",
        "tracking down the missing module yourself. Thanks!",
    }
)


class AuditError(ValueError):
    """Raised when audit inputs violate the fail-closed schema."""


@dataclass(frozen=True, order=True)
class MissingImportEdge:
    """One exact missing-module relationship from PyInstaller analysis."""

    kind: str
    module: str
    importer: str
    flags: tuple[str, ...]

    def render(self) -> str:
        flags = ", ".join(self.flags)
        return f"{self.kind} {self.module} <- {self.importer} ({flags})"


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


def _parse_importers(
    raw_importers: str,
    module: str,
    kind: str,
) -> list[MissingImportEdge]:
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
                kind=kind,
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
            edges.extend(
                _parse_importers(
                    match.group("importers"),
                    module,
                    match.group("kind"),
                )
            )
            continue
        if line in _HEADER_LINES:
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
                kind="missing",
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


def _literal_string_list(
    node: ast.expr,
    variables: dict[str, list[str]],
) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[str] = []
        for element in node.elts:
            if not isinstance(element, ast.Constant) or not isinstance(
                element.value,
                str,
            ):
                raise AuditError("Analysis.excludes must contain string literals")
            values.append(element.value)
        return values
    if isinstance(node, ast.Name) and node.id in variables:
        return list(variables[node.id])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_string_list(
            node.left,
            variables,
        ) + _literal_string_list(node.right, variables)
    raise AuditError(
        "Analysis.excludes must resolve to literal lists and named literal lists"
    )


def _sys_platform_value(platform: str) -> str:
    aliases = {
        "darwin": "darwin",
        "linux": "linux",
        "macos": "darwin",
        "win32": "win32",
        "windows": "win32",
    }
    return aliases.get(platform, platform)


def _platform_condition(node: ast.expr, platform: str) -> bool | None:
    if (
        not isinstance(node, ast.Compare)
        or len(node.ops) != 1
        or len(node.comparators) != 1
        or not isinstance(node.left, ast.Attribute)
        or not isinstance(node.left.value, ast.Name)
        or node.left.value.id != "sys"
        or node.left.attr != "platform"
        or not isinstance(node.comparators[0], ast.Constant)
        or not isinstance(node.comparators[0].value, str)
    ):
        return None

    actual = _sys_platform_value(platform)
    expected = node.comparators[0].value
    operator = node.ops[0]
    if isinstance(operator, ast.Eq):
        return actual == expected
    if isinstance(operator, ast.NotEq):
        return actual != expected
    raise AuditError("unsupported sys.platform comparison in PyInstaller spec")


def _active_analysis_excludes(path: Path, platform: str) -> set[str]:
    if not path.is_file():
        raise AuditError(f"PyInstaller spec does not exist: {path}")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise AuditError(f"cannot parse PyInstaller spec {path}: {exc}") from exc

    variables: dict[str, list[str]] = {}
    analysis_excludes: list[list[str]] = []

    def process(statements: list[ast.stmt]) -> None:
        for statement in statements:
            if isinstance(statement, ast.If):
                condition = _platform_condition(statement.test, platform)
                if condition is not None:
                    process(statement.body if condition else statement.orelse)
                continue
            if not isinstance(statement, ast.Assign):
                continue
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    with contextlib.suppress(AuditError):
                        variables[target.id] = _literal_string_list(
                            statement.value,
                            variables,
                        )
            if not isinstance(statement.value, ast.Call):
                continue
            function = statement.value.func
            if not isinstance(function, ast.Name) or function.id != "Analysis":
                continue
            excludes_keyword = next(
                (
                    keyword
                    for keyword in statement.value.keywords
                    if keyword.arg == "excludes"
                ),
                None,
            )
            if excludes_keyword is None:
                raise AuditError("Analysis call has no excludes keyword")
            analysis_excludes.append(
                _literal_string_list(excludes_keyword.value, variables)
            )

    process(tree.body)
    if len(analysis_excludes) != 1:
        raise AuditError(
            "PyInstaller spec must contain exactly one Analysis(excludes=...) call"
        )
    excludes = analysis_excludes[0]
    if len(excludes) != len(set(excludes)):
        raise AuditError("active Analysis.excludes contains duplicate modules")
    return set(excludes)


def _is_actionable(edge: MissingImportEdge) -> bool:
    flags = set(edge.flags)
    return "top-level" in flags or not flags.intersection(_REVIEWABLE_FLAGS)


def _audit(
    warning_edges: list[MissingImportEdge],
    allowed_edges: list[MissingImportEdge],
    active_excludes: set[str],
) -> list[str]:
    warning_set = set(warning_edges)
    allowed_set = set(allowed_edges)
    failures: list[str] = []

    for edge in sorted(warning_set):
        if edge.kind == "excluded":
            if edge.module not in active_excludes:
                failures.append(
                    "excluded module is not in active Analysis.excludes: "
                    f"{edge.render()}"
                )
            continue
        if _is_actionable(edge):
            failures.append(f"actionable import edge: {edge.render()}")
        if edge not in allowed_set:
            failures.append(f"unreviewed missing-import edge: {edge.render()}")
    missing_warning_set = {
        edge for edge in warning_set if edge.kind == "missing"
    }
    for edge in sorted(allowed_set - missing_warning_set):
        failures.append(f"stale allowlist edge: {edge.render()}")
    return failures


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warnings", required=True, type=Path)
    parser.add_argument("--allowlist", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--pyinstaller-version", required=True)
    parser.add_argument("--spec", required=True, type=Path)
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
        active_excludes = _active_analysis_excludes(args.spec, args.platform)
    except AuditError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    failures = _audit(warning_edges, allowed_edges, active_excludes)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    missing_count = sum(edge.kind == "missing" for edge in warning_edges)
    excluded_count = len(warning_edges) - missing_count
    print(
        "PASS: audited "
        f"{missing_count} reviewed missing-import edges and "
        f"{excluded_count} spec-excluded "
        f"{'edge' if excluded_count == 1 else 'edges'} "
        f"(platform={args.platform}, PyInstaller={args.pyinstaller_version})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
