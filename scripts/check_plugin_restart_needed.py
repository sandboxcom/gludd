#!/usr/bin/env python3
"""Detect when any plugin source file is newer than the opencode session start.

OpenCode compiles plugins once at startup.  Any edit to a plugin .ts file
(including impl/ sub-files imported by wrappers) after that point requires
a restart to take effect.  This script reads the session-start timestamp
from /tmp/gludd-session-start.json and compares it against the mtimes of
every source file under .opencode/plugin/.

Exit 0: all source files are older than or equal to session start (restart NOT needed).
Exit 1: at least one source file is newer than session start (restart NEEDED).
Exit 2: session-start file missing or unreadable (cannot determine — fail-safe).

Path overrides:
  GLUDD_PLUGIN_DIR   — source .ts directory (default: .opencode/plugin)
  GLUDD_SESSION_START_FILE — session state (default: /tmp/gludd-session-start.json)
"""

import json
import os
import sys
from pathlib import Path

PLUGIN_DIR = Path(
    os.environ.get(
        "GLUDD_PLUGIN_DIR",
        Path(__file__).resolve().parent.parent / ".opencode" / "plugin",
    )
)
SESSION_START_FILE = Path(os.environ.get("GLUDD_SESSION_START_FILE", "/tmp/gludd-session-start.json"))


def read_session_start_ms(session_path: Path) -> int | None:
    """Return session start timestamp in milliseconds, or None if unreadable."""
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
        return int(data.get("started_at", 0))
    except (OSError, json.JSONDecodeError, ValueError, KeyError):
        return None


def collect_ts_files(plugin_dir: Path) -> list[Path]:
    """Return every .ts file under the plugin directory."""
    if not plugin_dir.is_dir():
        return []
    return sorted(plugin_dir.rglob("*.ts"))


def find_newer_sources(
    plugin_dir: Path,
    session_start_ms: int,
) -> list[tuple[Path, float]]:
    """Return (path, mtime_seconds) for source files newer than session_start_ms.

    Compare file system mtime (seconds since epoch) against the session start
    (also converted to seconds).  A file whose mtime_seconds > session_start_seconds
    was modified AFTER the session began — a restart is needed for that change
    to take effect.
    """
    session_start_sec = session_start_ms / 1000.0
    newer: list[tuple[Path, float]] = []

    for src_path in collect_ts_files(plugin_dir):
        try:
            mtime_sec = src_path.stat().st_mtime
        except OSError:
            continue
        if mtime_sec > session_start_sec:
            newer.append((src_path, mtime_sec))

    return newer


def main(argv: list[str] | None = None) -> int:
    del argv

    plugin_dir = PLUGIN_DIR
    session_path = SESSION_START_FILE

    session_start_ms = read_session_start_ms(session_path)
    if session_start_ms is None or session_start_ms == 0:
        print(f"Cannot determine session start — {session_path} missing or invalid")
        print("UNKNOWN: assume restart NOT required (fail-safe)")
        return 0

    newer = find_newer_sources(plugin_dir, session_start_ms)

    total = len(collect_ts_files(plugin_dir))

    if not newer:
        print(f"=== PLUGIN RESTART CHECK: all {total} source file(s) are current ===")
        return 0

    session_start_sec = session_start_ms / 1000.0
    print("=== PLUGIN RESTART REQUIRED ===\n")
    print(f"Session started at epoch {session_start_sec:.0f}")
    print(f"The following {len(newer)}/{total} plugin source file(s) were modified AFTER session start:\n")
    for src_path, mtime_sec in newer:
        delta = int(mtime_sec - session_start_sec)
        print(f"  {src_path.relative_to(plugin_dir.parent)}  (+{delta}s)")

    print("\nThese changes will NOT take effect until opencode is restarted.")
    print("To activate:\n  1. Commit current work\n  2. Quit and re-launch opencode")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
