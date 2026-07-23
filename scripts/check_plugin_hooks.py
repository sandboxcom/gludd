#!/usr/bin/env python3
"""Scan .opencode plugins for hook names that are not valid in the loaded
opencode version (1.17.9 Hooks interface) AND for export-shape issues that
crash opencode's auto-discovery loader.

This catches TWO classes of boot-time crash:

1. **Invalid hook names** — opencode's Plugin.add registry rejects unknown
   hook keys and aborts with ``TypeError: undefined is not an object
   (evaluating 'N.event')``.

2. **Missing/invalid default export** — opencode auto-discovers every ``.ts``
   file in ``.opencode/plugin/`` and calls its default export as a plugin.
   Files with only named exports (``export const X = ...``) crash with
   ``Plugin export is not a function``.

Codified 2026-07-23 after the ``session.idle`` hook AND the ``_exports.ts``
files crashed opencode at every boot. See
``tests/unit/test_plugin_dir_hygiene.py`` for the gate-level guardrail.

Valid hook names mirror @opencode-ai/plugin/dist/index.d.ts `Hooks` interface.
Exit 0 = clean, 1 = invalid hook names OR export issues found.
"""
from __future__ import annotations

import os
import re
import sys

VALID_HOOKS = {
    "dispose",
    "event",
    "config",
    "tool",
    "auth",
    "provider",
    "chat.message",
    "chat.params",
    "chat.headers",
    "permission.ask",
    "command.execute.before",
    "tool.execute.before",
    "tool.execute.after",
    "shell.env",
    "experimental.chat.messages.transform",
    "experimental.chat.system.transform",
    "experimental.provider.small_model",
    "experimental.session.compacting",
    "experimental.compaction.autocontinue",
    "experimental.text.complete",
    "tool.definition",
}

# Hook keys look like "name.with.dots": or 'dispose': followed by an arrow fn
# or async fn value. Match the quoted-key colon pattern.
HOOK_KEY_RE = re.compile(r"""["']([a-z][a-z0-9_.]+)["']\s*:\s*(?:async\s*)?\(""")

SCAN_DIRS = (
    ".opencode/plugin",
    ".opencode/plugins",
    ".opencode/plugin/impl",
)


def scan_file(path: str) -> set[tuple[str, int]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return set()
    bad: set[tuple[str, int]] = set()

    # We want to flag invalid hook keys ONLY inside the object returned by the
    # default-export plugin function (the shape opencode registers). Keys in
    # top-level `defaultImpl` / `impl` HotModule dicts are internal names never
    # registered with opencode, so flagging them is a false positive.
    #
    # Strategy: find every `const NAME: HotModule = {` block and treat those
    # byte ranges as exclusion zones. Any hook-key match falling inside an
    # exclusion zone is skipped. All other matches are flagged.
    exclusion_zones: list[tuple[int, int]] = []
    for m in re.finditer(
        r"\b(?:const|let|var)\s+\w+\s*:\s*HotModule\s*=\s*\{",
        content,
    ):
        start = m.end() - 1
        depth = 1
        i = start + 1
        while i < len(content) and depth > 0:
            ch = content[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        exclusion_zones.append((m.start(), i))

    def in_exclusion(pos: int) -> bool:
        return any(s <= pos <= e for s, e in exclusion_zones)

    for m in HOOK_KEY_RE.finditer(content):
        key = m.group(1)
        if not ("." in key or key in ("dispose", "event", "config", "tool", "auth", "provider")):
            continue
        if key in VALID_HOOKS:
            continue
        if in_exclusion(m.start()):
            continue
        line_no = content[: m.start()].count("\n") + 1
        bad.add((key, line_no))
    return bad


def main() -> int:
    problems: dict[str, set[tuple[str, int]]] = {}
    export_problems: list[str] = []
    for d in SCAN_DIRS:
        if not os.path.isdir(d):
            continue
        # Export-shape check applies ONLY to top-level .ts files in the
        # auto-discovered directories (.opencode/plugin/ and .opencode/plugins/).
        # Subdirectories (impl/, test_exports/) are loaded as module deps
        # via import, NOT via opencode's getLegacyPlugins() auto-discovery.
        is_autoload_dir = d in (".opencode/plugin", ".opencode/plugins")
        for f in sorted(os.listdir(d)):
            if not f.endswith(".ts"):
                continue
            if f.endswith(".test.node.mjs") or f.endswith("_exports.ts"):
                continue
            path = os.path.join(d, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    content = fh.read()
            except OSError:
                continue
            # Export-shape check: only for auto-discovered top-level files.
            if is_autoload_dir:
                if "export default" not in content:
                    export_problems.append(
                        path + ": missing 'export default' "
                        "(opencode auto-loads this file and crashes)"
                    )
                for lineno, line in enumerate(content.split("\n"), 1):
                    stripped = line.lstrip()
                    if (
                        stripped.startswith("export ")
                        and not stripped.startswith("export default")
                        and not stripped.startswith("export type")
                        and not stripped.startswith("export function")
                        and not stripped.startswith("export async")
                    ):
                        export_problems.append(
                            path + ":" + str(lineno) + ": named export '"
                            + stripped[:60].rstrip() + "' crashes legacy loader"
                        )
            # Hook-name check applies to all plugin .ts files.
            bad = scan_file(path)
            if bad:
                problems[path] = bad
    if export_problems:
        print(
            "EXPORT SHAPE ISSUES in " + str(len(export_problems))
            + " file(s):"
        )
        for msg in export_problems:
            print("  " + msg)
        print()
    if not problems and not export_problems:
        print("OK: no invalid hook names or export issues in .opencode plugins")
        return 0
    if problems:
        print(f"INVALID HOOK NAMES in {len(problems)} file(s):")
        for path, hooks in sorted(problems.items()):
            for key, ln in sorted(hooks):
                print(f"  {path}:{ln}  -> {key!r}")
        print()
        print("Valid hooks (opencode 1.17.9 Hooks interface):")
        for h in sorted(VALID_HOOKS):
            print(f"  {h}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
