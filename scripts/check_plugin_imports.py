"""Structural checks for enforcement plugin imports.

Verifies:
1. All .opencode/plugin/*.ts use @opencode-ai/plugin not @opencode/plugin
2. No .ts file has top-level static import of "child_process"
3. All .ts files use "node:fs" not bare "fs" for fs imports
4. enforce-stop.ts has isSubagentFinalReport declared before use
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"
ALL_TS_DIRS = [d for d in (PLUGIN_DIR, PLUGINS_DIR) if d.exists()]

_BAD_IMPORT = re.compile(r'from "?@opencode/plugin"?')
_BARE_FS = re.compile(r"""from ["']fs["']|require\(["']fs["']\)""")
_CHILD_PROCESS_TOP = re.compile(r'import\s+.*\bchild_process\b')
_CHILD_REQUIRE = re.compile(r'require\(["\']child_process["\']\)')
_CONST = re.compile(r"\bconst\s+isSubagentFinalReport\b")
_USE = re.compile(r"\bisSubagentFinalReport\b")


def _check_bad_import(source: str, path: Path) -> list[str]:
    errors = []
    for i, line in enumerate(source.splitlines(), 1):
        if _BAD_IMPORT.search(line):
            errors.append(
                f"{path}:{i}: uses @opencode/plugin — must be @opencode-ai/plugin:\n  {line.strip()}"
            )
    return errors


def _check_bare_fs(source: str, path: Path) -> list[str]:
    errors = []
    for i, line in enumerate(source.splitlines(), 1):
        if _BARE_FS.search(line) and "node:fs" not in line:
            errors.append(
                f"{path}:{i}: bare fs import — must be node:fs:\n  {line.strip()}"
            )
    return errors


def _check_child_process(source: str, path: Path) -> list[str]:
    errors = []
    for i, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("@"):
            continue
        if stripped.startswith("import type"):
            continue
        if _CHILD_PROCESS_TOP.search(line) or _CHILD_REQUIRE.search(line):
            if "node:child_process" in line:
                continue
            errors.append(
                f"{path}:{i}: static child_process import at top level:\n  {line.strip()}"
            )
    return errors


def _check_use_before_decl(source: str, path: Path) -> list[str]:
    errors = []
    lines = source.splitlines()
    first_use = None
    first_decl = None
    for i, line in enumerate(lines, 1):
        if _CONST.search(line) and first_decl is None:
            first_decl = i
        if _USE.search(line) and first_use is None:
            first_use = i
    if first_use is not None and first_decl is not None and first_use < first_decl:
        errors.append(
            f"{path}:{first_use}: isSubagentFinalReport USED before declaration at line {first_decl}"
        )
    return errors


def main(paths: list[str] | None = None) -> int:
    if paths:
        all_ts = [Path(path) for path in paths]
    else:
        all_ts = []
        for directory in ALL_TS_DIRS:
            all_ts.extend(sorted(directory.glob("*.ts")))

    errors: list[str] = []

    for ts_file in all_ts:
        source = ts_file.read_text()
        errors.extend(_check_bad_import(source, ts_file))
        errors.extend(_check_bare_fs(source, ts_file))
        errors.extend(_check_child_process(source, ts_file))
        if ts_file.name == "enforce-stop.ts":
            errors.extend(_check_use_before_decl(source, ts_file))

    if errors:
        for e in errors:
            print(e)
        print(f"\n{len(errors)} plugin import violations found")
        return 1

    print("All plugin .ts files: imports OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
