#!/usr/bin/env python3
"""Diagnose opencode plugin load by importing ALL plugins in one node process.

Plugins load fine individually but opencode loads them ALL at startup. This
script reproduces that: imports every .opencode/plugin/*.ts in a single node
process to surface:
  - circular dependency errors
  - duplicate export name conflicts
  - shared module state corruption
  - memory issues from loading 26+ modules

Usage:
    make diag-plugin-load-all
    OPENCODE_DIR=.opencode.orig make diag-plugin-load-all
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = Path(os.environ.get("OPENCODE_DIR", REPO / ".opencode")).resolve()
PLUGIN_DIR = TARGET / "plugin"
PLUGINS_DIR = TARGET / "plugins"


def collect() -> list[Path]:
    files: list[Path] = []
    for d in (PLUGIN_DIR, PLUGINS_DIR):
        if d.is_dir():
            files.extend(sorted(d.glob("*.ts")))
    return files


def main() -> int:
    files = collect()
    if not files:
        print(f"NO PLUGINS under {TARGET}", file=sys.stderr)
        return 2

    # Build an ESM importer that imports every plugin in one process.
    # Use dynamic import() so a single failure doesn't abort the rest.
    imports = "\n".join(
        f'  await import("{p}").then('
        f'    () => console.log("OK    {p.name}"),'
        f'    (e) => console.log("FAIL  {p.name}: " + (e.message || e)));'
        for p in files
    )

    script = textwrap.dedent(
        """
        const files = [
        """
        + "\n".join(f'  "{p}",' for p in files)
        + """
        ];
        (async () => {
        """
        + imports
        + """
        })();
        """
    )

    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(TARGET),
    )

    print(proc.stdout)
    if proc.stderr:
        print("[stderr]")
        print(proc.stderr)
    return proc.returncode if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
