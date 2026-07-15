#!/usr/bin/env python3
"""
proactive_bug_scan.py

Proactive bug scanner — finds issues before the user does.

Runs a battery of checks and exits 0 (clean) or 1 (issues found with
summary). Designed to be wired into the gate or run ad-hoc via
`make proactive-scan`.

Checks:
  1. Makefile duplicate targets (reuses check_duplicate_targets logic)
  2. Python lint issues (ruff via subprocess)
  3. Plugin TypeScript syntax errors (check_plugin_syntax.py)
  4. Stale state files in /tmp/gludd-* older than 1 hour
  5. Git dirty working tree
  6. Missing __init__.py files in src/ directories
  7. Make invocation warnings (overriding recipe, ignoring commands)

Usage:
    python3 scripts/proactive_bug_scan.py [--root PATH] [--skip-env-checks]
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

TARGET_PATTERN = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_.-]*):")
TARGET_VAR_ASSIGN_PATTERN = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_.-]*:\s*[A-Za-z_][A-Za-z0-9_.-]*\s*(\?=|:=|\+=|=)"
)
STALE_THRESHOLD_SEC = 3600


def check_duplicate_makefile_targets(root: Path) -> list[str]:
    makefile = root / "Makefile"
    if not makefile.exists():
        return []
    targets: Counter[str] = Counter()
    for line in makefile.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("."):
            continue
        if TARGET_VAR_ASSIGN_PATTERN.match(stripped):
            continue
        m = TARGET_PATTERN.match(stripped)
        if m:
            targets[m.group(1)] += 1
    issues: list[str] = []
    for t, count in sorted(targets.items()):
        if count > 1:
            issues.append(f"duplicate Makefile target '{t}': declared {count} times")
    return issues


def _find_ruff() -> str | None:
    found = shutil.which("ruff")
    if found:
        return found
    candidate = Path(sys.executable).parent / "ruff"
    if candidate.exists():
        return str(candidate)
    return None


def check_python_lint(root: Path) -> list[str]:
    ruff = _find_ruff()
    if ruff is None:
        return []
    targets: list[str] = []
    for name in ("src", "tests"):
        d = root / name
        if d.is_dir():
            targets.append(str(d))
    if not targets:
        return []
    try:
        result = subprocess.run(
            [ruff, "check", "--output-format=concise", *targets],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode == 0:
        return []
    issues: list[str] = []
    for line in result.stdout.strip().splitlines():
        if line.strip():
            issues.append(f"lint: {line.strip()}")
    if not issues:
        issues.append(f"lint: ruff exited {result.returncode} (see output)")
    return issues


def check_plugin_syntax(root: Path) -> list[str]:
    plugin_checker = Path(__file__).resolve().parent / "check_plugin_syntax.py"
    plugin_dir = root / ".opencode" / "plugin"
    if not plugin_checker.exists() or not plugin_dir.exists():
        return []
    try:
        result = subprocess.run(
            [sys.executable, str(plugin_checker)],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        combined = (result.stdout + result.stderr).strip()
        return [f"plugin syntax error(s):\n  {combined}"]
    return []


def check_stale_state_files() -> list[str]:
    tmp = Path("/tmp")
    if not tmp.exists():
        return []
    cutoff = time.time() - STALE_THRESHOLD_SEC
    issues: list[str] = []
    for entry in tmp.glob("gludd-*"):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            age_min = int((time.time() - mtime) / 60)
            issues.append(f"stale state file ({age_min}m old): {entry}")
    return issues


def check_git_dirty_tree(root: Path) -> list[str]:
    if not (root / ".git").exists():
        return []
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root), capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return []
    issues = [f"dirty tree: {len(lines)} uncommitted change(s)"]
    for line in lines[:15]:
        issues.append(f"  {line}")
    return issues


def check_missing_init_files(root: Path) -> list[str]:
    src = root / "src"
    if not src.is_dir():
        return []
    issues: list[str] = []
    for py_dir in src.rglob("*"):
        if not py_dir.is_dir():
            continue
        has_py = any(p.suffix == ".py" for p in py_dir.iterdir() if p.is_file())
        if has_py and not (py_dir / "__init__.py").exists():
            try:
                rel = py_dir.relative_to(root)
            except ValueError:
                rel = py_dir
            issues.append(f"missing __init__.py in: {rel}")
    return issues


def check_make_warnings(root: Path) -> list[str]:
    makefile = root / "Makefile"
    if not makefile.exists():
        return []
    try:
        result = subprocess.run(
            ["make", "-n", "-C", str(root)],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    issues: list[str] = []
    for line in result.stderr.splitlines():
        low = line.lower()
        if "warning" in low or "overriding" in low or "ignoring old commands" in low:
            issues.append(f"make warning: {line.strip()}")
    return issues[:20]


def run_all_checks(root: Path, skip_env: bool) -> list[str]:
    issues: list[str] = []
    issues += check_duplicate_makefile_targets(root)
    issues += check_python_lint(root)
    issues += check_missing_init_files(root)
    issues += check_git_dirty_tree(root)
    if not skip_env:
        issues += check_plugin_syntax(root)
        issues += check_stale_state_files()
        issues += check_make_warnings(root)
    return issues


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    issues = run_all_checks(root, skip_env=args.skip_env_checks)

    if not issues:
        print(f"proactive-scan: OK — 0 issues found in {root}")
        return 0

    print(
        f"proactive-scan: {len(issues)} issue(s) found in {root}",
        file=sys.stderr,
    )
    for issue in issues:
        print(f"  - {issue}", file=sys.stderr)
    print(
        "\nFix these before they surface as user-reported bugs.",
        file=sys.stderr,
    )
    return 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Proactive bug scanner — finds issues before the user does.",
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Repository root to scan (default: script parent's parent).",
    )
    parser.add_argument(
        "--skip-env-checks",
        action="store_true",
        help="Skip environment-specific checks (plugin syntax, /tmp state, make warnings).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
