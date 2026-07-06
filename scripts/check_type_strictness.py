#!/usr/bin/env python3
"""
check_type_strictness.py

Gate script: scans Python source for `Any` usage in type annotations and
reports each occurrence. `Any` defeats the purpose of static typing and is
the single most common type-safety regression. See
.opencode/skills/type-safety/SKILL.md for the full policy.

Usage:
    python3 scripts/check_type_strictness.py [PATHS...] [--baseline FILE]
                                              [--format text|json] [--quiet]

Defaults to scanning src/ when no paths are given.

Exit codes:
    0   No violations (or, with --baseline, no NEW violations).
    1   Violations found (or new violations beyond the baseline).
    2   Usage error / internal failure.

The scanner uses the `ast` module — no execution of the target code. It
detects `Any` in:
    - function return annotations       (`def f() -> Any: ...`)
    - parameter annotations             (`def f(x: Any) -> ...`)
    - annotated assignments             (`x: dict[str, Any] = ...`)
    - nested container types            (`dict[str, Any]`, `list[Any]`)
    - Optional / Union forms            (`Optional[Any]`, `Union[int, Any]`)
    - attribute form                    (`typing.Any`)
    - stringified forward references    (`x: "dict[str, Any]"`)
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Violation:
    """A single `Any` occurrence in a type annotation."""

    file: str
    line: int
    col: int
    context: str
    kind: str  # return | param | annassign

    @property
    def baseline_key(self) -> str:
        """Stable key for baseline matching (file:line)."""
        return f"{self.file}:{self.line}"

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}:{self.col}"


@dataclass
class ScanResult:
    """Aggregate scan output."""

    violations: list[Violation] = field(default_factory=list)
    files_scanned: int = 0

    @property
    def count(self) -> int:
        return len(self.violations)


# --------------------------------------------------------------------------- #
# Annotation introspection
# --------------------------------------------------------------------------- #

_ANY_NAMES = frozenset({"Any"})


def _name_is_any(node: ast.AST) -> bool:
    """True for a bare `Any` or the attribute form `typing.Any`."""
    if isinstance(node, ast.Name) and node.id in _ANY_NAMES:
        return True
    if isinstance(node, ast.Attribute) and node.attr in _ANY_NAMES:
        return True
    return False


def _scan_string_annotation(value: str, parent_line: int, parent_col: int) -> list[tuple[int, int]]:
    """Parse a forward-ref string and look for Any inside it."""
    hits: list[tuple[int, int]] = []
    try:
        parsed = ast.parse(value, mode="eval")
    except SyntaxError:
        if "Any" in value:
            hits.append((parent_line, parent_col))
        return hits
    for child in ast.walk(parsed.body):
        if _name_is_any(child):
            hits.append((parent_line, parent_col))
            break
    return hits


def _walk_annotation(node: ast.AST | None) -> list[tuple[int, int]]:
    """
    Return every (line, col) where `Any` appears inside an annotation.

    Handles nested subscripts (dict[str, Any]), tuples (Union[int, Any]),
    attribute forms (typing.Any), and stringified annotations ("dict[str, Any]").
    """
    hits: list[tuple[int, int]] = []
    if node is None:
        return hits

    for child in ast.walk(node):
        if _name_is_any(child):
            line = getattr(child, "lineno", 1)
            col = getattr(child, "col_offset", 0)
            hits.append((line, col))
        elif (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and "Any" in child.value
        ):
            hits.extend(_scan_string_annotation(child.value, child.lineno, child.col_offset))
    return hits


def _iter_args(args: ast.arguments) -> list[ast.arg]:
    """All argument slots on a function that may carry annotations."""
    out: list[ast.arg] = []
    out.extend(getattr(args, "posonlyargs", []) or [])
    out.extend(args.args)
    if args.vararg is not None:
        out.append(args.vararg)
    out.extend(args.kwonlyargs)
    if args.kwarg is not None:
        out.append(args.kwarg)
    return out


# --------------------------------------------------------------------------- #
# File scanning
# --------------------------------------------------------------------------- #


def _scan_source(source: str, rel_path: str) -> list[Violation]:
    """Scan Python source text for Any-in-annotation violations."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()

    def ctx(line: int) -> str:
        return lines[line - 1].strip() if 1 <= line <= len(lines) else ""

    def record(line: int, col: int, kind: str) -> None:
        out.append(
            Violation(
                file=rel_path,
                line=line,
                col=col,
                context=ctx(line),
                kind=kind,
            )
        )

    out: list[Violation] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ln, col in _walk_annotation(node.returns):
                record(ln, col, "return")
            for arg in _iter_args(node.args):
                for ln, col in _walk_annotation(arg.annotation):
                    record(ln, col, "param")
        elif isinstance(node, ast.AnnAssign):
            for ln, col in _walk_annotation(node.annotation):
                record(ln, col, "annassign")

    out.sort(key=lambda v: (v.file, v.line, v.col))
    return out


