#!/usr/bin/env python3
"""Register agent_count_truth.sh in .claude/settings.json under UserPromptSubmit + Stop.

Updates both the worktree's local settings.json and the main repo's settings.json.
Idempotent — safe to run multiple times.

Usage: python3 scripts/register_agent_count_truth_hook.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOOK_CMD = "bash /Users/shawnwilson/gludd/.claude/hooks/agent_count_truth.sh"
HOOK_ENTRY = {"type": "command", "command": HOOK_CMD}

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent


def _register(settings_path: Path) -> bool:
    """Register the hook in the given settings.json. Returns True if changed."""
    if not settings_path.exists():
        print(f"  [register] {settings_path} not found — skipping")
        return False

    settings = json.loads(settings_path.read_text())
    hooks = settings.setdefault("hooks", {})
    changed = False

    for event in ("UserPromptSubmit", "Stop"):
        event_hooks = hooks.setdefault(event, [])
        # Collect all existing hook commands across all entries
        all_commands = [
            h.get("command", "")
            for entry in event_hooks
            for h in entry.get("hooks", [])
        ]
        if not any("agent_count_truth.sh" in cmd for cmd in all_commands):
            # Add to the first entry if it exists, otherwise create a new one
            if event_hooks:
                event_hooks[0].setdefault("hooks", []).append(HOOK_ENTRY)
            else:
                event_hooks.append({"hooks": [HOOK_ENTRY]})
            changed = True
            print(f"  [register] added agent_count_truth.sh to {event} in {settings_path}")
        else:
            print(f"  [register] agent_count_truth.sh already in {event} in {settings_path}")

    if changed:
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    return changed


def main() -> int:
    print("[register_agent_count_truth_hook] registering hook in settings.json files...")

    # Worktree settings.json
    worktree_settings = REPO_ROOT / ".claude" / "settings.json"
    _register(worktree_settings)

    # Main repo settings.json (the one the harness actually uses)
    main_settings = Path("/Users/shawnwilson/gludd/.claude/settings.json")
    if main_settings != worktree_settings.resolve():
        _register(main_settings)

    print("[register_agent_count_truth_hook] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
