#!/usr/bin/env python3
"""AB021 — check that hot-reload modules are newer than their source plugins.

Each /tmp/gludd-hot-enforce-*.js must be newer than the corresponding
.opencode/plugin/enforce-*.ts source. Stale hot modules indicate the
hot-reload was never run or failed silently.

Exit non-zero if any hot module is stale or has warnings.
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
HOT_MODULE_PATTERN = re.compile(r"gludd-hot-enforce-([a-z][a-z0-9_-]*)\.js")


def find_hot_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for p in Path("/tmp").glob("gludd-hot-enforce-*.js"):
        m = HOT_MODULE_PATTERN.match(p.name)
        if m:
            modules[m.group(1)] = p
    return modules


def find_source_plugins() -> dict[str, Path]:
    plugins: dict[str, Path] = {}
    for p in PLUGIN_DIR.glob("enforce-*.ts"):
        name = p.stem.replace("enforce-", "")
        plugins[name] = p
    return plugins


def has_warnings(path: Path) -> bool:
    content = path.read_text(encoding="utf-8", errors="replace")
    warning_patterns = [
        "invalid JS",
        "Unexpected token",
        "already been declared",
        "failed to require",
        "is not defined",
    ]
    for pattern in warning_patterns:
        if pattern in content:
            return True
    return False


def main() -> int:
    hot_modules = find_hot_modules()
    sources = find_source_plugins()

    if not hot_modules:
        print("check-hot-module-freshness: no hot modules found in /tmp/ — run 'make hot-reload-plugins'")
        return 1

    stale_count = 0
    warning_count = 0

    for name, hot_path in sorted(hot_modules.items()):
        source_path = sources.get(name)
        if source_path is None:
            print(f"WARNING: hot module '{name}' has no corresponding source plugin")
            continue

        hot_mtime = hot_path.stat().st_mtime
        src_mtime = source_path.stat().st_mtime

        if hot_mtime < src_mtime:
            print(f"STALE: {hot_path.name} (hot: {hot_mtime:.0f}, source: {src_mtime:.0f}) — source is newer")
            stale_count += 1

        if has_warnings(hot_path):
            print(f"WARNING: {hot_path.name} contains JS warnings — must be regenerated")
            warning_count += 1

    # Check for source plugins with no hot module
    for name in sources:
        if name not in hot_modules:
            print(f"MISSING: no hot module for enforce-{name}.ts")

    if stale_count or warning_count:
        print(f"\ncheck-hot-module-freshness: {stale_count} stale, {warning_count} with warnings")
        return 1

    print(f"check-hot-module-freshness: {len(hot_modules)} hot modules fresh, no warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
