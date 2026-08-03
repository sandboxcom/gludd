"""check_test_coverage.py — AA057 enforcement.

Verify that structural tests correctly cross-reference shared.ts imports
when checking enforcement plugins. Tests that assert on plugin source but
miss shared.ts definitions are flagged.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


def main() -> int:
    issues: list[tuple[Path, int, str]] = []
    if not TESTS_DIR.exists():
        print("No tests directory found.")
        return 0

    for test_file in sorted(TESTS_DIR.rglob("test_*plugin*.py")):
        content = test_file.read_text()
        for i, line in enumerate(content.split("\n"), 1):
            if "enforce-" in line and ".ts" in line and "shared.ts" not in line.lower():
                plugin_match = re.search(r"enforce-[\w-]+\.ts", line)
                if plugin_match:
                    plugin_name = plugin_match.group(0)
                    plugin_path = PLUGIN_DIR / plugin_name
                    if plugin_path.exists():
                        plugin_text = plugin_path.read_text()
                        if "shared" in plugin_text.lower() and "shared.ts" not in line.lower():
                            issues.append((test_file, i, plugin_name))

    if not issues:
        print("No cross-reference gaps detected in plugin tests.")
        return 0
    print(f"{len(issues)} test(s) reference plugins importing shared.ts without cross-checking:")
    for path, line, plugin in issues[:20]:
        print(f"  {path}:{line}: {plugin} — check shared.ts imports")
    return 1


if __name__ == "__main__":
    sys.exit(main())
