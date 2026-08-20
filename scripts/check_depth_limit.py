#!/usr/bin/env python3
"""Verify depth-limit config matches expected value. Exits 0 on match, 1 on mismatch."""

import os
import re
import sys

PLUGIN_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".opencode",
    "plugin",
    "enforce-depth.ts",
)

EXPECTED_MAX = int(os.environ.get("EXPECTED_MAX_DEPTH", "3"))


def read_depth_config(path: str) -> tuple[int, str]:
    with open(path) as f:
        content = f.read()
    match = re.search(
        r'MAX_DEPTH\s*=\s*parseInt\(process\.env\.GLUDD_MAX_DEPTH\s*\|\|\s*"(\d+)"',
        content,
    )
    if not match:
        return 0, f"MISSING: MAX_DEPTH declaration not found in {path}"
    default = int(match.group(1))
    env_val = os.environ.get("GLUDD_MAX_DEPTH")
    effective = int(env_val) if env_val else default
    source = f"env GLUDD_MAX_DEPTH={env_val}" if env_val else f"default={default}"
    return effective, f"MAX_DEPTH={effective} (source: {source})"


def check_subagent_depth_only() -> int:
    """Check enforce-depth.ts does NOT bypass subagents in code (it's depth-only)."""
    with open(PLUGIN_PATH) as plugin_file:
        lines = [line for line in plugin_file if not line.strip().startswith("//")]
    content = "\n".join(lines)
    if "isSubagent()" in content or "OPENCODE_SUBAGENT" in content:
        print("FAIL: enforce-depth.ts contains a subagent bypass — depth checks must run in delegated contexts")
        return 1
    print("OK: enforce-depth.ts has NO subagent bypass (uses OPENCODE_DEPTH, fires in subagents intentionally)")
    return 0


def main() -> int:
    effective, msg = read_depth_config(PLUGIN_PATH)
    print(msg)
    if effective < EXPECTED_MAX:
        print(f"FAIL: effective MAX_DEPTH={effective} < expected {EXPECTED_MAX}")
        print("3x dispatch (main→agent→subagent→subagent) requires MAX_DEPTH >= 3")
        return 1
    print(
        "OK: 3x dispatch supported "
        f"(max depth {effective}, subagent at depth {effective} blocked from further dispatch)"
    )
    return check_subagent_depth_only()


if __name__ == "__main__":
    sys.exit(main())
