#!/usr/bin/env python3
"""Add console.log("SUBAGENT SKIP: <plugin>") to every OPENCODE_SUBAGENT guard line."""

import re
import os

PLUGIN_DIR = ".opencode/plugin"
PLUGINS = sorted(f for f in os.listdir(PLUGIN_DIR) if f.endswith(".ts") and f != "hot_reload.ts")

for fname in PLUGINS:
    fpath = os.path.join(PLUGIN_DIR, fname)
    with open(fpath) as fh:
        content = fh.read()

    plugin_label = fname.replace(".ts", "")

    def replacer(m):
        indent = m.group(1)
        rest = m.group(2).rstrip()
        return f'{indent}{rest}\n{indent}console.log("SUBAGENT SKIP: {plugin_label}")'

    new_content, count = re.subn(
        r'^(\s+)(if\s*\(\s*process\.env\.OPENCODE_SUBAGENT\s*===\s*"1"\s*\)\s*return\s*[a-zA-Z_]*\s*;?\s*)$',
        replacer,
        content,
        flags=re.MULTILINE,
    )

    # Also handle "if (isSubagent) return" in enforce-make.ts (already has console.log)
    # And handle "if (process.env.OPENCODE_SUBAGENT === "1") return output" (with output)
    new_content2, count2 = re.subn(
        r'^(\s+)(if\s*\(\s*process\.env\.OPENCODE_SUBAGENT\s*===\s*"1"\s*\)\s*return\s+output\s*;?\s*)$',
        replacer,
        new_content,
        flags=re.MULTILINE,
    )

    total = count + count2
    if total > 0:
        with open(fpath, "w") as fh:
            fh.write(new_content2)
        print(f"  {fname}: {total} guards annotated")

print(f"\nDone — processed {len(PLUGINS)} files")
