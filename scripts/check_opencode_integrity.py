#!/usr/bin/env python3
"""Check .opencode/ integrity — validates the config survives an opencode restart.

Verifies:
1. .opencode/ exists with required subdirectories (plugin/, plugins/, skill/, skills/, agent/)
2. Every .ts file in plugin/ and plugins/ passes node --check (valid syntax)
3. Every .ts file has a default export (required for plugin loading)
4. shared.ts exports match what plugins import
5. opencode.json at root is valid JSON with correct permission ordering

Usage:
    python3 scripts/check_opencode_integrity.py [--root /path/to/repo]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REQUIRED_SUBDIRS = ("plugin", "plugins", "skill", "skills", "agent")

EXPORT_RE = re.compile(
    r"^\s*export\s+(?:const|let|var|function|class|interface|type|enum|abstract|async\s+function|"
    r"default\s+(?:const|let|var|function|class|interface|type|enum|abstract|async\s+function))?"
    r"\s*(\w+)",
    re.MULTILINE,
)
EXPORT_DEFAULT_RE = re.compile(
    r"^\s*export\s+default\s+",
    re.MULTILINE,
)
IMPORT_FROM_SHARED_RE = re.compile(
    r"""from\s+["']\./shared(?:\.ts)?["']""",
)
IMPORT_RE = re.compile(
    r"""import\s*\{([^}]+)\}\s*from\s+["']\./shared(?:\.ts)?["']""",
    re.MULTILINE,
)


def _extract_exports(content: str) -> set[str]:
    """Extract named + default exports from a TypeScript source string."""
    names: set[str] = set()
    for m in EXPORT_RE.finditer(content):
        name = m.group(1)
        if name and not name.startswith("_"):
            names.add(name)
    if EXPORT_DEFAULT_RE.search(content):
        names.add("default")
    return names


def _clean_import_name(name: str) -> str:
    """Strip 'type ' prefix from import names (TypeScript type-only imports)."""
    if name.startswith("type "):
        return name[len("type "):]
    return name


def _extract_imports(content: str) -> set[str]:
    """Extract named imports from './shared' in a TypeScript source string."""
    names: set[str] = set()
    # Single-line: import { foo, bar } from "./shared"
    for m in IMPORT_RE.finditer(content):
        for name in m.group(1).split(","):
            n = name.strip()
            if " as " in n:
                n = n.split(" as ")[0].strip()
            if n:
                names.add(_clean_import_name(n))
    # Multi-line: import { ... } from "./shared"
    multi = re.finditer(
        r"""import\s*\{([^}]+)\}\s*from\s+["']\./shared(?:\.ts)?["']""",
        content,
        re.DOTALL,
    )
    for m in multi:
        body = m.group(1).strip()
        if "\n" in body:
            for part in body.split(","):
                n = part.strip()
                if " as " in n:
                    n = n.split(" as ")[0].strip()
                if n:
                    names.add(_clean_import_name(n))
    return names


# ── Check 1: required subdirectories ─────────────────────────────────────

def check_subdirs(root: Path) -> list[str]:
    errors: list[str] = []
    opencode_dir = root / ".opencode"
    if not opencode_dir.is_dir():
        errors.append(f"MISSING: .opencode/ directory not found at {opencode_dir}")
        return errors
    for sub in REQUIRED_SUBDIRS:
        p = opencode_dir / sub
        if not p.is_dir():
            errors.append(f"MISSING: .opencode/{sub}/ directory not found")
    return errors


# ── Check 2: JS syntax via node --check ──────────────────────────────────

