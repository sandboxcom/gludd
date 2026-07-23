#!/usr/bin/env python3
"""Fix opencode startup crash by resolving global config conflicts.

Root cause: ~/.config/opencode/ contains an OLD enforce-multitask.ts that
conflicts with the project's version, plus a permission:{*:allow} override
that defeats the project's make-only bash enforcement. This script:

1. Backs up the global enforce-multitask.ts (does NOT delete — user may want it)
2. Removes the plugin reference from global opencode.json + opencode.jsonc
3. Removes the *:allow permission override (lets project config decide)
4. Truncates the 306MB opencode.log and plugin-load spam log
5. Reports what it changed

This is CONSERVATIVE: it never deletes files, only backs up + edits config.
The user can restore by renaming the .bak files.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

GLOBAL_DIR = Path(os.path.expanduser("~/.config/opencode"))


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"  backed up {path.name} -> {bak.name}")
    else:
        print(f"  backup exists: {bak.name}")
    return bak


def fix_plugin_list(config: dict) -> bool:
    """Remove enforce-multitask.ts from global plugin list. Returns True if changed."""
    plugins = config.get("plugin", [])
    if not plugins:
        return False
    original_len = len(plugins)
    filtered = [p for p in plugins if "enforce-multitask" not in str(p)]
    if len(filtered) == original_len:
        return False
    config["plugin"] = filtered
    print(f"  removed enforce-multitask from plugin list ({original_len} -> {len(filtered)})")
    return True


def fix_permissions(config: dict) -> bool:
    """Remove the *:allow override so project config controls permissions."""
    perm = config.get("permission")
    if not isinstance(perm, dict):
        return False
    if "*" not in perm:
        return False
    del perm["*"]
    print("  removed permission {'*': 'allow'} override (project config now controls)")
    return True


def fix_config_file(path: Path) -> bool:
    """Fix one config file. Returns True if changes were made."""
    if not path.exists():
        print(f"  {path.name}: not found, skipping")
        return False
    try:
        raw = path.read_text()
        config = json.loads(raw)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  {path.name}: parse error ({e}), skipping")
        return False

    changed = False
    changed |= fix_plugin_list(config)
    changed |= fix_permissions(config)

    if changed:
        backup(path)
        path.write_text(json.dumps(config, indent=2) + "\n")
        print(f"  wrote updated {path.name}")
    else:
        print(f"  {path.name}: no changes needed")
    return changed


def truncate_log(path: Path, label: str) -> None:
    """Truncate a log file to 0 bytes if it exists and is large."""
    if not path.exists():
        return
    size = path.stat().st_size
    if size > 1_000_000:  # only truncate if > 1MB
        backup(path)
        path.write_text("")
        print(f"  truncated {label} ({size // 1_000_000}MB -> 0)")
    else:
        print(f"  {label}: {size // 1000}KB, leaving as-is")


def main() -> int:
    if not GLOBAL_DIR.exists():
        print(f"ERROR: {GLOBAL_DIR} does not exist")
        return 1

    print(f"=== Fixing opencode global config at {GLOBAL_DIR} ===\n")

    # 1. Back up the global enforce-multitask.ts (don't delete)
    em = GLOBAL_DIR / "enforce-multitask.ts"
    if em.exists():
        print("[1/4] Backing up global enforce-multitask.ts:")
        backup(em)
        print("  (file left in place; plugin reference removed from config below)")
    else:
        print("[1/4] No global enforce-multitask.ts found")
    print()

    # 2. Fix opencode.json
    print("[2/4] Fixing opencode.json:")
    fix_config_file(GLOBAL_DIR / "opencode.json")
    print()

    # 3. Fix opencode.jsonc
    print("[3/4] Fixing opencode.jsonc:")
    fix_config_file(GLOBAL_DIR / "opencode.jsonc")
    print()

    # 4. Truncate massive logs
    print("[4/4] Truncating logs:")
    truncate_log(Path.home() / ".local/share/opencode/log/opencode.log", "opencode.log")
    truncate_log(Path("/tmp/gludd-plugin-loaded.log"), "plugin-load spam log")
    print()

    print("=== Done ===")
    print("To verify: restart opencode and run `opencode debug info`")
    print("To restore: rename .bak files back in ~/.config/opencode/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
