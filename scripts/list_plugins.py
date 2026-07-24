#!/usr/bin/env python3
"""Generate a machine-readable list of all enforcement plugins with their
hook registrations, block conditions, and disable env vars.

Usage: python scripts/list_plugins.py [--json|--markdown]
"""
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(PROJECT_ROOT, ".opencode", "plugin")
HOOK_KEYS = [
    "tool.execute.before",
    "tool.execute.after",
    "text.complete",
    "experimental.text.complete",
    "session.idle",
    "system.transform",
]


def extract_default_impl_hooks(content: str):
    """Extract hooks from the defaultImpl object (the actual enforcement logic)."""
    hooks = {}
    # Find defaultImpl block
    m = re.search(r'const defaultImpl[^=]*=\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', content, re.DOTALL)
    if not m:
        # Try without 'const'
        m = re.search(r'defaultImpl[^=]*=\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', content, re.DOTALL)
    if not m:
        return hooks
    block = m.group(1)
    for hook in HOOK_KEYS:
        if f'"{hook}"' in block:
            hooks[hook] = True
    return hooks


def extract_disable_env(content: str):
    """Extract the disable env var."""
    match = re.search(r'process\.env\.(GLUDD_\w+_ENFORCE)\s*!==\s*["\']0["\']', content)
    if match:
        return match.group(1)
    match = re.search(r'process\.env\.(GLUDD_\w+_ENFORCE)', content)
    if match:
        return match.group(1)
    return "—"


def extract_block_summary(content: str, fname: str):
    """Summarize what the plugin blocks."""
    has_bash = bool(re.search(r'tool\s*(===|!==)\s*"bash"', content))
    has_edit = bool(re.search(r'tool\s*(===|!==)\s*"edit"', content))
    has_task = bool(re.search(r'DispatchTool|dispatch.*tool|"task"[\s,]*"agent"[\s,]*"workflow"', content))
    has_read_guard = bool(re.search(r'isReadTool\b', content))
    has_read_tool_check = bool(re.search(r'"read"', content))
    has_session_grace = bool(re.search(r'sessionPrimed|session.*[Gg]race|DISPATCH_NOW_SECS|FRESH_SECS', content))

    blocks = []
    if has_bash:
        blocks.append("bash")
    if has_edit:
        blocks.append("edit/write")
    if has_task:
        blocks.append("task/agent dispatch")
    if has_read_guard:
        blocks.append("↳ excludes reads")
    if has_session_grace:
        blocks.append("↳ session-start grace")
    return ", ".join(blocks) if blocks else "all (no tool filter)"


def main():
    fmt = sys.argv[1] if len(sys.argv) > 1 else "--markdown"
    plugins = []
    for fname in sorted(os.listdir(PLUGIN_DIR)):
        if not fname.endswith(".ts"):
            continue
        fpath = os.path.join(PLUGIN_DIR, fname)
        content = open(fpath).read()
        hooks = extract_default_impl_hooks(content)
        if not hooks:
            continue  # no defaultImpl hooks = no enforcement
        disable = extract_disable_env(content)
        blocks = extract_block_summary(content, fname)

        plugins.append({
            "file": fname,
            "hooks": sorted(hooks.keys()),
            "blocks": blocks,
            "disable": disable,
        })

    if fmt == "--json":
        print(json.dumps(plugins, indent=2))
    else:
        # Markdown table
        print("| Plugin | Hooks | Blocks | Disable |")
        print("|--------|-------|--------|---------|")
        for p in plugins:
            hooks_str = ", ".join(p["hooks"])
            print(f"| {p['file']} | {hooks_str} | {p['blocks']} | {p['disable']} |")
        print(f"\n{len(plugins)} plugins total.")


if __name__ == "__main__":
    main()
