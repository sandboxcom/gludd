#!/usr/bin/env python3
"""Fix opencode plugin crash by removing named exports from plugin files.

opencode's getLegacyPlugins() iterates Object.values(mod) and requires EVERY
export to be a function. Non-function exports (const, regex, arrays, objects)
cause "Plugin export is not a function" and the plugin is rejected.

FIX: Strip the `export ` keyword from all named (non-default) exports in each
plugin file. The declarations stay at module scope (usable internally) but
are invisible to opencode's Object.values(mod) iteration.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1] / ".opencode" / "plugin"


def strip_named_exports(content: str) -> str:
    """Remove `export ` from named exports (keep export default and export type).

    Handles:
      export const X = ...     -> const X = ...
      export let X = ...       -> let X = ...
      export function x() ... -> function x() ...
      export async function x() -> async function x() ...
      export class X ...      -> class X ...
    """
    lines = content.split('\n')
    result: list[str] = []

    for line in lines:
        # Match: ^export <something> but NOT export default or export type
        if re.match(r'^export (?!default\b)(?!type\b)', line):
            # Remove the 'export ' prefix
            result.append(re.sub(r'^export ', '', line, count=1))
        else:
            result.append(line)

    return '\n'.join(result)


def main() -> None:
    changed: list[str] = []
    skipped: list[str] = []

    for ts_file in sorted(PLUGIN_DIR.glob("*.ts")):
        if ts_file.name.endswith(".test.node.mjs"):
            continue
        if ts_file.name.endswith("_exports.ts"):
            continue
        if ts_file.name == "hot_reload.ts":
            continue

        content = ts_file.read_text()
        # Quick check: any named exports?
        if not re.search(r'^export (?!default\b)(?!type\b)', content, re.MULTILINE):
            skipped.append(ts_file.name)
            continue

        new_content = strip_named_exports(content)
        ts_file.write_text(new_content)
        changed.append(ts_file.name)

    print(f"Changed {len(changed)} plugin files:")
    for name in changed:
        print(f"  {name}")
    print(f"\nSkipped {len(skipped)} (no named exports):")
    for name in skipped:
        print(f"  {name}")


if __name__ == "__main__":
    main()
