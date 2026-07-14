#!/usr/bin/env python3
"""Comprehensive opencode readiness checker — prevents startup failures.

Checks ALL of:
1. Every plugin registered in opencode.json exists on disk
2. Every .ts file in .opencode/plugin/ has valid JS syntax (node --check)
3. Every registered plugin has a valid default export
4. No plugin imports a relative file that doesn't exist
5. plugin-hashes.json matches actual files on disk
6. opencode.json has valid JSON syntax

Usage:
    python3 scripts/check_opencode_ready.py
    python3 scripts/check_opencode_ready.py --base .opencode.orig

    When --base is given, ALL file-existence and hash checks use that base
    dir.  opencode.json is always read from repo root; only plugin-file and
    manifest paths are remapped.

Env vars:
    GLUDD_PLUGIN_DIR — override plugin base directory (default: .opencode/)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = ROOT / ".opencode"

RELATIVE_IMPORT_RE = re.compile(
    r"""from\s+["'](\./|\.\./)([^"']+)["']""",
    re.MULTILINE,
)
EXPORT_DEFAULT_RE = re.compile(r"^\s*export\s+default\s+", re.MULTILINE)


def _resolve_relative(base_dir: Path, prefix: str, spec: str) -> Path:
    parts = prefix.rstrip("/").split("/")
    resolved = base_dir
    for part in parts:
        if part == "..":
            resolved = resolved.parent
        elif part == ".":
            pass
    return (resolved / spec).resolve()


# ── Check 6: opencode.json valid JSON + extract plugin names ─────────────

def check_opencode_json(base_dir: Path) -> tuple[list[str], list[str]]:
    """Parse opencode.json from repo root.

    Returns ([absolute_paths_under_base], [errors]).
    When base_dir != DEFAULT_BASE, paths are remapped to the alternate base.
    """
    config_path = ROOT / "opencode.json"
    if not config_path.exists():
        return [], [f"opencode.json not found at {config_path}"]
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [], [f"opencode.json is invalid JSON: {e}"]
    plugins_raw = config.get("plugin", [])
    if not isinstance(plugins_raw, list):
        return [], ["opencode.json 'plugin' field is not a list"]

    plugin_files = []
    for p in plugins_raw:
        if not isinstance(p, str):
            continue
        # "./.opencode/plugin/foo.ts" → ".opencode/plugin/foo.ts"
        rel = p[2:] if p.startswith("./") else p
        # If using alternate base, swap the prefix
        if base_dir != DEFAULT_BASE:
            default_prefix = ".opencode/"
            if rel.startswith(default_prefix):
                rel = str(base_dir.relative_to(ROOT)) + "/" + rel[len(default_prefix):]
        resolved = (ROOT / rel).resolve()
        plugin_files.append(str(resolved))
    return plugin_files, []


# ── Check 1: Registered plugins exist on disk ───────────────────────────

def check_plugins_exist(plugin_files: list[str]) -> list[str]:
    errors = []
    for pf in plugin_files:
        if not Path(pf).exists():
            errors.append(f"MISSING: {Path(pf).name} (registered in opencode.json, not on disk)")
    return errors


# ── Check 2: JS syntax via node --check ─────────────────────────────────

def check_ts_syntax(plugin_dir: Path, plugins_dir: Path) -> list[str]:
    errors = []
    for d in (plugin_dir, plugins_dir):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.ts")):
            result = subprocess.run(
                ["node", "--check", str(f)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()[:400]
                errors.append(f"SYNTAX ERROR: {f.name} — {stderr}")
    return errors


# ── Check 3: Default exports on registered plugins ──────────────────────

def check_default_exports(
    plugin_dir: Path,
    plugins_dir: Path,
    registered_names: set[str],
) -> list[str]:
    errors = []
    for d in (plugin_dir, plugins_dir):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.ts")):
            if f.name not in registered_names:
                continue
            content = f.read_text(encoding="utf-8")
            if not EXPORT_DEFAULT_RE.search(content):
                errors.append(f"NO EXPORT DEFAULT: {f.name} (registered plugin, no default export)")
    return errors


# ── Check 4: Relative imports resolve to existing files ─────────────────

def check_imports_resolve(plugin_dir: Path, plugins_dir: Path) -> list[str]:
    errors = []
    for d in (plugin_dir, plugins_dir):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.ts")):
            content = f.read_text(encoding="utf-8")
            for match in RELATIVE_IMPORT_RE.finditer(content):
                prefix = match.group(1)
                spec = match.group(2)
                resolved = _resolve_relative(f.parent, prefix, spec)
                if not resolved.exists():
                    errors.append(
                        f"IMPORT NOT FOUND: {f.name} imports '{prefix}{spec}' "
                        f"— {resolved.name} does not exist"
                    )
    return errors


# ── Check 5: plugin-hashes.json matches actual files ────────────────────

def compute_hashes(plugin_dir: Path, plugins_dir: Path) -> dict[str, str]:
    current: dict[str, str] = {}
    for d, prefix in ((plugin_dir, ""), (plugins_dir, "plugins/")):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.ts")):
            try:
                key = f"{prefix}{f.name}"
                current[key] = hashlib.sha256(f.read_bytes()).hexdigest()
            except OSError:
                continue
    return current


def check_hashes(plugin_dir: Path, plugins_dir: Path, base_dir: Path) -> list[str]:
    errors = []
    hashes_path = base_dir / "plugin-hashes.json"
    current = compute_hashes(plugin_dir, plugins_dir)

    if not current:
        return []

    if not hashes_path.exists():
        errors.append(f"HASHES MISSING: plugin-hashes.json not found at {hashes_path}")
        return errors

    try:
        stored = json.loads(hashes_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"HASHES INVALID JSON: {e}")
        return errors

    if not isinstance(stored, dict):
        errors.append(f"HASHES INVALID: content is not a dict")
        return errors

    new_files = sorted(set(current) - set(stored))
    removed_files = sorted(set(stored) - set(current))
    changed_files = sorted(
        f for f in (set(current) & set(stored)) if current[f] != stored[f]
    )

    for nf in new_files:
        errors.append(f"HASHES UNTRACKED: {nf} on disk but not in manifest")
    for rf in removed_files:
        errors.append(f"HASHES STALE: {rf} in manifest but not on disk")
    for cf in changed_files:
        errors.append(f"HASHES MISMATCH: {cf} content differs from manifest")

    return errors


# ── Bonus: non-fatal warnings about known issues ────────────────────────

def bonus_checks(plugin_dir: Path) -> list[str]:
    warnings: list[str] = []

    stop_file = plugin_dir / "enforce-stop.ts"
    if stop_file.exists():
        content = stop_file.read_text(encoding="utf-8")
        imports_shared = (
            "from \"./shared.ts\"" in content
            or "from './shared.ts'" in content
        )
        has_inline = "_isSubagent" in content and "_reportAlive" in content
        if not imports_shared and has_inline:
            warnings.append(
                "WARNING: enforce-stop.ts has inline _isSubagent/_reportAlive "
                "(pre-refactoring remnant — should import from shared.ts)"
            )

    dg_file = plugin_dir / "enforce-deletion-gate.ts"
    if dg_file.exists():
        content = dg_file.read_text(encoding="utf-8")
        if "@opencode/core" in content and "@opencode-ai/plugin" not in content:
            lines_with_core = [
                line.strip() for line in content.splitlines()
                if "@opencode/core" in line and not line.strip().startswith("//")
            ]
            if lines_with_core:
                warnings.append(
                    "WARNING: enforce-deletion-gate.ts imports from @opencode/core "
                    "(all other plugins use @opencode-ai/plugin)"
                )

    known = {
        "enforce-clean-tree.ts", "enforce-commit-lock.ts", "enforce-deadline.ts",
        "enforce-delegate.ts", "enforce-deletion-gate.ts", "enforce-enhancement-ratio.ts",
        "enforce-floor.ts", "enforce-make.ts", "enforce-multitask.ts",
        "enforce-no-suppressions.ts", "enforce-no-wait.ts", "enforce-session-start.ts",
        "enforce-stop.ts", "enforce-verified-claims.ts", "hot_reload.ts", "shared.ts",
    }
    for f in sorted(plugin_dir.glob("*.ts")):
        if f.name not in known:
            warnings.append(f"WARNING: unknown plugin file: {f.name} (not in known set)")

    return warnings


# ── Main ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description="Check opencode readiness")
    parser.add_argument(
        "--base",
        default=None,
        help=f"Plugin base directory (default: {DEFAULT_BASE})",
    )
    args = parser.parse_args(argv)

    base_dir = Path(args.base).resolve() if args.base else DEFAULT_BASE.resolve()
    plugin_dir = base_dir / "plugin"
    plugins_dir = base_dir / "plugins"

    all_errors: list[str] = []
    all_warnings: list[str] = []

    # Check 6: Valid opencode.json + extract registered plugins (paths remapped to base_dir)
    plugin_files, json_errors = check_opencode_json(base_dir)
    all_errors.extend(json_errors)

    registered_names = {Path(pf).name for pf in plugin_files}

    # Check 1: Registered plugins exist on disk (under base_dir)
    if plugin_files and not json_errors:
        all_errors.extend(check_plugins_exist(plugin_files))

    # Check 2: JS syntax
    all_errors.extend(check_ts_syntax(plugin_dir, plugins_dir))

    # Check 3: Default exports
    all_errors.extend(check_default_exports(plugin_dir, plugins_dir, registered_names))

    # Check 4: Imports resolve
    all_errors.extend(check_imports_resolve(plugin_dir, plugins_dir))

    # Check 5: Hash manifest
    all_errors.extend(check_hashes(plugin_dir, plugins_dir, base_dir))

    # Bonus warnings
    all_warnings.extend(bonus_checks(plugin_dir))

    # Report
    label = f"base={base_dir.relative_to(ROOT) if str(base_dir).startswith(str(ROOT)) else base_dir}"
    if all_errors:
        print(f"\nOPENDCODE READINESS CHECK: FAILED ({len(all_errors)} errors) [{label}]")
        for e in all_errors:
            print(f"  ERROR: {e}")
        if all_warnings:
            print(f"\n  ({len(all_warnings)} warnings)")
            for w in all_warnings:
                print(f"  ! {w}")
        return 1

    if all_warnings:
        print(f"OPENDCODE READINESS CHECK: PASSED ({len(all_warnings)} warnings) [{label}]")
        for w in all_warnings:
            print(f"  ! {w}")
        return 0

    print(f"OPENDCODE READINESS CHECK: ALL PASSED [{label}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
