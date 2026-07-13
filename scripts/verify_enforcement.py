#!/usr/bin/env python3
"""Verify all enforcement plugins are structurally in blocking mode.

CI guard: exits 1 if any plugin is advisory-only or structurally weakened.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

ENFORCEMENT_PLUGINS: dict[str, str] = {
    "enforce-floor.ts": "GLUDD_FLOOR_ENFORCE",
    "enforce-delegate.ts": "GLUDD_MAINTHREAD_STREAK_ENFORCE",
    "enforce-multitask.ts": "GLUDD_MULTITASK_FLOOR_ENFORCE",
    "enforce-stop.ts": "GLUDD_STOP_ENFORCE",
    "enforce-deadline.ts": "GLUDD_TASK_DEADLINE_BLOCK",
    "enforce-enhancement-ratio.ts": "GLUDD_ENHANCEMENT_RATIO_BLOCK",
    "enforce-clean-tree.ts": "GLUDD_CLEAN_TREE_ENFORCE",
    "enforce-verified-claims.ts": "GLUDD_VERIFIED_CLAIMS_ENFORCE",
    "enforce-no-suppressions.ts": None,  # hard-coded ON
    "enforce-session-start.ts": "GLUDD_SESSION_START_ENFORCE",
}

BLOCKING_PATTERNS = [
    re.compile(r'permissionDecision":\s*"deny"'),
    re.compile(r"permissionDecision':\s*'deny'"),
    re.compile(r"permissionDecision:\s*\"deny\""),
    re.compile(r"permissionDecision:\s*'deny'"),
    re.compile(r"throw new Error\("),
    re.compile(r"throw Error\("),
    # text.complete hooks: block by replacing output.text
    re.compile(r"output\.text\s*=\s*BLOCK_MESSAGE"),
    # text.complete hooks: zeroStreak threshold block (enforce-multitask)
    re.compile(r"output\.text\s*=\s*ZERO_STREAK_BLOCKED"),
]


def _has_blocking_pattern(source: str) -> bool:
    return any(pat.search(source) for pat in BLOCKING_PATTERNS)


def main() -> int:
    failures: list[str] = []

    for filename, disable_var in ENFORCEMENT_PLUGINS.items():
        path = PLUGIN_DIR / filename

        if not path.exists():
            failures.append(f"{filename}: MISSING — file not found in .opencode/plugin/")
            continue

        source = path.read_text(encoding="utf-8")

        if not _has_blocking_pattern(source):
            failures.append(
                f"{filename}: NO BLOCKING PATTERN — "
                "missing permissionDecision:deny or throw new Error"
            )

        if disable_var is not None and os.environ.get(disable_var) == "0":
            failures.append(
                f"{filename}: DISABLED — env var {disable_var}=0 is set "
                "(CI must never disable enforcement)"
            )

    print(f"=== Verify Enforcement — {len(ENFORCEMENT_PLUGINS)} plugins ===")
    for filename in ENFORCEMENT_PLUGINS:
        path = PLUGIN_DIR / filename
        if path.exists():
            source = path.read_text(encoding="utf-8")
            has_block = _has_blocking_pattern(source)
            var = ENFORCEMENT_PLUGINS[filename]
            disabled = var and os.environ.get(var) == "0"
            status = "BLOCKING" if has_block and not disabled else "WEAKENED"
            print(f"  {filename:<34} [{status}]")
        else:
            print(f"  {filename:<34} [MISSING]")

    if failures:
        print(f"\nFAIL: {len(failures)} enforcement plugin(s) not in blocking mode:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASS: all enforcement plugins are structurally in blocking mode.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