def check_syntax(root: Path) -> list[str]:
    errors: list[str] = []
    for sub in ("plugin", "plugins"):
        d = root / ".opencode" / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.ts")):
            result = subprocess.run(
                ["node", "--check", str(f)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()[:400]
                errors.append(f"SYNTAX ERROR: {f.relative_to(root)} - {stderr}")
    return errors


# ── Check 3: default exports on all .ts files ────────────────────────────

def check_default_exports(root: Path) -> list[str]:
    errors: list[str] = []
    for sub in ("plugin", "plugins"):
        d = root / ".opencode" / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.ts")):
            if f.name in ("shared.ts", "hot_reload.ts"):
                continue
            content = f.read_text(encoding="utf-8")
            if not EXPORT_DEFAULT_RE.search(content):
                errors.append(
                    f"NO DEFAULT EXPORT: {f.relative_to(root)} has no default export"
                )
    return errors


# ── Check 4: shared.ts exports match what plugins import ─────────────────

def _find_shared(root: Path) -> Path | None:
    """Locate shared.ts.

    It moved from .opencode/plugin/ to .opencode/lib/ in the E.5 refactor. Check
    the current location first, then the legacy one, so this check keeps running
    instead of silently no-opping when the file moves.
    """
    for rel in (("lib", "shared.ts"), ("plugin", "shared.ts")):
        candidate = root / ".opencode" / rel[0] / rel[1]
        if candidate.is_file():
            return candidate
    return None


def check_shared_alignment(root: Path) -> list[str]:
    errors: list[str] = []
    shared = _find_shared(root)
    if shared is None:
        return []  # can't check without shared.ts

    shared_content = shared.read_text(encoding="utf-8")
    exported = _extract_exports(shared_content)

    for sub in ("plugin", "plugins"):
        d = root / ".opencode" / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.ts")):
            if f.name == "shared.ts":
                continue
            content = f.read_text(encoding="utf-8")
            imported = _extract_imports(content)
            missing = imported - exported
            if missing and IMPORT_FROM_SHARED_RE.search(content):
                errors.append(
                    f"SHARED MISMATCH: {f.relative_to(root)} imports from shared.ts "
                    f"but these are not exported: {sorted(missing)}"
                )
    return errors


# ── Check 5: opencode.json validity + permission ordering ────────────────

def check_opencode_json(root: Path) -> list[str]:
    errors: list[str] = []
    config_path = root / "opencode.json"
    if not config_path.is_file():
        errors.append(f"MISSING: opencode.json not found at {config_path}")
        return errors

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"INVALID JSON: opencode.json - {e}")
        return errors

    perm = config.get("permission")
    if not isinstance(perm, dict):
        errors.append("BAD CONFIG: opencode.json 'permission' field missing or not a dict")
        return errors

    bash_perm = perm.get("bash")
    if isinstance(bash_perm, dict):
        keys = list(bash_perm.keys())
        if len(keys) < 2:
            errors.append("BAD CONFIG: permission.bash should have at least 2 entries (* and make *)")
            return errors
        first_key = keys[0]
        second_key = keys[1]
        if first_key != "*" or bash_perm.get(first_key) != "deny":
            errors.append(
                f"BAD ORDER: permission.bash first entry must be '*' -> 'deny', "
                f"got {first_key!r} -> {bash_perm.get(first_key)!r}"
            )
        if second_key != "make *" or bash_perm.get(second_key) != "allow":
            errors.append(
                f"BAD ORDER: permission.bash second entry must be 'make *' -> 'allow', "
                f"got {second_key!r} -> {bash_perm.get(second_key)!r}"
            )
    elif isinstance(bash_perm, list):
        deny_indices = [
            index for index, rule in enumerate(bash_perm)
            if isinstance(rule, dict) and rule.get("path") == "*" and rule.get("allow") is False
        ]
        allow_indices = [
            index for index, rule in enumerate(bash_perm)
            if isinstance(rule, dict) and rule.get("command") == "make *" and rule.get("allow") is True
        ]
        if not deny_indices or not allow_indices:
            errors.append("BAD CONFIG: permission.bash requires wildcard deny and make wildcard allow rules")
        elif min(deny_indices) > min(allow_indices):
            errors.append("BAD ORDER: permission.bash wildcard deny must precede make wildcard allow")
    else:
        errors.append("BAD CONFIG: opencode.json permission.bash must be an object or ordered rule array")
        return errors

    doom = perm.get("doom_loop")
    if doom != "deny":
        errors.append(
            f"BAD CONFIG: permission.doom_loop should be 'deny', got {doom!r}"
        )

    plugins = config.get("plugin")
    if not isinstance(plugins, list) or len(plugins) == 0:
        errors.append("BAD CONFIG: opencode.json 'plugin' field missing or empty")

    return errors


# ── Main ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description="Check .opencode/ integrity")
    parser.add_argument("--root", default=None, help="Repo root (default: auto-detect)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent

    all_errors: list[str] = []

    all_errors.extend(check_subdirs(root))
    all_errors.extend(check_syntax(root))
    all_errors.extend(check_default_exports(root))
    all_errors.extend(check_shared_alignment(root))
    all_errors.extend(check_opencode_json(root))

    if all_errors:
        print(f"OPENDCODE INTEGRITY: FAILED ({len(all_errors)} errors)")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print("OPENDCODE INTEGRITY: ALL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
