#!/usr/bin/env python3
"""
check_dead_code.py

Mechanical dead-code detection: finds top-level classes/functions defined in
src/general_ludd/ that are never referenced in any production (src/) file.
Classes/functions referenced ONLY in test files are flagged as dead.

Usage:
    python3 scripts/check_dead_code.py [--json] [--quiet]

Exit codes:
    0   No dead code found.
    1   Dead code found.
    2   Usage / internal error.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src" / "general_ludd"
SRC_ROOT = PROJECT_ROOT / "src"
TESTS_ROOT = PROJECT_ROOT / "tests"
BASELINE_FILE = PROJECT_ROOT / "config" / "dead_code_baseline.txt"

SKIP_NAMES: frozenset[str] = frozenset({"__init__", "__main__", "main"})

IGNORE_FILES: frozenset[str] = frozenset({"__init__.py"})


def _load_baseline(path: Path | None) -> set[str]:
    """Load baseline allowlist. Each line: 'file:name' (relative path)."""
    if path is None:
        return set()
    if not path.is_file():
        return set()
    entries: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.add(line)
    return entries


def _baseline_key(symbol: Symbol) -> str:
    """Generate the baseline key for a symbol: 'file:name'."""
    return f"{symbol.file}:{symbol.name}"


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # "class" or "function"
    file: str
    line: int
    module: str


@dataclass
class DeadSymbol:
    symbol: Symbol
    referenced_in: list[str] = field(default_factory=list)

    @property
    def is_orphan(self) -> bool:
        return not self.referenced_in


@dataclass
class ScanResult:
    symbols: list[Symbol] = field(default_factory=list)
    dead: list[DeadSymbol] = field(default_factory=list)
    files_scanned: int = 0


def _module_path(file_path: Path, src_root: Path) -> str:
    rel = file_path.relative_to(src_root)
    parts = list(rel.parts)
    parts[-1] = parts[-1].replace(".py", "")
    return ".".join(parts)


def _collect_files(root: Path) -> list[Path]:
    return sorted(f for f in root.rglob("*.py") if f.name not in IGNORE_FILES)


def _extract_symbols(file_path: Path, repo_root: Path) -> list[Symbol]:
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    src_root = repo_root / "src"
    module = _module_path(file_path, src_root)
    rel = str(file_path.relative_to(repo_root))
    symbols: list[Symbol] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            symbols.append(Symbol(name=node.name, kind="class", file=rel, line=node.lineno, module=module))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_") or node.name in SKIP_NAMES:
                continue
            symbols.append(Symbol(name=node.name, kind="function", file=rel, line=node.lineno, module=module))

    return symbols


def _referenced_names(file_path: Path, name_set: set[str]) -> set[str]:
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in name_set:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in name_set:
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            root_name = node.name.rsplit(".", 1)[-1]
            if root_name in name_set:
                found.add(root_name)
            if node.asname in name_set:
                found.add(node.asname)
    return found


def _build_ref_map(symbols: list[Symbol], files: list[Path], repo_root: Path) -> dict[str, set[str]]:
    """For each symbol name, find which files reference it.

    Parses each file once and indexes Python identifiers instead of running a
    repo-wide regex with every symbol name alternated into it.
    """
    name_set = {s.name for s in symbols}
    refs: dict[str, set[str]] = {n: set() for n in name_set}

    if not name_set:
        return refs

    for f in files:
        rel = str(f.relative_to(repo_root))
        for name in _referenced_names(f, name_set):
            refs[name].add(rel)

    return refs


def run(repo_root: Path | None = None) -> ScanResult:
    root = repo_root or PROJECT_ROOT
    src_dir = root / "src" / "general_ludd"
    if not src_dir.is_dir():
        print(f"ERROR: src directory not found: {src_dir}", file=sys.stderr)
        sys.exit(2)

    py_files = _collect_files(src_dir)
    symbols: list[Symbol] = []
    for f in py_files:
        symbols.extend(_extract_symbols(f, root))

    all_src_files = sorted(f for f in (root / "src").rglob("*.py"))
    all_test_files = sorted(f for f in (root / "tests").rglob("*.py"))

    src_refs = _build_ref_map(symbols, all_src_files, root)
    test_refs = _build_ref_map(symbols, all_test_files, root)

    dead: list[DeadSymbol] = []
    for sym in symbols:
        srefs = src_refs.get(sym.name, set())
        trefs = test_refs.get(sym.name, set())
        if not srefs:
            dead.append(
                DeadSymbol(
                    symbol=sym,
                    referenced_in=sorted(trefs),
                )
            )

    return ScanResult(symbols=symbols, dead=dead, files_scanned=len(py_files))


def format_text(result: ScanResult) -> str:
    if not result.dead:
        return f"dead-code: 0 dead symbol(s) across {result.files_scanned} file(s)\n"

    lines: list[str] = []
    lines.append(f"dead-code: {len(result.dead)} dead symbol(s) found\n")

    test_only = [d for d in result.dead if d.referenced_in]
    orphans = [d for d in result.dead if not d.referenced_in]

    if test_only:
        lines.append(f"  Test-only ({len(test_only)}):")
        for d in test_only:
            s = d.symbol
            refs = ", ".join(d.referenced_in[:3])
            if len(d.referenced_in) > 3:
                refs += f" (+{len(d.referenced_in) - 3} more)"
            lines.append(f"    {s.file}:{s.line}  {s.kind} {s.name}  -> {refs}")

    if orphans:
        lines.append(f"  Orphans ({len(orphans)}):")
        for d in orphans:
            s = d.symbol
            lines.append(f"    {s.file}:{s.line}  {s.kind} {s.name}  -> no references found")

    lines.append("")
    lines.append(f"Scanned {result.files_scanned} file(s), {len(result.symbols)} symbol(s).")
    return "\n".join(lines) + "\n"


def format_json(result: ScanResult) -> str:
    payload = {
        "files_scanned": result.files_scanned,
        "symbols_total": len(result.symbols),
        "dead_count": len(result.dead),
        "dead": [
            {
                "file": d.symbol.file,
                "line": d.symbol.line,
                "name": d.symbol.name,
                "kind": d.symbol.kind,
                "module": d.symbol.module,
                "orphan": d.is_orphan,
                "test_only": bool(d.referenced_in),
                "referenced_in": d.referenced_in,
            }
            for d in result.dead
        ],
    }
    return json.dumps(payload, indent=2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="check_dead_code")
    parser.add_argument("--json", action="store_true", help="Output as JSON.")
    parser.add_argument("--quiet", action="store_true", help="Print summary only.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Project root directory (default: script parent).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Baseline allowlist file (default: config/dead_code_baseline.txt). "
        "Symbols in this file are excluded from the dead-code count.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Write current dead symbols to the baseline file and exit 0.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = args.repo_root or PROJECT_ROOT
    result = run(repo_root=repo_root)

    baseline_path = args.baseline or (repo_root / "config" / "dead_code_baseline.txt")

    if args.update_baseline:
        keys = sorted(_baseline_key(d.symbol) for d in result.dead)
        header = (
            "# Dead-code baseline — symbols referenced only in tests or nowhere.\n"
            "# Regenerate with: make dead-code-baseline\n"
            "# Format: <relative-file-path>:<symbol-name>\n"
        )
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(header + "\n".join(keys) + "\n", encoding="utf-8")
        print(f"Wrote {len(keys)} entries to {baseline_path}")
        return 0

    baseline = _load_baseline(baseline_path)
    new_dead = [d for d in result.dead if _baseline_key(d.symbol) not in baseline]
    baselined = len(result.dead) - len(new_dead)

    if args.json:
        payload = {
            "files_scanned": result.files_scanned,
            "symbols_total": len(result.symbols),
            "dead_count": len(result.dead),
            "baselined": baselined,
            "new_dead_count": len(new_dead),
            "dead": [
                {
                    "file": d.symbol.file,
                    "line": d.symbol.line,
                    "name": d.symbol.name,
                    "kind": d.symbol.kind,
                    "module": d.symbol.module,
                    "orphan": d.is_orphan,
                    "test_only": bool(d.referenced_in),
                    "referenced_in": d.referenced_in,
                    "in_baseline": _baseline_key(d.symbol) in baseline,
                }
                for d in result.dead
            ],
        }
        print(json.dumps(payload, indent=2))
    elif args.quiet:
        if new_dead:
            print(
                f"dead-code: {len(new_dead)} NEW dead symbol(s) "
                f"({baselined} baselined) across {result.files_scanned} file(s)"
            )
        else:
            print(f"dead-code: 0 new dead symbol(s) ({baselined} baselined) across {result.files_scanned} file(s)")
    else:
        if new_dead:
            print(format_text(ScanResult(symbols=result.symbols, dead=new_dead, files_scanned=result.files_scanned)))
            print(f"({baselined} additional symbol(s) are in the baseline)")
        else:
            print(f"dead-code: 0 new dead symbol(s) ({baselined} baselined) across {result.files_scanned} file(s)\n")

    return 0 if not new_dead else 1


if __name__ == "__main__":
    sys.exit(main())
