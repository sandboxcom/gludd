#!/usr/bin/env python3
"""Add file-based OPENCODE_SUBAGENT fallback to all enforcement plugins."""
from __future__ import annotations

import re
import sys
from pathlib import Path

PLUGIN_DIR = Path(".opencode/plugin")

FS_IMPORT_NEEDED = [
    "enforce-commit-lock.ts",
    "enforce-deletion-gate.ts",
    "enforce-clean-tree.ts",
    "enforce-no-wait.ts",
    "enforce-verified-claims.ts",
]

HELPER_FUNC = """function _isSubagent(): boolean {
  if (process.env.OPENCODE_SUBAGENT === "1") return true;
  try { return fs.existsSync(`/tmp/gludd-subagent-${process.pid}.json`); } catch { return false; }
}
"""


def process_file(filepath: Path) -> str:
    content = filepath.read_text()
    filename = filepath.name

    # Add fs import if needed
    if filename in FS_IMPORT_NEEDED:
        if "import * as fs from" not in content and 'require("fs")' not in content:
            # Add after the first import line or after the file doc comment
            lines = content.split("\n")
            insert_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("import "):
                    insert_idx = i + 1
                elif not line.startswith("import ") and insert_idx > 0:
                    break
                elif i > 3 and not line.startswith("//") and not line.startswith("/**") and not line.startswith(" *") and not line.startswith("*/"):
                    insert_idx = i
                    break

            if insert_idx == 0:
                insert_idx = 1
            lines.insert(insert_idx, 'import * as fs from "node:fs"')
            content = "\n".join(lines)

    # Add helper function if not present
    if "_isSubagent()" not in content:
        fn_match = re.search(r'(function \w+\(|async \(\) =>|export default (function|async))', content)
        if fn_match:
            insert_pos = fn_match.start()
            # Find the start of the line
            line_start = content.rfind("\n", 0, insert_pos)
            if line_start == -1:
                line_start = 0
            else:
                line_start += 1
            content = content[:line_start] + HELPER_FUNC + "\n" + content[line_start:]
        else:
            # Fallback: add after last import
            last_import = 0
            for m in re.finditer(r'^import .+$', content, re.MULTILINE):
                last_import = m.end()
            if last_import == 0:
                last_import = 0
            else:
                content = content[:last_import] + "\n\n" + HELPER_FUNC + content[last_import:]

    # Replace all process.env.OPENCODE_SUBAGENT === "1" with _isSubagent()
    content = content.replace('process.env.OPENCODE_SUBAGENT === "1"', "_isSubagent()")

    # Also handle const isSubagent = process.env.OPENCODE_SUBAGENT === "1"
    content = content.replace(
        'const isSubagent = process.env.OPENCODE_SUBAGENT === "1"',
        "const isSubagent = _isSubagent()"
    )

    return content


def main() -> int:
    for filepath in sorted(PLUGIN_DIR.glob("enforce-*.ts")):
        before = filepath.read_text()
        after = process_file(filepath)
        if before != after:
            filepath.write_text(after)
            count = before.count('process.env.OPENCODE_SUBAGENT === "1"')
            added_fs = "import * as fs from" not in before.split("\n")[:5] and "import * as fs from" in after.split("\n")[:10]
            print(f"  UPDATED {filepath.name}: {count} occurrences replaced, fs_import_added={added_fs}")
        else:
            print(f"  SKIPPED {filepath.name}: no changes needed")

    # Also handle hot_reload.ts
    hot_path = PLUGIN_DIR / "hot_reload.ts"
    hot_content = hot_path.read_text()
    if "_isSubagent()" not in hot_content:
        hot_content = hot_content + "\n\n" + HELPER_FUNC
        hot_path.write_text(hot_content)
        print(f"  UPDATED hot_reload.ts: added _isSubagent helper")

    return 0


if __name__ == "__main__":
    sys.exit(main())
