#!/usr/bin/env python3
"""Self-contained smoke test verifying .opencode/ is ready for an opencode restart.

Checks (order matters — fails fast on each phase):
  1. opencode.json is valid JSON
  2. All plugin paths declared in opencode.json exist on disk
  3. node --check on every .ts file (syntax)
  4. node --experimental-strip-types import on every .ts file (runtime load)
  5. check_plugin_imports.py (import audit)
  6. check_plugin_syntax.py (syntax audit)

Exits 0 only if ALL phases pass.  Prints a single-page PASS/FAIL summary.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPECODE_JSON = ROOT / "opencode.json"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"
SCRIPTS = ROOT / "scripts"

RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"


def _ok(label: str) -> str:
    return f"  {GREEN}PASS{RESET}  {label}"


def _fail(label: str, detail: str = "") -> str:
    msg = f"  {RED}FAIL{RESET}  {label}"
    if detail:
        msg += f"\n        {YELLOW}{detail}{RESET}"
    return msg


def _check_json() -> tuple[bool, str]:
    try:
        data = json.loads(OPECODE_JSON.read_text())
    except json.JSONDecodeError as e:
        return False, _fail("opencode.json valid JSON", str(e))
    return True, _ok("opencode.json valid JSON")


def _check_plugin_paths() -> tuple[bool, str]:
    try:
        data = json.loads(OPECODE_JSON.read_text())
    except json.JSONDecodeError:
        return False, _fail("plugin paths exist", "json parse failed — skipping")

    plugins = data.get("plugin", [])
    if not plugins:
        return True, _ok("plugin paths exist (0 declared)")

    missing = []
    for p in plugins:
        resolved = (ROOT / p).resolve()
        if not resolved.exists():
            missing.append(p)
    if missing:
        return False, _fail(
            "plugin paths exist",
            f"{len(missing)} declared path(s) missing:\n        " + "\n        ".join(missing),
        )
    return True, _ok(f"plugin paths exist ({len(plugins)} declared)")


def _get_registered_plugins() -> set[str]:
    if not OPECODE_JSON.exists():
        return set()
    try:
        data = json.loads(OPECODE_JSON.read_text())
    except json.JSONDecodeError:
        return set()
    return {p for p in data.get("plugin", [])}


def _collect_ts_files() -> list[Path]:
    registered = _get_registered_plugins()
    files = []
    for d in (PLUGIN_DIR, PLUGINS_DIR):
        if d.exists():
            for f in sorted(d.glob("*.ts")):
                rel = "./" + f.relative_to(ROOT).as_posix()
                if rel in registered:
                    files.append(f)
    return files


def _check_syntax() -> tuple[bool, str]:
    errors = 0
    lines = []
    for f in _collect_ts_files():
        result = subprocess.run(
            ["node", "--check", str(f)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            errors += 1
            lines.append(f"    {f.name}: {result.stderr.strip()[:200]}")
    if errors:
        return False, _fail(
            f"node --check syntax ({errors} error(s))",
            "\n".join(lines),
        )
    return True, _ok(f"node --check syntax ({len(_collect_ts_files())} files)")


def _check_runtime_load() -> tuple[bool, str]:
    all_ts = _collect_ts_files()
    errors = 0
    lines = []
    for f in all_ts:
        rel = f.relative_to(ROOT).as_posix()
        script = "import('./" + rel.replace("'", "\\'") + "')"
        result = subprocess.run(
            ["node", "--experimental-strip-types", "-e", script],
            capture_output=True, text=True, timeout=30,
            cwd=str(ROOT),
        )
        if result.returncode != 0:
            errors += 1
            output = (result.stderr or result.stdout).strip()
            lines.append(f"    {f.name}: {output[:200]}")
    if errors:
        return False, _fail(
            f"node --experimental-strip-types runtime load ({errors} error(s))",
            "\n".join(lines),
        )
    return True, _ok(f"node --experimental-strip-types runtime load ({len(all_ts)} files)")


def _run_script(name: str) -> tuple[bool, str]:
    script = SCRIPTS / name
    if not script.exists():
        return False, _fail(name, f"script not found: {script}")
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return False, _fail(
            name,
            result.stdout.strip()[-300:] or result.stderr.strip()[-300:],
        )
    return True, _ok(name)


def _check_backup() -> tuple[bool, str]:
    orig = ROOT / ".opencode.orig"
    current = ROOT / ".opencode"
    if not current.is_dir():
        return True, _ok("backup fresh (no .opencode/ to back up)")
    if not orig.is_dir():
        return False, _fail(
            "backup fresh",
            ".opencode.orig/ does not exist — run 'make backup-opencode'",
        )
    current_mtime = current.stat().st_mtime
    orig_mtime = orig.stat().st_mtime
    if orig_mtime < current_mtime:
        return False, _fail(
            "backup fresh",
            ".opencode.orig/ is older than .opencode/ — run 'make backup-opencode'",
        )
    return True, _ok("backup fresh")


def main() -> int:
    print(f"\n{BOLD}=== opencode restart readiness check ==={RESET}\n")

    backup_ok, backup_msg = _check_backup()
    checks: list[tuple[str, tuple[bool, str]]] = [
        ("json", _check_json()),
        ("paths", _check_plugin_paths()),
        ("syntax", _check_syntax()),
        ("runtime", _check_runtime_load()),
        ("imports", _run_script("check_plugin_imports.py")),
        ("syntax-audit", _run_script("check_plugin_syntax.py")),
        ("backup", (backup_ok, backup_msg)),
    ]

    all_pass = True
    for _name, (ok, output) in checks:
        print(output)
        if not ok:
            all_pass = False

    print()
    if all_pass:
        print(f"  {GREEN}{BOLD}=== ALL CHECKS PASSED ==={RESET}  .opencode/ is ready for restart.\n")
        return 0
    else:
        print(f"  {RED}{BOLD}=== CHECKS FAILED ==={RESET}  Fix errors above before restarting opencode.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
