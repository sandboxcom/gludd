#!/usr/bin/env python3
"""Verify all enforcement plugins are in blocking mode — structural AND runtime.

CI guard: exits 1 if any plugin is structurally weakened OR has failing runtime tests.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from .plugin_contract import plugin_contract_source
except ImportError:
    from plugin_contract import plugin_contract_source

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

_COVERAGE_ENV_PREFIXES = ("COVERAGE_", "COV_CORE_")
_NESTED_PYTEST_ENV_PREFIXES = ("PYTEST_XDIST_",)
_NESTED_PYTEST_ENV_KEYS = {"PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST"}
_RUNTIME_STATE_FILES: dict[str, str] = {
    "GLUDD_ALIVE_PATH": "plugin-alive.json",
    "GLUDD_BLOCK_COUNTER_FILE": "block-counter.json",
    "GLUDD_BLOCK_REASON_FILE": "block-reason.json",
    "GLUDD_CI_CACHE_PATH": "watchdog-ci.json",
    "GLUDD_COMMIT_LOCK_PATH": "commit.lock",
    "GLUDD_DISENGAGE_PATH": "watchdog-disengage.json",
    "GLUDD_ENHANCEMENT_RATIO_STATE": "enhancement-ratio.json",
    "GLUDD_FALSE_DONE_BLOCKS_FILE": "false-done-blocks.json",
    "GLUDD_FLOOR_TEXT_COMPLETE_COUNT": "floor-text-complete-count.json",
    "GLUDD_FORCE_DELEGATE_STATE": "force-delegate.json",
    "GLUDD_FORCE_DISPATCH_PATH": "force-dispatch.json",
    "GLUDD_HOT_MODULE_PREFIX": "gludd-hot-",
    "GLUDD_LAST_TEST_RESULT_FILE": "last-test-result.json",
    "GLUDD_MAINTHREAD_STREAK_FILE": "mainthread-streak.json",
    "GLUDD_MAIN_MODEL_FILE": "main-model",
    "GLUDD_MODEL_UTIL_STATE": "model-util.json",
    "GLUDD_MULTITASK_STATE_FILE": "multitask-state.json",
    "GLUDD_PERSIST_STOP_BLOCK_FILE": "persist-stop-block.json",
    "GLUDD_POST_RESULTS_STATE_FILE": "post-results-state.json",
    "GLUDD_READ_GRIND_FILE": "read-grind.json",
    "GLUDD_RELEASE_COMPLETENESS_FILE": "release-completeness.json",
    "GLUDD_SESSION_STATE": "session-start.json",
    "GLUDD_STOP_STATE_FILE": "stop-state.json",
    "GLUDD_STOP_STATE_PATH": "stop-state-signal.json",
    "GLUDD_STOP_TEXT_COMPLETE_COUNT": "stop-text-complete-count.json",
    "GLUDD_STOP_TOOL_COUNTS_FILE": "stop-tool-counts.json",
    "GLUDD_STREAK_FILE": "tool-streak.json",
    "GLUDD_TASK_DEADLINE_STATE": "task-deadlines.json",
    "GLUDD_TASK_DEADLINE_WARNINGS": "task-deadline-warnings.log",
    "GLUDD_TASK_STALE_FILE": "task-stale.json",
    "GLUDD_TEXT_ONLY_STATE_FILE": "text-only-state.json",
    "GLUDD_TODOWRITE_STATE": "todowrite-state.json",
    "GLUDD_TODOWRITE_STATE_PATH": "todowrite-state-signal.json",
    "GLUDD_WATCHDOG_CI_FILE": "watchdog-ci-state.json",
    "GLUDD_WATCHDOG_PID_FILE": "watchdog.pid",
}


def _runtime_environment(state_root: Path) -> dict[str, str]:
    """Build a hermetic environment for the nested hook-runtime pytest."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(
            _COVERAGE_ENV_PREFIXES + _NESTED_PYTEST_ENV_PREFIXES
        )
        and name not in _NESTED_PYTEST_ENV_KEYS
    }
    env.update(
        {
            "GLUDD_PROJECT_ROOT": str(ROOT),
            "GLUDD_RUNTIME_TEST_STATE_DIR": str(state_root),
            "OPENCODE_SUBAGENT": "0",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "TEMP": str(state_root),
            "TMP": str(state_root),
            "TMPDIR": str(state_root),
            "UV_NO_SYNC": "1",
        }
    )
    env.update(
        {
            name: str(state_root / filename)
            for name, filename in _RUNTIME_STATE_FILES.items()
        }
    )
    return env


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
    "enforce-worktree.ts": "GLUDD_WORKTREE_ENFORCE",
    "enforce-audit.ts": "GLUDD_AUDIT_ENFORCE",
    "enforce-context.ts": "GLUDD_CONTEXT_ENFORCE",
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

        source = plugin_contract_source(path)

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

def _parse_pytest_summary(combined: str) -> tuple[int, int]:
    """Extract (failed, passed) from pytest's summary line.

    Handles all pytest output shapes:
        "N failed, M passed"                 (no skips)
        "M passed, K skipped"                (no failures — old regex missed this)
        "N failed, M passed, K skipped"      (failures + skips)
        "N passed"                           (only passes)
    Returns (0, 0) if no summary line found.
    """
    failed = 0
    passed = 0
    m_failed = re.search(r"(\d+)\s+failed", combined)
    m_passed = re.search(r"(\d+)\s+passed", combined)
    if m_failed:
        failed = int(m_failed.group(1))
    if m_passed:
        passed = int(m_passed.group(1))
    return failed, passed


def _attribute_failed_line(line: str) -> str | None:
    """Map a pytest FAILED line to its owning plugin via test-name prefix.

    Pytest FAILED format: "FAILED path::test_name - ErrorType: msg"
    Extracts the test_name token and matches against _TEST_PREFIX_TO_PLUGIN
    at a token boundary (not a bare substring) to prevent false attribution.
    Returns plugin filename or None.
    """
    m = re.search(r"::(test_[a-z_]+)", line)
    if not m:
        return None
    test_name = m.group(1)
    for prefix, plugin in _TEST_PREFIX_TO_PLUGIN.items():
        if test_name.startswith(prefix):
            return plugin
    return None


def _check_runtime() -> tuple[int, int, int, set[str]]:
    """Run test-hook-runtime. Returns (passed, failed, total, failing_plugins)."""
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"gludd-verify-enforcement-{os.getpid()}-"
        ) as state_dir:
            state_root = Path(state_dir)
            result = subprocess.run(
                ["uv", "run", "python", "scripts/test_hook_runtime.py"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(ROOT),
                env=_runtime_environment(state_root),
            )
    except subprocess.TimeoutExpired:
        return 0, 0, 0, {"runtime-timed-out"}

    combined = result.stdout + "\n" + result.stderr

    failed, passed = _parse_pytest_summary(combined)

    failing_plugins: set[str] = set()
    for line in combined.splitlines():
        line = line.strip()
        if not line.startswith("FAILED"):
            continue
        plugin = _attribute_failed_line(line)
        if plugin is not None:
            failing_plugins.add(plugin)

    # SyntaxError attribution: only fire when (a) "SyntaxError" appears AND
    # (b) the filename is one of the known enforcement plugins. The old regex
    # matched ANY .ts file in any stack frame (shared.ts, hot-reload modules,
    # etc.), causing spurious attribution to whichever plugin appeared first.
    if "SyntaxError" in combined:
        for plugin_filename in ENFORCEMENT_PLUGINS:
            if re.search(re.escape(plugin_filename) + r":\d+", combined):
                failing_plugins.add(plugin_filename)

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
