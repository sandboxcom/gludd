#!/usr/bin/env python3
"""AB038 — audit enforcement plugin hook exports for test coverage.

Scans each .opencode/plugin/enforce-*.ts for exported hook functions
(toolExecuteBefore, textComplete, etc.) and cross-references test files
for corresponding test functions. Plugins with exported hooks that have
zero tests are flagged.

Exit non-zero if any enforcement plugin has <1 test per exported hook.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
TEST_DIR = ROOT / "tests" / "unit"

EXPORT_RE = re.compile(r"export\s+(?:async\s+)?function\s+(\w+)")
HOOK_NAMES = {
    "toolExecuteBefore",
    "textComplete",
    "sessionIdle",
    "systemTransform",
    "chatTransform",
}


def extract_exports(ts_path: Path) -> list[str]:
    if not ts_path.exists():
        return []
    content = ts_path.read_text()
    exports = []
    for m in EXPORT_RE.finditer(content):
        name = m.group(1)
        if name in HOOK_NAMES or "Hook" in name or "check" in name.lower():
            exports.append(name)
    return exports


def find_tests_for_plugin(plugin_name: str) -> list[str]:
    base = plugin_name.replace(".ts", "").replace("enforce-", "")
    test_files = list(TEST_DIR.glob(f"test_enforce_{base}*.py"))
    test_names = []

    for tf in test_files:
        content = tf.read_text()
        for m in re.finditer(r"def (test_\w+)", content):
            test_names.append(m.group(1))

    return test_names


def main() -> int:
    plugin_files = sorted(PLUGIN_DIR.glob("enforce-*.ts"))
    violations: list[str] = []

    for pf in plugin_files:
        exports = extract_exports(pf)
        if not exports:
            continue

        tests = find_tests_for_plugin(pf.name)
        ratio = f"{len(tests)} test(s) for {len(exports)} export(s)"

        if len(tests) == 0:
            violations.append(f"  {pf.name}: {ratio} — ZERO test coverage")
        elif len(tests) < len(exports):
            violations.append(f"  {pf.name}: {ratio} — below 1:1 threshold")

    if violations:
        print(f"audit-plugin-hook-exports: {len(violations)} plugin(s) below test coverage threshold:")
        for v in violations:
            print(v)
        return 1

    print(f"audit-plugin-hook-exports: all {len(plugin_files)} plugins meet test coverage threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
