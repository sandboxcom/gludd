"""Verify .opencode.orig/ backup is not stale vs .opencode/.

Checks:
  1. File listing: .opencode.orig/ must contain all .ts files present in
     .opencode/plugin/ (excludes node_modules/).
  2. Export parity: plugin/shared.ts exports must match between live and backup.
     A backup missing exports is stale — running restore-opencode would
     overwrite live code with code missing those exports.

Exit 0 = backup is current. Exit 1 = backup is stale or incomplete.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

EXPORT_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?"
    r"(?:async\s+)?(?:function|const|let|var|interface|type|class|enum)\s+"
    r"(\w+)",
    re.MULTILINE,
)


def _extract_exports(filepath: Path) -> set[str]:
    """Return set of exported names from a TypeScript file."""
    names: set[str] = set()
    try:
        text = filepath.read_text()
    except (OSError, FileNotFoundError):
        return names
    for match in EXPORT_RE.finditer(text):
        names.add(match.group(1))
    return names


def _list_plugin_ts_files(root: Path) -> set[str]:
    """Return relative paths (from root) of all .ts files under plugin/."""
    plugin = root / "plugin"
    if not plugin.is_dir():
        return set()
    return {
        str(p.relative_to(root))
        for p in plugin.rglob("*.ts")
        if p.is_file() and "node_modules" not in p.parts
    }


def verify(opencode: Path, backup: Path) -> tuple[bool, list[str]]:
    """Return (ok, messages). ok=True means backup is current."""
    msgs: list[str] = []
    if not backup.is_dir():
        msgs.append("ERROR: .opencode.orig/ does not exist")
        return False, msgs

    live_files = _list_plugin_ts_files(opencode)
    backup_files = _list_plugin_ts_files(backup)

    missing = live_files - backup_files
    if missing:
        for f in sorted(missing):
            msgs.append(f"  MISSING: {f} in .opencode.orig/")
        msgs.append(
            "  WARNING: .opencode.orig/ is missing files present in .opencode/."
            " Run 'make backup-opencode' to refresh."
        )

    extra = backup_files - live_files
    for f in sorted(extra):
        msgs.append(f"  NOTE: extra file in backup (may be stale): {f}")

    live_shared = opencode / "plugin" / "shared.ts"
    backup_shared = backup / "plugin" / "shared.ts"
    if live_shared.is_file() and backup_shared.is_file():
        live_exports = _extract_exports(live_shared)
        backup_exports = _extract_exports(backup_shared)
        missing_exports = live_exports - backup_exports
        if missing_exports:
            for name in sorted(missing_exports):
                msgs.append(
                    f"  MISSING EXPORT from backup shared.ts: '{name}'"
                )
            msgs.append(
                "  WARNING: backup shared.ts is missing exports the live"
                " shared.ts has. Running 'make restore-opencode' would"
                " overwrite live code with stale code."
            )
        extra_exports = backup_exports - live_exports
        if extra_exports:
            for name in sorted(extra_exports):
                msgs.append(
                    f"  NOTE: backup shared.ts has extra export not in live: '{name}'"
                )
    elif live_shared.is_file() and not backup_shared.is_file():
        msgs.append("  MISSING: plugin/shared.ts from .opencode.orig/")

    ok = len(missing) == 0 and not any(
        "MISSING EXPORT" in m for m in msgs
    )
    return ok, msgs


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    opencode = root / ".opencode"
    backup = root / ".opencode.orig"
    ok, msgs = verify(opencode, backup)
    for m in msgs:
        print(m, file=sys.stderr if "WARNING" in m else sys.stdout)
    if not ok:
        print("\n  Backup verification FAILED — backup is stale.", file=sys.stderr)
        return 1
    print("  .opencode.orig/ backup is current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
