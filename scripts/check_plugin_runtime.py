#!/usr/bin/env python3
"""scripts/check_plugin_runtime.py — runtime validation of .opencode plugins.

Delegates to scripts/validate_plugins_runtime.mjs for comprehensive checks:
  - Imports each plugin module
  - Invokes the factory function (default export)
  - Calls each hook with null inputs to catch ReferenceError (undefined symbols)
  - Catches SyntaxError, import resolution errors, and eval-mode incompatibility

The Node.js script is the canonical implementation; this Python wrapper exists
for make-target integration.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "validate_plugins_runtime.mjs"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    cmd = ["node", "--experimental-strip-types", str(SCRIPT)]
    # If explicit directory given, pass it; otherwise let the script auto-detect
    if args:
        cmd.append(args[0])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
