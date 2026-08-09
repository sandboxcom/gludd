#!/usr/bin/env python3
"""Remove stale gludd-owned temp directories/files from TMPDIR (~/tmp).

Each pattern is traced to a specific source file that calls ``mkdtemp``
or ``NamedTemporaryFile(delete=False)`` without subsequent cleanup.

Run as a pre-session hook or on-demand via ``make cleanup-stale-tmp``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CleanupPattern:
    prefix: str
    source_file: str
    source_line: int
    kind: str  # "dir" or "file"


# Each entry = one leak traced to its source.  Keep in sync with the
# actual mkdtemp / NamedTemporaryFile(delete=False) calls in src/.
PATTERNS: list[CleanupPattern] = [
    CleanupPattern("gl-runner-", "src/general_ludd/ansible/runner.py", 180, "dir"),
    CleanupPattern("gl-runner-iso-", "src/general_ludd/ansible/core_runner.py", 638, "dir"),
    CleanupPattern("gludd-tf-", "src/general_ludd/infra/deployment.py", 210, "dir"),
    CleanupPattern("gludd-collections-", "src/general_ludd/ansible/paths.py", 395, "dir"),
    CleanupPattern("gludd-render-", "src/general_ludd/renderers/runner.py", 223, "dir"),
    CleanupPattern("gludd-sandbox-", "src/general_ludd/sandbox/enforcer.py", 100, "dir"),
    CleanupPattern("gludd-llama-stderr-", "src/general_ludd/infra/local_inference.py", 295, "file"),
    CleanupPattern("gludd-qwen-", "e2e test model download", 0, "dir"),
    CleanupPattern("_MEI", "PyInstaller OneFile extraction", 0, "dir"),
    CleanupPattern("lsmt_", "src/general_ludd/storage/lsm_tree.py", 128, "dir"),
]


def _default_tmp() -> Path:
    return Path(os.environ.get("GLUDD_TMP_DIR", Path.home() / "tmp"))


def _stale_seconds(path: Path) -> float:
    try:
        stat = path.stat()
        mtime = stat.st_mtime
    except OSError:
        return 0.0
    return time.time() - (mtime if mtime > 0 else stat.st_ctime)


def _fmt_size(sz: int) -> str:
    if sz >= 1_073_741_824:
        return f"{sz / 1_073_741_824:.1f}G"
    if sz >= 1_048_576:
        return f"{sz / 1_048_576:.1f}M"
    if sz >= 1_024:
        return f"{sz / 1_024:.1f}K"
    return f"{sz}B"


def cleanup_tmp(
    tmp_dir: Path | None = None,
    *,
    min_age_seconds: int = 3600,
    dry_run: bool = True,
    patterns: list[CleanupPattern] | None = None,
) -> tuple[int, int]:
    """Remove stale gludd temp entries.  Returns (entries_removed, bytes_freed)."""
    patterns = patterns or PATTERNS
    tmp = tmp_dir or _default_tmp()
    removed = 0
    bytes_freed = 0

    if not tmp.is_dir():
        print(f"cleanup-stale-tmp: {tmp} does not exist or is not a directory")
        return 0, 0

    for pat in patterns:
        glob_pattern = f"{pat.prefix}*"
        for entry in sorted(tmp.glob(glob_pattern)):
            try:
                size = 0
                if entry.is_dir():
                    size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                elif entry.is_file():
                    size = entry.stat().st_size
            except OSError:
                continue

            age = _stale_seconds(entry)
            action = "would remove" if dry_run else "removing"
            print(
                f"  {action} {pat.prefix}* "
                f"entry={entry.name} "
                f"age_s={age:.0f} "
                f"size={_fmt_size(size)} "
                f"source={pat.source_file}:{pat.source_line}"
            )

            if dry_run:
                removed += 1
                bytes_freed += size
                continue

            if age < min_age_seconds:
                print(f"    SKIP: age {age:.0f}s < min_age {min_age_seconds}s")
                continue

            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed += 1
                bytes_freed += size
            except OSError as exc:
                print(f"    ERROR: {exc}")

    return removed, bytes_freed


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually remove (default: dry-run)",
    )
    parser.add_argument(
        "--min-age-seconds",
        type=int,
        default=3600,
        help="Only remove entries older than this many seconds (default: 3600)",
    )
    parser.add_argument(
        "--tmp-dir",
        type=str,
        default=str(_default_tmp()),
        help=f"Override temp directory (default: {_default_tmp()})",
    )
    args = parser.parse_args()
    tmp = Path(args.tmp_dir)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"cleanup-stale-tmp: mode={mode} tmp={tmp} min_age_s={args.min_age_seconds}")
    removed, freed = cleanup_tmp(
        tmp_dir=tmp,
        dry_run=not args.apply,
        min_age_seconds=args.min_age_seconds,
    )
    action = "Would remove" if not args.apply else "Removed"
    print(f"{action} {removed} entries ({_fmt_size(freed)} freed)")


if __name__ == "__main__":
    _main()