def _collect_files(paths: list[Path]) -> list[Path]:
    """Resolve the list of .py files to scan, de-duplicated, sorted."""
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(p.rglob("*.py"))
        elif p.is_file() and p.suffix == ".py":
            files.append(p)
        # silently ignore non-py files and missing paths

    seen: set[Path] = set()
    unique: list[Path] = []
    for f in sorted(files):
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(f)
    return unique


def _relative_to_roots(path: Path, roots: list[Path]) -> str:
    """Display path relative to the closest given root, else absolute."""
    resolved = path.resolve()
    for r in roots:
        try:
            return str(resolved.relative_to(r.resolve()))
        except ValueError:
            continue
    return str(path)


def scan(paths: list[Path]) -> ScanResult:
    """Scan the given paths (files and/or directories) for violations."""
    roots = [p for p in paths if p.is_dir()]
    if not roots:
        roots = [Path.cwd()]
    files = _collect_files(paths)
    result = ScanResult(files_scanned=len(files))
    for f in files:
        rel = _relative_to_roots(f, roots)
        try:
            source = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        result.violations.extend(_scan_source(source, rel))
    return result


# --------------------------------------------------------------------------- #
# Baseline
# --------------------------------------------------------------------------- #


def load_baseline(path: Path | None) -> set[str]:
    """
    Load a baseline file: one `file:line` per line, comments with `#`.

    Returns an empty set if path is None or does not exist.
    """
    if path is None:
        return set()
    if not path.exists():
        return set()
    keys: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keys.add(line)
    return keys


def filter_new(violations: list[Violation], baseline: set[str]) -> list[Violation]:
    """Return only the violations whose `file:line` is NOT in the baseline."""
    return [v for v in violations if v.baseline_key not in baseline]


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def format_text(result: ScanResult, new_only: ScanResult | None = None) -> str:
    """Human-readable per-violation report."""
    lines: list[str] = []
    target = new_only if new_only is not None else result
    for v in target.violations:
        lines.append(
            f"{v.location}: kind={v.kind:<10}  {v.context}"
        )
    if new_only is not None:
        lines.append("")
        lines.append(
            f"type-strictness: {new_only.count} new violation(s) "
            f"({result.count} total, {result.count - new_only.count} baselined) "
            f"across {result.files_scanned} file(s)."
        )
    else:
        lines.append("")
        lines.append(
            f"type-strictness: {result.count} violation(s) "
            f"across {result.files_scanned} file(s)."
        )
    return "\n".join(lines)


def format_json(result: ScanResult, new_only: ScanResult | None = None) -> str:
    target = new_only if new_only is not None else result
    payload = {
        "total": result.count,
        "new": new_only.count if new_only is not None else result.count,
        "baselined": (result.count - new_only.count) if new_only is not None else 0,
        "files_scanned": result.files_scanned,
        "violations": [
            {
                "file": v.file,
                "line": v.line,
                "col": v.col,
                "kind": v.kind,
                "context": v.context,
            }
            for v in target.violations
        ],
    }
    return json.dumps(payload, indent=2)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_type_strictness",
        description="Flag `Any` usage in Python type annotations.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files/dirs to scan (default: src/).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Baseline file of tolerated `file:line` violations (enforces on new code).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-violation detail; print summary only.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    paths = args.paths or [Path("src")]
    # Resolve against cwd so missing paths are reported clearly.
    resolved = [p for p in paths]
    missing = [p for p in resolved if not p.exists()]
    if missing:
        for m in missing:
            print(f"ERROR: path does not exist: {m}", file=sys.stderr)
        return 2

    result = scan(resolved)
    baseline = load_baseline(args.baseline)

    if baseline:
        new_violations = filter_new(result.violations, baseline)
        new_result = ScanResult(violations=new_violations, files_scanned=result.files_scanned)
    else:
        new_result = None

    deciding_count = new_result.count if new_result is not None else result.count

    if args.format == "json":
        print(format_json(result, new_result))
    else:
        if args.quiet:
            if new_result is not None:
                print(
                    f"type-strictness: {new_result.count} new violation(s) "
                    f"({result.count} total, {result.count - new_result.count} baselined) "
                    f"across {result.files_scanned} file(s)."
                )
            else:
                print(
                    f"type-strictness: {result.count} violation(s) "
                    f"across {result.files_scanned} file(s)."
                )
        else:
            print(format_text(result, new_result))

    return 0 if deciding_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
