#!/usr/bin/env python3
"""Check .opencode/plugin/*.ts hashes against stored manifest.

Computes SHA-256 hashes of all .opencode/plugin/*.ts files, compares against
.opencode/plugin-hashes.json. If hashes differ (plugins were modified since the
last manifest write), writes a disengage signal to /tmp/gludd-watchdog-disengage.json
with disengage_until = now + 3600 (1 hour, in milliseconds) so the running opencode
instance stops enforcing with stale (now-replaced) plugin code.

Usage:
    scripts/check_plugin_hashes.py                     — check, write disengage if changed
    scripts/check_plugin_hashes.py --write-manifest     — update manifest (no disengage check)
    scripts/check_plugin_hashes.py --quiet              — suppress stdout, exit code only
    scripts/check_plugin_hashes.py --max-age SECONDS    — auto-write manifest if older than SECONDS (default: 0/never)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

PLUGIN_DIR = Path(os.environ.get("GLUDD_PLUGIN_DIR", ".opencode/plugin"))
MANIFEST_PATH = Path(os.environ.get("GLUDD_PLUGIN_MANIFEST", ".opencode/plugin-hashes.json"))
DISENGAGE_FILE = Path("/tmp/gludd-watchdog-disengage.json")
BLOCK_COUNTER_FILE = Path("/tmp/gludd-block-counter.json")

DISENGAGE_DURATION_SECONDS = int(
    os.environ.get("GLUDD_PLUGIN_DISENGAGE_DURATION", "3600")
)  # default 1 hour


def _now_ms() -> int:
    return int(time.time() * 1000)


def _scan_ts_dir(directory: Path, result: dict[str, str], prefix: str = "") -> None:
    """Scan a directory for .ts files and add their hashes to result."""
    if not directory.is_dir():
        return
    for f in sorted(directory.glob("*.ts")):
        try:
            content = f.read_bytes()
            key = f"{prefix}{f.name}"
            result[key] = hashlib.sha256(content).hexdigest()
        except OSError:
            continue


def compute_hashes(plugin_dir: Path | None = None) -> dict[str, str]:
    """Return {filename: sha256_hex} for all .ts files in plugin_dir and plugins_dir."""
    pd = Path(plugin_dir) if plugin_dir else PLUGIN_DIR
    if not pd.is_dir():
        return {}
    result: dict[str, str] = {}
    _scan_ts_dir(pd, result)
    plugins_dir = pd.parent / "plugins"
    _scan_ts_dir(plugins_dir, result, prefix="plugins/")
    return result


def read_manifest(manifest_path: Path | None = None) -> dict[str, str]:
    mp = Path(manifest_path) if manifest_path else MANIFEST_PATH
    if not mp.is_file():
        return {}
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except (json.JSONDecodeError, ValueError):
        return {}


def write_manifest(
    hashes: dict[str, str],
    manifest_path: Path | None = None,
) -> Path:
    mp = Path(manifest_path) if manifest_path else MANIFEST_PATH
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return mp


def write_disengage(
    duration_seconds: int = 3600,
    reason: str = "",
    disengage_path: Path | None = None,
) -> Path:
    dp = Path(disengage_path) if disengage_path else DISENGAGE_FILE
    disengage_until = _now_ms() + duration_seconds * 1000
    dp.write_text(
        json.dumps({
            "disengage_until": disengage_until,
            "disengage_until_epoch_ms": disengage_until,
            "reason": reason,
            "ts": time.time(),
        }),
        encoding="utf-8",
    )
    return dp


def write_block_counter_disengage(
    block_counter_path: Path | None = None,
) -> Path:
    bp = Path(block_counter_path) if block_counter_path else BLOCK_COUNTER_FILE
    bp.write_text(
        json.dumps({
            "consecutiveBlocks": 0,
            "totalBlocks": 0,
            "lastBlockTs": 0,
            "disengageUntil": 9999999999999,
        }),
        encoding="utf-8",
    )
    return bp


def check(
    plugin_dir: Path | None = None,
    manifest_path: Path | None = None,
    disengage_path: Path | None = None,
    block_counter_path: Path | None = None,
    quiet: bool = False,
    max_age_seconds: int = 0,
) -> int:
    """Main check: compare hashes, write disengage if changed.

    Returns 0 if hashes match (no disengage), 1 if hashes differ (disengage written).
    """
    current = compute_hashes(plugin_dir)
    stored = read_manifest(manifest_path)

    dp = Path(disengage_path) if disengage_path else DISENGAGE_FILE
    bp = Path(block_counter_path) if block_counter_path else BLOCK_COUNTER_FILE

    if not current:
        if not quiet:
            print("no plugin .ts files found — nothing to check", file=sys.stderr)
        return 0

    # Auto-generate manifest if missing or too old
    mp = Path(manifest_path) if manifest_path else MANIFEST_PATH
    if not stored:
        if not quiet:
            print(f"no stored manifest at {mp} — writing initial manifest", file=sys.stderr)
        write_manifest(current, manifest_path)
        return 0

    if max_age_seconds > 0 and mp.is_file():
        mtime = mp.stat().st_mtime
        age = time.time() - mtime
        if age > max_age_seconds:
            if not quiet:
                print(f"manifest is {age:.0f}s old (> {max_age_seconds}s) — refreshing", file=sys.stderr)
            write_manifest(current, manifest_path)
            return 0

    if current == stored:
        if not quiet:
            print("plugin hashes match — no disengage needed")
        return 0

    # Hashes differ — plugins were modified
    new_files = set(current) - set(stored)
    removed_files = set(stored) - set(current)
    changed_files = {
        f for f in set(current) & set(stored)
        if current[f] != stored[f]
    }

    details: list[str] = []
    if new_files:
        details.append(f"new: {', '.join(sorted(new_files))}")
    if removed_files:
        details.append(f"removed: {', '.join(sorted(removed_files))}")
    if changed_files:
        details.append(f"changed: {', '.join(sorted(changed_files))}")

    reason = " | ".join(details)

    write_disengage(DISENGAGE_DURATION_SECONDS, reason, dp)
    write_block_counter_disengage(bp)

    if not quiet:
        print(f"DISENGAGE: plugin hashes changed — {reason}")
        print(f"  disengage until: {_now_ms() + DISENGAGE_DURATION_SECONDS * 1000} ms")
        print(f"  disengage file: {dp}")
        print(f"  block counter: {bp}")

    return 1


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if "--write-manifest" in argv:
        current = compute_hashes()
        mp = write_manifest(current)
        print(f"manifest written: {mp} ({len(current)} files)")
        return 0

    quiet = "--quiet" in argv

    max_age = 0
    for i, arg in enumerate(argv):
        if arg == "--max-age" and i + 1 < len(argv):
            try:
                max_age = int(argv[i + 1])
            except ValueError:
                pass

    return check(quiet=quiet, max_age_seconds=max_age)


if __name__ == "__main__":
    raise SystemExit(main())
