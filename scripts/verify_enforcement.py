#!/usr/bin/env python3
"""Verify all enforcement plugins are in blocking mode — structural AND runtime.

CI guard: exits 1 if any plugin is structurally weakened OR has failing runtime tests.
"""
from __future__ import annotations

import os
import re
import subprocess
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
    re.compile(r"output\.text\s*=\s*BLOCK_MESSAGE"),
    re.compile(r"output\.text\s*=\s*ZERO_STREAK_BLOCKED"),
]

_TEST_PREFIX_TO_PLUGIN: dict[str, str] = {
    "test_clean_tree_": "enforce-clean-tree.ts",
    "test_enhancement_": "enforce-enhancement-ratio.ts",
    "test_delegate_": "enforce-delegate.ts",
    "test_deadline_": "enforce-deadline.ts",
    "test_floor_": "enforce-floor.ts",
    "test_multitask_": "enforce-multitask.ts",
    "test_stop_": "enforce-stop.ts",
    "test_verified_": "enforce-verified-claims.ts",
    "test_no_suppression_": "enforce-no-suppressions.ts",
    "test_session_start_": "enforce-session-start.ts",
}


def _has_blocking_pattern(source: str) -> bool:
    return any(pat.search(source) for pat in BLOCKING_PATTERNS)


def _check_structural() -> tuple[list[str], dict[str, bool]]:
    """Run structural checks. Returns (failures, status_map)."""
    failures: list[str] = []
    status: dict[str, bool] = {}

    for filename, disable_var in ENFORCEMENT_PLUGINS.items():
        path = PLUGIN_DIR / filename

        if not path.exists():
            failures.append(f"{filename}: MISSING")
            status[filename] = False
            continue

        source = path.read_text(encoding="utf-8")

        if not _has_blocking_pattern(source):
            failures.append(
                f"{filename}: NO BLOCKING PATTERN"
            )
            status[filename] = False
        elif disable_var is not None and os.environ.get(disable_var) == "0":
            failures.append(
                f"{filename}: DISABLED — {disable_var}=0"
            )
            status[filename] = False
        else:
            status[filename] = True

    return failures, status


def _check_runtime() -> tuple[int, int, int, set[str]]:
    """Run test-hook-runtime. Returns (passed, failed, total, failing_plugins)."""
    try:
        result = subprocess.run(
            ["uv", "run", "python", "scripts/test_hook_runtime.py"],
            capture_output=True, text=True, timeout=60,
            cwd=str(ROOT),
            env={**os.environ, "OPENCODE_SUBAGENT": "", "UV_NO_SYNC": "1"},
        )
    except subprocess.TimeoutExpired:
        return 0, 0, 0, {"runtime-timed-out"}

    stdout = result.stdout
    stderr = result.stderr

    passed = 0
    failed = 0

    m = re.search(r"(\d+)\s+failed,\s+(\d+)\s+passed", stdout)
    if m:
        failed = int(m.group(1))
        passed = int(m.group(2))

    failing_plugins: set[str] = set()
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("FAILED"):
            continue
        for prefix, plugin in _TEST_PREFIX_TO_PLUGIN.items():
            if prefix in line:
                failing_plugins.add(plugin)
                break

    # Also check stderr for SyntaxError — all enforce-stop tests fail due to
    # a single SyntaxError in the plugin source. Surface the file that has it.
    if "SyntaxError" in stderr or "SyntaxError" in stdout:
        # extract filename from stderr if possible
        m_ts = re.search(r"([a-z_-]+\.ts):\d+", stderr + stdout)
        if m_ts:
            failing_plugins.add(m_ts.group(1))

    return passed, failed, passed + failed, failing_plugins


def main() -> int:
    # --- structural ---
    structural_failures, structural_status = _check_structural()

    # --- runtime ---
    passed, failed, total, failing_plugins = _check_runtime()

    # --- report ---
    print(f"=== Verify Enforcement — {len(ENFORCEMENT_PLUGINS)} plugins ===")
    print(f"{'Plugin':<34} [structural] [runtime]")
    print("-" * 63)

    for filename in ENFORCEMENT_PLUGINS:
        s_ok = structural_status.get(filename, False)
        s_label = "BLOCKING " if s_ok else "WEAKENED "
        r_label = "PASS" if filename not in failing_plugins else "FAIL"
        print(f"  {filename:<34} [{s_label}]   [{r_label}]")

    # --- summary ---
    print(f"\nStructural: {len([f for f in structural_failures])}/{len(ENFORCEMENT_PLUGINS)} issues")
    if structural_failures:
        for f in structural_failures:
            print(f"  - {f}")

    print(f"Runtime:    {failed} failed, {passed} passed, {total} total")
    if failing_plugins:
        print("  Plugins with runtime failures:")
        for p in sorted(failing_plugins):
            print(f"    - {p}")

    if structural_failures or failing_plugins:
        print(f"\nFAIL: {len(structural_failures)} structural + {len(failing_plugins)} "
              f"plugin(s) with runtime failures.")
        return 1

    print(f"\nPASS: all enforcement plugins structurally blocking, all {passed} runtime tests pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
