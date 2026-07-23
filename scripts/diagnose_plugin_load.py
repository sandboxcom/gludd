#!/usr/bin/env python3
"""Diagnose which .opencode plugin crashes Node v26 load.

Runs each plugin through `node --experimental-strip-types` to surface load-time
errors that crash opencode at startup. Reports the ACTUAL node error message,
not just pass/fail.

Usage:
    make diag-plugin-load                       # checks .opencode/
    OPENCODE_DIR=.opencode.orig make diag-plugin-load   # check a backup
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = Path(os.environ.get("OPENCODE_DIR", REPO / ".opencode")).resolve()
PLUGIN_DIR = TARGET / "plugin"
PLUGINS_DIR = TARGET / "plugins"


def load_one(path: Path) -> tuple[int, str, str]:
    """Return (exit_code, stderr, stdout) from node loading the file."""
    try:
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(TARGET),
        )
        return proc.returncode, proc.stderr.strip(), proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after 15s", ""
    except FileNotFoundError:
        return 127, "node binary not found", ""


def collect() -> list[Path]:
    files: list[Path] = []
    for d in (PLUGIN_DIR, PLUGINS_DIR):
        if d.is_dir():
            files.extend(sorted(d.glob("*.ts")))
    return files


def main() -> int:
    if not TARGET.is_dir():
        print(f"ERROR: target dir {TARGET} does not exist")
        print("  set OPENCODE_DIR=... to check a different location")
        return 2

    files = collect()
    if not files:
        print(f"NO PLUGINS FOUND under {PLUGIN_DIR} or {PLUGINS_DIR}")
        return 2

    print(f"Checking {len(files)} plugin file(s) under {TARGET}\n")

    failures: list[tuple[Path, str, str]] = []
    for p in files:
        rc, stderr, stdout = load_one(p)
        try:
            rel = str(p.relative_to(TARGET))
        except ValueError:
            rel = str(p)
        if rc == 0:
            print(f"  OK    {rel}")
        else:
            tail = stderr.splitlines()[-1] if stderr else "(no stderr)"
            print(f"  FAIL  {rel}  rc={rc}  {tail}")
            failures.append((p, stderr, stdout))

    print(f"\n=== {len(files) - len(failures)}/{len(files)} OK, {len(failures)} FAIL ===")
    if failures:
        print("\n=== FAILURES (full stderr) ===")
        for p, stderr, stdout in failures:
            try:
                rel = str(p.relative_to(TARGET))
            except ValueError:
                rel = str(p)
            print(f"\n--- {rel} ---")
            if stderr:
                print(stderr)
            if stdout:
                print("[stdout]")
                print(stdout)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
